#!/usr/bin/env bash
# Independent healthcheck for mcp-observability.
set -euo pipefail
PORT="${MCP_OBSERVABILITY_PORT:-9104}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
if curl -fsS -m 5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "mcp-observability: healthy (${HOST}:${PORT})"
  exit 0
fi
echo "mcp-observability: DOWN (${HOST}:${PORT})"
exit 1
