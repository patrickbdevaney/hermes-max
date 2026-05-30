#!/usr/bin/env bash
# Independent healthcheck for mcp-search.
set -euo pipefail
PORT="${MCP_SEARCH_PORT:-9108}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
if curl -fsS -m 5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "mcp-search: healthy (${HOST}:${PORT})"
  exit 0
fi
echo "mcp-search: DOWN (${HOST}:${PORT})"
exit 1
