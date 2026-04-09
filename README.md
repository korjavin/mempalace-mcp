# MemPalace Bridge

Exposes your local MemPalace MCP server over HTTPS so Claude.ai, Cursor,
ChatGPT, n8n, and any other remote AI client can use it as a memory layer.

## Architecture

```
Claude.ai / Cursor / n8n
         │
         │  HTTPS  Bearer token
         ▼
  Cloudflare Tunnel
         │
         ▼
  localhost:7891  ← mcp_bridge.py  (SSE → stdio proxy)
         │
         ▼
  MemPalace MCP server (subprocess, 19 tools)
         │
         ▼
  /opt/mempalace/data  ← ChromaDB  (host volume, never inside container)
  /opt/mempalace/tokens.db          (shared with admin)

  localhost:7892  ← admin_server.py  (token management UI)
                     (internal network only — NOT in CF Tunnel)
```

## First-time setup

### 1. Create host data directory

```bash
sudo mkdir -p /opt/mempalace/data
sudo chown -R $USER:$USER /opt/mempalace
```

### 2. Initialize the palace

```bash
pip install mempalace
mempalace init /opt/mempalace/data
```

Optionally mine your existing Claude.ai exports:

```bash
mempalace mine ~/Downloads/claude-exports/ --palace /opt/mempalace/data
```

### 3. Set credentials

```bash
cp .env.example .env
nano .env          # set ADMIN_PASSWORD to something strong
```

### 4. Build and start

```bash
docker compose up -d --build
```

### 5. Open the admin portal

From any machine on your office network:

```
http://<SERVER-LAN-IP>:7892
```

Log in → **Issue Token** → label it "claude.ai" → copy the token.

### 6. Configure Cloudflare Tunnel

Follow **CLOUDFLARE_SETUP.md** to expose port 7891 at your subdomain.

### 7. Add to Claude.ai

Settings → Integrations → Add MCP Server
- URL: `https://mempalace.carlosvargas.com/sse`
- Header: `Authorization: Bearer mp_YOUR_TOKEN`

---

## Backup

The only directory you need to back up:

```bash
tar -czf mempalace-backup-$(date +%Y%m%d).tar.gz /opt/mempalace/
```

---

## Logs

```bash
docker compose logs -f mcp    # MCP bridge
docker compose logs -f admin  # Admin portal
```
