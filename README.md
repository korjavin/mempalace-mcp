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

## Container image

Pre-built images are published to GitHub Container Registry on every push to `main`:

```
ghcr.io/carlosvargasvip/mempalace-mcp:latest
```

---

## Option A — Automated deploy (Docker Compose)

The fastest path. One script handles everything.

```bash
git clone https://github.com/carlosvargasvip/mempalace-mcp.git
cd mempalace-mcp
./deploy.sh
```

`deploy.sh` will:
1. Create `/opt/mempalace/data` on the host
2. Generate `.env` with a random admin password (prints it once — save it)
3. Pull the image from GHCR
4. Initialize the MemPalace data store (first run only)
5. Start both services via `docker compose up -d`

To redeploy or update, just run `./deploy.sh` again.

---

## Option B — Manual Docker Compose

### 1. Create host data directory

```bash
sudo mkdir -p /opt/mempalace/data
sudo chown -R $USER:$USER /opt/mempalace
```

### 2. Set credentials

```bash
cp .env.example .env
nano .env          # set ADMIN_PASSWORD to something strong
```

### 3. Initialize the palace

Run the init command inside the container (no need to install anything on the host):

```bash
echo "" | docker run --rm -i -v /opt/mempalace:/palace \
  ghcr.io/carlosvargasvip/mempalace-mcp:latest \
  mempalace init /palace/data
```

Optionally mine your existing Claude.ai exports:

```bash
docker run --rm \
  -v /opt/mempalace:/palace \
  -v ~/Downloads/claude-exports:/exports:ro \
  ghcr.io/carlosvargasvip/mempalace-mcp:latest \
  mempalace mine /exports --palace /palace/data
```

### 4. Start

```bash
docker compose up -d
```

---

## Option C — Portainer

### Stack deployment

1. In Portainer, go to **Stacks → Add stack**
2. Choose **Repository** and enter:
   - **Repository URL**: `https://github.com/carlosvargasvip/mempalace-mcp`
   - **Branch**: `main`
   - **Compose path**: `docker-compose.yml`
3. Under **Environment variables**, add:
   | Name             | Value                      |
   |------------------|----------------------------|
   | `ADMIN_USER`     | `admin`                    |
   | `ADMIN_PASSWORD` | *(your strong password)*   |
   | `PUBLIC_URL`     | `https://mempalace.yourdomain.com` |
4. Click **Deploy the stack**

### Manual compose paste

If you prefer not to link a repository, go to **Stacks → Add stack → Web editor** and paste:

```yaml
services:
  mcp:
    image: ghcr.io/carlosvargasvip/mempalace-mcp:latest
    command: python mcp_bridge.py
    restart: unless-stopped
    ports:
      - "7891:7891"
    volumes:
      - /opt/mempalace:/palace
    environment:
      PALACE_PATH: /palace/data
      DB_PATH: /palace/tokens.db
      MCP_PORT: "7891"
      ADMIN_USER: ${ADMIN_USER:-admin}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:-changeme}
      PUBLIC_URL: ${PUBLIC_URL:-}
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:7891/health')"]
      interval: 30s
      timeout: 5s
      retries: 3

  admin:
    image: ghcr.io/carlosvargasvip/mempalace-mcp:latest
    command: python admin_server.py
    restart: unless-stopped
    ports:
      - "7892:7892"
    volumes:
      - /opt/mempalace:/palace
    environment:
      DB_PATH: /palace/tokens.db
      ADMIN_PORT: "7892"
      ADMIN_USER: ${ADMIN_USER:-admin}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:-changeme}
    depends_on:
      - mcp
```

Add the `ADMIN_USER` and `ADMIN_PASSWORD` environment variables in the **Environment variables** section below the editor before deploying.

### Initialize the palace (once)

Before the MCP bridge can serve requests, the data store must be initialized.
Run this from the Portainer host or via **Containers → mcp → Console**:

```bash
echo "" | docker run --rm -i -v /opt/mempalace:/palace \
  ghcr.io/carlosvargasvip/mempalace-mcp:latest \
  mempalace init /palace/data
```

Or from the Portainer console on the running `mcp` container:

```bash
mempalace init /palace/data
```

### Updating the image in Portainer

When a new image is pushed:
1. Go to your stack
2. Click **Pull and redeploy** (or **Update the stack** with "Re-pull image" checked)

---

## Post-deploy setup

These steps are the same regardless of which deployment option you used.

### 1. Open the admin portal

From any machine on your LAN:

```
http://<SERVER-LAN-IP>:7892
```

Log in with the `ADMIN_USER` / `ADMIN_PASSWORD` credentials from your `.env` file.

### 2. Configure Cloudflare Tunnel

Follow **[CLOUDFLARE_SETUP.md](CLOUDFLARE_SETUP.md)** to expose port 7891 at your subdomain (e.g. `https://mempalace.yourdomain.com`).

> **Security:** Port 7892 (admin portal) must **never** be added to the tunnel — it should only be accessible on your local network.

### 3. Connect your AI clients

MemPalace Bridge supports two authentication methods. Choose the one that matches your client.

---

#### Method A — OAuth 2.1 with PKCE (automatic)

**For clients that handle OAuth natively** — they discover endpoints, open a consent page, and receive a token automatically. No manual token creation needed.

| Client | OAuth Support |
|--------|--------------|
| Claude.ai (web) | Yes — built-in |
| Any MCP client with OAuth 2.1 | Yes |

**How it works:**

1. The client connects to `https://mempalace.yourdomain.com/sse`
2. The bridge returns `401` with a `WWW-Authenticate` header
3. The client discovers OAuth endpoints via `/.well-known/oauth-authorization-server`
4. A consent page opens — you enter your **admin password** to approve
5. The client receives an `mp_*` token automatically

**Claude.ai setup:**

1. Go to **Settings → Integrations → Add MCP Server**
2. Enter the server URL:
   ```
   https://mempalace.yourdomain.com/sse
   ```
3. Claude.ai opens the consent page — enter your **admin password** and approve
4. Done — all 19 MemPalace tools are now available in your conversations

OAuth-issued tokens appear in the admin portal labeled `oauth:<client_name>` and can be revoked at any time.

---

#### Method B — Bearer Token (manual)

**For clients that authenticate via static headers** — you create a token in the admin portal and configure the client with it directly. No OAuth flow involved.

| Client | Auth Method |
|--------|------------|
| Claude Code (CLI) | Bearer token via header |
| Claude Desktop | Bearer token via header |
| Cursor | Bearer token via header |
| n8n | Bearer token via header |
| Any HTTP client | Bearer token via header |

**Step 1 — Create a token:**

1. Open the admin portal at `http://<SERVER-LAN-IP>:7892`
2. Click **Issue Token**
3. Give it a descriptive label (e.g. `claude-code`, `cursor`, `n8n-prod`)
4. Copy the generated `mp_*` token — it is only shown once

**Step 2 — Configure your client:**

Every token-based client needs two values:

- **URL:** `https://mempalace.yourdomain.com/sse`
- **Header:** `Authorization: Bearer mp_YOUR_TOKEN`

Below are specific setup instructions for common clients.

---

##### Claude Code (CLI)

**Option 1 — CLI command (quickest)**

```bash
claude mcp add --transport sse mempalace https://mempalace.yourdomain.com/sse \
  --header "Authorization: Bearer mp_YOUR_TOKEN"
```

**Option 2 — Project config (shared with team)**

Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "mempalace": {
      "type": "sse",
      "url": "https://mempalace.yourdomain.com/sse",
      "headers": {
        "Authorization": "Bearer ${MEMPALACE_TOKEN}"
      }
    }
  }
}
```

Then set the environment variable before running Claude Code:

```bash
export MEMPALACE_TOKEN=mp_YOUR_TOKEN
```

**Option 3 — Global config (all projects)**

```bash
claude mcp add --transport sse --scope user mempalace https://mempalace.yourdomain.com/sse \
  --header "Authorization: Bearer mp_YOUR_TOKEN"
```

After adding, restart Claude Code. The 19 MemPalace tools will be available in every conversation.

---

##### Claude Desktop

Add to your `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "mempalace": {
      "type": "sse",
      "url": "https://mempalace.yourdomain.com/sse",
      "headers": {
        "Authorization": "Bearer mp_YOUR_TOKEN"
      }
    }
  }
}
```

Restart Claude Desktop to activate.

---

##### Cursor

1. Open **Settings → MCP Servers → Add Server**
2. Set the type to **SSE**
3. Enter the URL: `https://mempalace.yourdomain.com/sse`
4. Add the header: `Authorization: Bearer mp_YOUR_TOKEN`

---

##### n8n

1. Add an **MCP Client** node to your workflow
2. Set **SSE URL** to `https://mempalace.yourdomain.com/sse`
3. Under **Authentication**, choose **Header Auth**
4. Set header name to `Authorization` and value to `Bearer mp_YOUR_TOKEN`

---

##### Generic HTTP / custom clients

Any client that supports SSE with custom headers can connect:

```bash
# Test the connection with curl
curl -N -H "Authorization: Bearer mp_YOUR_TOKEN" \
  https://mempalace.yourdomain.com/sse
```

---

### Token management

All tokens — whether created manually or issued via OAuth — are managed from the admin portal:

- **View** all active and revoked tokens at `http://<SERVER-LAN-IP>:7892`
- **Revoke** a token to immediately block access (the token remains in the database for audit)
- **Delete** a token to remove it entirely
- OAuth tokens are labeled `oauth:<client_name>` for easy identification
- Each client should have its own token so you can revoke access per-client

---

## Operations

### Logs

```bash
docker compose logs -f mcp    # MCP bridge
docker compose logs -f admin  # Admin portal
```

### Backup

The only directory you need to back up:

```bash
tar -czf mempalace-backup-$(date +%Y%m%d).tar.gz /opt/mempalace/
```

### Build locally (development)

If you want to build the image yourself instead of pulling from GHCR:

```bash
docker build -t mempalace-mcp .
```

The Dockerfile uses a multi-stage build — the final image contains only the
Python runtime and dependencies, no build tools.
