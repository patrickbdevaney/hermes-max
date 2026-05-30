#!/usr/bin/env bash
# Quick health probe for mcp-research and its backends.
set -uo pipefail
PORT="${MCP_RESEARCH_PORT:-9110}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
echo "mcp-research health (:${PORT}):"
curl -fsS -m5 "http://${HOST}:${PORT}/health" 2>/dev/null | python3 -m json.tool 2>/dev/null \
  || echo "  DOWN (start: scripts/start-all.sh)"
