# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MemPalace Bridge is a Python HTTP/SSE bridge that exposes a local MemPalace MCP server to remote AI clients (Claude.ai, Cursor, ChatGPT, n8n). It proxies the stdio-based MCP protocol over HTTP using Server-Sent Events, secured with bearer tokens stored in SQLite.

## Commands

```bash
# Full automated deploy (first-time or redeploy)
./deploy.sh

# Rebuild a single service
docker compose up -d --build mcp
docker compose up -d --build admin

# View logs
docker compose logs -f mcp
docker compose logs -f admin

# Stop services
docker compose down

# Backup data
tar -czf mempalace-backup-$(date +%Y%m%d).tar.gz /opt/mempalace/
```

`deploy.sh` handles: data directory creation, .env generation with random password, image build (multi-stage), palace init, and service startup. Safe to re-run.

There are no tests, linters, or build scripts beyond Docker.

## Architecture

Two FastAPI services sharing a SQLite database and ChromaDB volume:

- **mcp_bridge.py** (port 7891) — SSE transport layer exposed via Cloudflare Tunnel
  - `GET /sse` — Opens SSE stream, validates Bearer token, spawns `mempalace.mcp_server` subprocess
  - `POST /messages?sessionId=X` — Relays JSON-RPC messages to subprocess stdin
  - `GET /health` — Health check
  - Each client session gets an isolated subprocess; sessions tracked in-memory (`_sessions` dict)

- **admin_server.py** (port 7892) — Token management API, internal network only
  - CRUD REST endpoints under `/api/tokens` with HTTP Basic auth
  - Serves `static/admin.html` — self-contained admin UI (dark theme, token lifecycle management)
  - Tokens are `mp_<32-char-urlsafe-random>`, stored in SQLite with active/revoked state

**Data flow:** Client → Cloudflare Tunnel → `/sse` (Bearer auth) → subprocess stdin/stdout → SSE events back to client

**Shared state:** Both services read/write `/palace/tokens.db` (SQLite). ChromaDB data lives at `/palace/data`. Host volume is `/opt/mempalace`.

## Configuration

Environment variables via `.env` (see `.env.example`):
- `ADMIN_USER` / `ADMIN_PASSWORD` — HTTP Basic auth for admin portal
- `PALACE_PATH` — ChromaDB data directory (default: `/palace/data`)
- `DB_PATH` — SQLite token database (default: `/palace/tokens.db`)
- `MCP_PORT` / `ADMIN_PORT` — Service ports (defaults: 7891 / 7892)

## Key Constraints

- Python 3.12, FastAPI, uvicorn — no TypeScript in this repo
- Port 7891 is the only port exposed externally (via Cloudflare Tunnel)
- Port 7892 must stay internal (admin access only)
- The `mempalace` package is an external dependency installed via pip
- Docker-first: all deployment uses docker-compose with a single shared host volume
