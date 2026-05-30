#!/usr/bin/env bash
# Independent healthcheck for mcp-watchdog.
set -euo pipefail
PORT="${MCP_WATCHDOG_PORT:-9107}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
if curl -fsS -m 5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "mcp-watchdog: healthy (${HOST}:${PORT})"
  exit 0
fi
echo "mcp-watchdog: DOWN (${HOST}:${PORT})"
exit 1
