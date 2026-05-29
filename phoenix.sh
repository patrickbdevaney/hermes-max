#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Phoenix (Arize) observability for Hermes — one self-healing container.
# Same fire-and-forget pattern as SearXNG. No account, no keys, no headache.
#
# UI:          http://localhost:6006   (trace waterfalls, query, filter)
# OTLP ingest: http://localhost:4317   (MCP servers emit OpenTelemetry here)
# Persistence: named volume -> SQLite, survives reboots
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

echo "→ Pulling and starting Phoenix..."
docker run -d --name phoenix \
  -p 6006:6006 \
  -p 4317:4317 \
  -v phoenix_data:/mnt/data \
  -e PHOENIX_WORKING_DIR=/mnt/data \
  --restart unless-stopped \
  arizephoenix/phoenix:latest

echo "→ Confirming Docker starts on boot (so Phoenix self-heals after reboot)..."
if [ "$(systemctl is-enabled docker 2>/dev/null)" != "enabled" ]; then
  echo "  Docker not enabled on boot — enabling it once:"
  sudo systemctl enable docker
else
  echo "  ✓ Docker already enabled on boot."
fi

echo
echo "✓ Phoenix is up."
echo "    UI:          http://localhost:6006"
echo "    OTLP ingest: http://localhost:4317"
echo
echo "  The ONLY env var your MCP servers need (add to ~/.hermes/.env):"
echo "    PHOENIX_COLLECTOR_ENDPOINT=http://localhost:4317"
echo
echo "  Useful later:"
echo "    docker ps                # confirm running"
echo "    docker logs phoenix      # check health"
echo "    docker stop phoenix      # stop"
echo "    docker start phoenix     # start again"