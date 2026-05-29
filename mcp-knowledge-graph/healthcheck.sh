#!/usr/bin/env bash
# Independent healthcheck for mcp-knowledge-graph.
set -euo pipefail
PORT="${MCP_KG_PORT:-9103}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
if curl -fsS -m 5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "mcp-knowledge-graph: healthy (${HOST}:${PORT})"
  exit 0
fi
echo "mcp-knowledge-graph: DOWN (${HOST}:${PORT})"
exit 1
