FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App files
COPY mcp_bridge.py   .
COPY admin_server.py .
COPY static/         ./static/

# Palace directory — this is where the volume mounts.
# Data NEVER lives in the image; it lives on the host.
RUN mkdir -p /palace/data

# Two ports: 7891 = MCP, 7892 = Admin
EXPOSE 7891 7892
