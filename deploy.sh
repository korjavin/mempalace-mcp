#!/usr/bin/env bash
set -euo pipefail

IMAGE="ghcr.io/carlosvargasvip/mempalace-mcp:latest"
PALACE_DIR="/opt/mempalace"
DATA_DIR="$PALACE_DIR/data"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── Prerequisites ────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || error "Docker is not installed."
docker compose version >/dev/null 2>&1 || error "Docker Compose V2 is not installed."

cd "$SCRIPT_DIR"

# ── 1. Data directory ────────────────────────────────────────────────────────
if [ ! -d "$DATA_DIR" ]; then
    info "Creating $DATA_DIR"
    sudo mkdir -p "$DATA_DIR"
    sudo chown "$(id -u):$(id -g)" "$PALACE_DIR" "$DATA_DIR"
else
    info "Data directory exists"
fi

# ── 2. Environment file ─────────────────────────────────────────────────────
if [ ! -f .env ]; then
    PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)
    cat > .env <<EOF
ADMIN_USER=admin
ADMIN_PASSWORD=$PASSWORD
EOF
    info "Generated .env"
    echo ""
    echo -e "  ${BOLD}Admin credentials (save these now):${NC}"
    echo "    Username: admin"
    echo "    Password: $PASSWORD"
    echo ""
else
    info ".env already exists — skipping"
fi

# ── 3. Pull image ───────────────────────────────────────────────────────────
info "Pulling $IMAGE..."
docker pull "$IMAGE"

# ── 4. Initialize palace ────────────────────────────────────────────────────
if [ -z "$(ls -A "$DATA_DIR" 2>/dev/null)" ]; then
    info "Initializing MemPalace data store..."
    echo "" | docker run --rm -i -v "$PALACE_DIR:/palace" "$IMAGE" mempalace init /palace/data
else
    info "Palace already initialized"
fi

# ── 5. Start services ──────────────────────────────────────────────────────
info "Starting services..."
docker compose up -d

# ── 6. Status ───────────────────────────────────────────────────────────────
echo ""
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

info "MemPalace Bridge is running"
echo ""
echo "  Admin portal : http://$SERVER_IP:7892"
echo "  MCP endpoint : http://$SERVER_IP:7891/sse"
echo "  Health check : http://$SERVER_IP:7891/health"
echo ""
echo "  Next steps:"
echo "    1. Open the admin portal and create a token"
echo "    2. Configure Cloudflare Tunnel (see CLOUDFLARE_SETUP.md)"
echo "    3. Add MCP server in Claude.ai with your token"
echo ""
