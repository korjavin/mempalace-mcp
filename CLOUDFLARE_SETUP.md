# Cloudflare Tunnel — MemPalace MCP Bridge
## Config-file setup (no scripts, no dashboard wizards)

---

## Overview

You will expose **only port 7891** (the MCP SSE bridge) via Cloudflare Tunnel.
Port 7892 (admin portal) stays internal — accessible from your office network
by IP only. This is intentional. Never add the admin portal to the tunnel.

```
Internet → Cloudflare → Tunnel → localhost:7891   ✓ MCP endpoint
                                → localhost:7892   ✗ admin (internal only)
```

---

## 1. Install cloudflared on the server

```bash
# Debian / Ubuntu
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
  https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install cloudflared
```

---

## 2. Authenticate with your Cloudflare account

```bash
cloudflared tunnel login
```

A browser window opens. Log in and select the zone (domain) you want to use.
This writes a certificate to `~/.cloudflared/cert.pem`.

---

## 3. Create the tunnel

```bash
cloudflared tunnel create mempalace-mcp
```

Note the tunnel UUID printed — you will use it in the next step.
The credentials file is written to `~/.cloudflared/<UUID>.json`.

---

## 4. Create the config file

Create `/etc/cloudflared/config.yml`:

```yaml
tunnel: <YOUR-TUNNEL-UUID>
credentials-file: /root/.cloudflared/<YOUR-TUNNEL-UUID>.json

ingress:
  - hostname: mempalace.carlosvargas.com
    service: http://localhost:7891
    originRequest:
      connectTimeout: 30s
      noTLSVerify: false

  # Catch-all — required by cloudflared
  - service: http_status:404
```

Replace `<YOUR-TUNNEL-UUID>` with the UUID from step 3.
Replace `mempalace.carlosvargas.com` with your actual subdomain.

---

## 5. Create the DNS record

```bash
cloudflared tunnel route dns mempalace-mcp mempalace.carlosvargas.com
```

This creates a CNAME in your Cloudflare DNS pointing the subdomain to the
tunnel. No A record needed — Cloudflare handles the routing.

---

## 6. Install as a systemd service

```bash
sudo cloudflared service install
```

This reads `/etc/cloudflared/config.yml` automatically and creates a systemd
unit (`cloudflared.service`).

Enable and start it:

```bash
sudo systemctl enable cloudflared
sudo systemctl start  cloudflared
sudo systemctl status cloudflared
```

---

## 7. Verify the tunnel is working

```bash
# From anywhere on the internet
curl -H "Authorization: Bearer mp_YOUR_TOKEN" \
     https://mempalace.carlosvargas.com/health
# Expected: {"ok":true,"sessions":0}
```

---

## 8. Add to Claude.ai

1. Go to **claude.ai → Settings → Integrations**
2. Click **Add MCP Server**
3. Fill in:
   - **URL**: `https://mempalace.carlosvargas.com/sse`
   - **Header name**: `Authorization`
   - **Header value**: `Bearer mp_YOUR_TOKEN`
4. Save — Claude.ai will call `/sse` and discover all 19 MemPalace tools.

---

## Admin portal access (from the office)

The admin portal runs on port 7892 and is NOT in the tunnel.
Access it from any machine on your office network:

```
http://<SERVER-LAN-IP>:7892
```

Log in with `ADMIN_USER` / `ADMIN_PASSWORD` from your `.env` file.
From there you can generate a token for Claude.ai, another for Cursor,
another for n8n — and revoke any of them independently.

---

## Cloudflare Access (optional hardening)

If you want an extra auth layer in front of the MCP endpoint (in addition to
Bearer tokens), you can attach a Cloudflare Access policy to the tunnel:

1. Cloudflare dashboard → **Access → Applications → Add an application**
2. Choose **Self-hosted**
3. Set the domain to `mempalace.carlosvargas.com`
4. Add a policy (e.g., allow by email or service token)

This gives you two independent auth layers: Cloudflare Access + Bearer token.

---

## Maintenance

```bash
# View tunnel logs
sudo journalctl -u cloudflared -f

# Reload config after changes
sudo systemctl restart cloudflared

# List tunnels
cloudflared tunnel list

# Delete tunnel (if needed)
cloudflared tunnel delete mempalace-mcp
```
