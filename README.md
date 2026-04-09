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

### Open the admin portal

From any machine on your LAN:

```
http://<SERVER-LAN-IP>:7892
```

Log in → **Issue Token** → label it `claude.ai` → copy the token.

### Configure Cloudflare Tunnel

Follow **[CLOUDFLARE_SETUP.md](CLOUDFLARE_SETUP.md)** to expose port 7891 at your subdomain.
Port 7892 (admin) must **never** be added to the tunnel.

### Add to Claude.ai

The bridge supports OAuth 2.1 with PKCE — Claude.ai handles the flow automatically.

1. Go to **Settings → Integrations → Add MCP Server**
2. Enter the URL: `https://mempalace.yourdomain.com/sse`
3. Claude.ai will open a consent page — enter your **admin password** to approve
4. Done — Claude.ai now has a token and can use all 19 MemPalace tools

OAuth tokens appear in the admin portal with the label `oauth:Claude.ai` and can be revoked like any other token.

### Add to Claude Desktop or other clients (Bearer token)

Clients that support custom headers can use Bearer tokens directly (no OAuth needed).
Create a token in the admin portal, then configure:

- **URL**: `https://mempalace.yourdomain.com/sse`
- **Header**: `Authorization: Bearer mp_YOUR_TOKEN`

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
