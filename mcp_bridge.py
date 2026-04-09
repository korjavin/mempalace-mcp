"""
MemPalace MCP Bridge — SSE transport layer
Exposes MemPalace stdio MCP server over HTTP/SSE so Claude.ai and
other remote AI clients can connect via the MCP integration URL.

Port : 7891  (expose via Cloudflare Tunnel)
Auth : Bearer token (managed via admin portal)
"""

import asyncio
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MCP] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PALACE_PATH = os.environ.get("PALACE_PATH", "/palace/data")
DB_PATH     = os.environ.get("DB_PATH",     "/palace/tokens.db")
PORT        = int(os.environ.get("MCP_PORT", "7891"))

# ── Database helpers ──────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id         TEXT PRIMARY KEY,
                label      TEXT NOT NULL,
                token      TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_used  TEXT,
                active     INTEGER DEFAULT 1
            )
        """)
        db.commit()

def verify_token(raw: str) -> bool:
    """Return True and stamp last_used if token is valid and active."""
    with _db() as db:
        row = db.execute(
            "SELECT id FROM tokens WHERE token=? AND active=1", (raw,)
        ).fetchone()
        if row:
            db.execute(
                "UPDATE tokens SET last_used=? WHERE token=?",
                (datetime.now(timezone.utc).isoformat(), raw),
            )
            db.commit()
            return True
    return False

# ── Session store (sessionId → subprocess) ────────────────────────────────────

_sessions: dict[str, asyncio.subprocess.Process] = {}

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="MemPalace MCP Bridge", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def startup():
    init_db()
    logger.info(f"Palace path : {PALACE_PATH}")
    logger.info(f"DB path     : {DB_PATH}")
    logger.info(f"Listening   : 0.0.0.0:{PORT}")


def _auth(request: Request) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return verify_token(header[7:].strip())


@app.get("/health")
async def health():
    return {"ok": True, "sessions": len(_sessions)}


@app.get("/sse")
async def sse(request: Request):
    """
    MCP SSE endpoint.
    1. Authenticates the Bearer token.
    2. Spawns a MemPalace stdio MCP server subprocess.
    3. Streams subprocess stdout → SSE (server→client).
    4. Provides /messages?sessionId=X for client→server messages.
    """
    if not _auth(request):
        return Response("Forbidden", status_code=403)

    session_id = str(uuid.uuid4())

    proc = await asyncio.create_subprocess_exec(
        "python3", "-m", "mempalace.mcp_server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "MEMPALACE_PALACE_PATH": PALACE_PATH},
    )
    _sessions[session_id] = proc
    logger.info(f"Session opened  {session_id[:8]}… (pid {proc.pid})")

    async def stream():
        try:
            # MCP SSE protocol: first event announces the POST endpoint
            yield f"event: endpoint\ndata: /messages?sessionId={session_id}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), 30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if not line:
                    break
                payload = line.decode().strip()
                if payload:
                    yield f"data: {payload}\n\n"
        finally:
            _sessions.pop(session_id, None)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            logger.info(f"Session closed  {session_id[:8]}…")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@app.post("/messages")
async def messages(request: Request):
    """Relay client→server JSON-RPC into the subprocess stdin."""
    session_id = request.query_params.get("sessionId", "")
    proc = _sessions.get(session_id)
    if not proc:
        return Response("Session not found", status_code=404)

    body = await request.body()
    proc.stdin.write(body + b"\n")
    await proc.stdin.drain()
    return Response(status_code=202)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
