"""
MemPalace MCP Bridge — SSE transport layer
Exposes MemPalace stdio MCP server over HTTP/SSE so Claude.ai and
other remote AI clients can connect via the MCP integration URL.

Port : 7891  (expose via Cloudflare Tunnel)
Auth : OAuth 2.1 with PKCE (for Claude.ai) + Bearer token (direct)
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MCP] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PALACE_PATH    = os.environ.get("PALACE_PATH", "/palace/data")
DB_PATH        = os.environ.get("DB_PATH",     "/palace/tokens.db")
PORT           = int(os.environ.get("MCP_PORT", "7891"))
ADMIN_USER     = os.environ.get("ADMIN_USER",     "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
PUBLIC_URL     = os.environ.get("PUBLIC_URL",      "")

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
        db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id     TEXT PRIMARY KEY,
                client_name   TEXT NOT NULL,
                redirect_uris TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_auth_codes (
                code                  TEXT PRIMARY KEY,
                client_id             TEXT NOT NULL,
                redirect_uri          TEXT NOT NULL,
                code_challenge        TEXT NOT NULL,
                code_challenge_method TEXT NOT NULL DEFAULT 'S256',
                token_id              TEXT NOT NULL,
                expires_at            TEXT NOT NULL,
                used                  INTEGER DEFAULT 0,
                created_at            TEXT NOT NULL
            )
        """)
        # Pre-register Claude.ai as an OAuth client
        db.execute("""
            INSERT OR IGNORE INTO oauth_clients (client_id, client_name, redirect_uris, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            "claude-ai",
            "Claude.ai",
            json.dumps(["https://claude.ai/api/mcp/auth_callback"]),
            datetime.now(timezone.utc).isoformat(),
        ))
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

# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _base_url(request: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    return f"{proto}://{host}"

def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)

def _get_client(client_id: str) -> dict | None:
    with _db() as db:
        row = db.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        if row:
            d = dict(row)
            d["redirect_uris"] = json.loads(d["redirect_uris"])
            return d
    return None

def _cleanup_expired_codes():
    with _db() as db:
        db.execute(
            "DELETE FROM oauth_auth_codes WHERE expires_at < ?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        db.commit()

# ── Session store (sessionId → subprocess) ────────────────────────────────────

_sessions: dict[str, asyncio.subprocess.Process] = {}

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="MemPalace MCP Bridge", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def startup():
    init_db()
    _cleanup_expired_codes()
    logger.info(f"Palace path : {PALACE_PATH}")
    logger.info(f"DB path     : {DB_PATH}")
    logger.info(f"Listening   : 0.0.0.0:{PORT}")


def _auth(request: Request) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return verify_token(header[7:].strip())


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"ok": True, "sessions": len(_sessions)}


# ── OAuth 2.1 Discovery ──────────────────────────────────────────────────────

@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource(request: Request):
    base = _base_url(request)
    return JSONResponse({
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
    })

@app.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server(request: Request):
    base = _base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


# ── OAuth 2.1 Dynamic Client Registration ────────────────────────────────────

@app.post("/register")
async def register_client(request: Request):
    body = await request.json()
    redirect_uris = body.get("redirect_uris", [])
    if not redirect_uris:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    client_id = str(uuid.uuid4())
    client_name = body.get("client_name", "Unknown Client")
    now = datetime.now(timezone.utc).isoformat()

    with _db() as db:
        db.execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) VALUES (?,?,?,?)",
            (client_id, client_name, json.dumps(redirect_uris), now),
        )
        db.commit()

    logger.info(f"OAuth client registered: {client_name} ({client_id})")
    return JSONResponse({
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }, status_code=201)


# ── OAuth 2.1 Authorization ──────────────────────────────────────────────────

CONSENT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Authorize — MemPalace</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700&family=JetBrains+Mono&display=swap');
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#0a0a0f;color:#e0e0e0;font-family:'Syne',sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100vh;padding:1rem}
  .card{background:#12121a;border:1px solid #1e1e2e;border-radius:16px;
        padding:2.5rem;max-width:420px;width:100%}
  .logo{font-size:1.5rem;font-weight:700;color:#a78bfa;margin-bottom:.25rem}
  .subtitle{font-size:.85rem;color:#666;margin-bottom:2rem}
  .client{background:#1a1a2e;border:1px solid #2a2a3e;border-radius:10px;
          padding:1rem;margin-bottom:1.5rem;text-align:center}
  .client-name{font-size:1.1rem;font-weight:600;color:#c4b5fd}
  .client-desc{font-size:.8rem;color:#888;margin-top:.25rem}
  label{display:block;font-size:.85rem;color:#999;margin-bottom:.4rem}
  input[type=password]{width:100%;padding:.7rem 1rem;background:#0a0a0f;
        border:1px solid #2a2a3e;border-radius:8px;color:#e0e0e0;
        font-family:'JetBrains Mono',monospace;font-size:.9rem;outline:none}
  input[type=password]:focus{border-color:#a78bfa}
  .error{color:#f87171;font-size:.8rem;margin-top:.4rem;margin-bottom:.5rem}
  .actions{display:flex;gap:.75rem;margin-top:1.5rem}
  button{flex:1;padding:.7rem;border:none;border-radius:8px;font-family:'Syne',sans-serif;
         font-size:.9rem;font-weight:600;cursor:pointer;transition:background .2s}
  .approve{background:#7c3aed;color:#fff}
  .approve:hover{background:#6d28d9}
  .deny{background:#1e1e2e;color:#999}
  .deny:hover{background:#2a2a3e;color:#e0e0e0}
</style>
</head>
<body>
<div class="card">
  <div class="logo">MemPalace</div>
  <div class="subtitle">Authorization Request</div>
  <div class="client">
    <div class="client-name">{{CLIENT_NAME}}</div>
    <div class="client-desc">wants access to your MemPalace memory tools</div>
  </div>
  <form method="POST" action="/authorize">
    <input type="hidden" name="client_id" value="{{CLIENT_ID}}">
    <input type="hidden" name="redirect_uri" value="{{REDIRECT_URI}}">
    <input type="hidden" name="state" value="{{STATE}}">
    <input type="hidden" name="code_challenge" value="{{CODE_CHALLENGE}}">
    <input type="hidden" name="code_challenge_method" value="{{CODE_CHALLENGE_METHOD}}">
    <input type="hidden" name="response_type" value="code">
    <label for="password">Admin Password</label>
    <input type="password" id="password" name="password" required autofocus>
    {{ERROR}}
    <div class="actions">
      <button type="button" class="deny" onclick="denyAccess()">Deny</button>
      <button type="submit" class="approve">Approve</button>
    </div>
  </form>
</div>
<script>
function denyAccess(){
  const uri = '{{REDIRECT_URI}}';
  const state = '{{STATE}}';
  window.location = uri + '?error=access_denied&state=' + encodeURIComponent(state);
}
</script>
</body>
</html>"""

def _render_consent(client_id, client_name, redirect_uri, state,
                    code_challenge, code_challenge_method, error=""):
    error_html = f'<div class="error">{error}</div>' if error else ""
    html = (CONSENT_HTML
        .replace("{{CLIENT_NAME}}", client_name)
        .replace("{{CLIENT_ID}}", client_id)
        .replace("{{REDIRECT_URI}}", redirect_uri)
        .replace("{{STATE}}", state)
        .replace("{{CODE_CHALLENGE}}", code_challenge)
        .replace("{{CODE_CHALLENGE_METHOD}}", code_challenge_method)
        .replace("{{ERROR}}", error_html))
    return HTMLResponse(html)


@app.get("/authorize")
async def authorize_get(request: Request):
    params = request.query_params
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    response_type = params.get("response_type", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "")
    state = params.get("state", "")

    # Validate client
    client = _get_client(client_id)
    if not client:
        return JSONResponse({"error": "invalid_client"}, status_code=400)

    # Validate redirect URI
    if redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    # Validate required OAuth params
    if response_type != "code":
        return RedirectResponse(f"{redirect_uri}?error=unsupported_response_type&state={state}")
    if code_challenge_method != "S256" or not code_challenge:
        return RedirectResponse(f"{redirect_uri}?error=invalid_request&state={state}")

    return _render_consent(
        client_id, client["client_name"], redirect_uri, state,
        code_challenge, code_challenge_method,
    )


@app.post("/authorize")
async def authorize_post(request: Request):
    form = await request.form()
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    state = form.get("state", "")
    code_challenge = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "S256")
    password = form.get("password", "")

    client = _get_client(client_id)
    if not client or redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_client"}, status_code=400)

    # Verify admin password
    if not secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        return _render_consent(
            client_id, client["client_name"], redirect_uri, state,
            code_challenge, code_challenge_method,
            error="Incorrect password",
        )

    # Create a new token for this OAuth grant
    now = datetime.now(timezone.utc)
    token_id = str(uuid.uuid4())
    raw_token = f"mp_{secrets.token_urlsafe(32)}"
    auth_code = f"oac_{secrets.token_urlsafe(32)}"

    with _db() as db:
        db.execute(
            "INSERT INTO tokens (id, label, token, created_at, active) VALUES (?,?,?,?,1)",
            (token_id, f"oauth:{client['client_name']}", raw_token, now.isoformat()),
        )
        db.execute(
            "INSERT INTO oauth_auth_codes "
            "(code, client_id, redirect_uri, code_challenge, code_challenge_method, "
            "token_id, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (auth_code, client_id, redirect_uri, code_challenge, code_challenge_method,
             token_id, (now + timedelta(minutes=10)).isoformat(), now.isoformat()),
        )
        db.commit()

    logger.info(f"OAuth code issued for client {client_id}")
    return RedirectResponse(
        f"{redirect_uri}?{urlencode({'code': auth_code, 'state': state})}",
        status_code=302,
    )


# ── OAuth 2.1 Token Exchange ─────────────────────────────────────────────────

@app.post("/token")
async def token_exchange(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type", "")
    code = form.get("code", "")
    redirect_uri = form.get("redirect_uri", "")
    client_id = form.get("client_id", "")
    code_verifier = form.get("code_verifier", "")

    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    with _db() as db:
        row = db.execute("SELECT * FROM oauth_auth_codes WHERE code=?", (code,)).fetchone()
        if not row:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        row = dict(row)

        # Validate code
        if row["used"]:
            # Possible token theft — revoke the associated token
            db.execute("UPDATE tokens SET active=0 WHERE id=?", (row["token_id"],))
            db.commit()
            logger.warning(f"OAuth code replay detected, revoked token {row['token_id']}")
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return JSONResponse({"error": "invalid_grant", "error_description": "code expired"}, status_code=400)

        if row["client_id"] != client_id:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        if row["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        # PKCE verification
        if not _verify_pkce(code_verifier, row["code_challenge"]):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

        # Mark code as used
        db.execute("UPDATE oauth_auth_codes SET used=1 WHERE code=?", (code,))
        db.commit()

        # Fetch the token
        token_row = db.execute("SELECT token FROM tokens WHERE id=?", (row["token_id"],)).fetchone()
        if not token_row:
            return JSONResponse({"error": "server_error"}, status_code=500)

    logger.info(f"OAuth token exchanged for client {client_id}")
    return JSONResponse({
        "access_token": token_row["token"],
        "token_type": "Bearer",
    })


# ── MCP SSE Endpoint ─────────────────────────────────────────────────────────

@app.get("/sse")
async def sse(request: Request):
    """
    MCP SSE endpoint.
    1. Authenticates the Bearer token (returns 401 with discovery if missing).
    2. Spawns a MemPalace stdio MCP server subprocess.
    3. Streams subprocess stdout → SSE (server→client).
    4. Provides /messages?sessionId=X for client→server messages.
    """
    if not _auth(request):
        base = _base_url(request)
        return Response(
            "Unauthorized",
            status_code=401,
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"',
            },
        )

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
