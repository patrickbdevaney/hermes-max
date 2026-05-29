#!/usr/bin/env bash
# Independent healthcheck for mcp-checkpoint. Exits 0 if the server answers /health.
set -euo pipefail
PORT="${MCP_CHECKPOINT_PORT:-9106}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
if curl -fsS -m 5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "mcp-checkpoint: healthy (${HOST}:${PORT})"
  exit 0
fi
echo "mcp-checkpoint: DOWN (${HOST}:${PORT})"
exit 1
