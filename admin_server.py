"""
MemPalace Admin Server
Manages Bearer tokens used by the MCP bridge.
Serves the admin UI and a small REST API.

Port : 7892  (internal network only — DO NOT add to Cloudflare Tunnel)
Auth : HTTP Basic (ADMIN_USER / ADMIN_PASSWORD env vars)
"""

import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

DB_PATH        = os.environ.get("DB_PATH",        "/palace/tokens.db")
ADMIN_USER     = os.environ.get("ADMIN_USER",     "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
PORT           = int(os.environ.get("ADMIN_PORT", "7892"))

app      = FastAPI(title="MemPalace Admin", docs_url=None, redoc_url=None)
security = HTTPBasic()

# ── DB ────────────────────────────────────────────────────────────────────────

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

# ── Auth ──────────────────────────────────────────────────────────────────────

def require_admin(creds: HTTPBasicCredentials = Depends(security)):
    ok = (
        secrets.compare_digest(creds.username.encode(), ADMIN_USER.encode()) and
        secrets.compare_digest(creds.password.encode(), ADMIN_PASSWORD.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

# ── Models ────────────────────────────────────────────────────────────────────

class TokenCreate(BaseModel):
    label: str

# ── Routes ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
async def ui(_: None = Depends(require_admin)):
    path = os.path.join(os.path.dirname(__file__), "static", "admin.html")
    with open(path, encoding="utf-8") as f:
        return f.read()

@app.get("/api/tokens")
async def list_tokens(_: None = Depends(require_admin)):
    with _db() as db:
        rows = db.execute(
            "SELECT id, label, token, created_at, last_used, active "
            "FROM tokens ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/tokens", status_code=201)
async def create_token(body: TokenCreate, _: None = Depends(require_admin)):
    token_id  = str(uuid.uuid4())
    raw_token = f"mp_{secrets.token_urlsafe(32)}"
    created   = datetime.now(timezone.utc).isoformat()
    with _db() as db:
        db.execute(
            "INSERT INTO tokens (id, label, token, created_at, active) VALUES (?,?,?,?,1)",
            (token_id, body.label.strip(), raw_token, created),
        )
        db.commit()
    # Return the raw token — this is the ONLY time it is shown in full
    return {
        "id":         token_id,
        "label":      body.label,
        "token":      raw_token,
        "created_at": created,
        "active":     1,
    }

@app.patch("/api/tokens/{token_id}/revoke")
async def revoke_token(token_id: str, _: None = Depends(require_admin)):
    with _db() as db:
        db.execute("UPDATE tokens SET active=0 WHERE id=?", (token_id,))
        db.commit()
    return {"revoked": True}

@app.patch("/api/tokens/{token_id}/activate")
async def activate_token(token_id: str, _: None = Depends(require_admin)):
    with _db() as db:
        db.execute("UPDATE tokens SET active=1 WHERE id=?", (token_id,))
        db.commit()
    return {"activated": True}

@app.delete("/api/tokens/{token_id}")
async def delete_token(token_id: str, _: None = Depends(require_admin)):
    with _db() as db:
        db.execute("DELETE FROM tokens WHERE id=?", (token_id,))
        db.commit()
    return {"deleted": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
