#!/usr/bin/env bash
# Independent healthcheck for mcp-escalation.
set -euo pipefail
PORT="${MCP_ESCALATION_PORT:-9105}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
if curl -fsS -m 5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "mcp-escalation: healthy (${HOST}:${PORT})"
  exit 0
fi
echo "mcp-escalation: DOWN (${HOST}:${PORT})"
exit 1
