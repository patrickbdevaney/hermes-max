#!/usr/bin/env bash
# Independent healthcheck for mcp-codebase-rag.
set -euo pipefail
PORT="${MCP_RAG_PORT:-9102}"
HOST="${MCP_BIND_HOST:-127.0.0.1}"
if curl -fsS -m 5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "mcp-codebase-rag: healthy (${HOST}:${PORT})"
  exit 0
fi
echo "mcp-codebase-rag: DOWN (${HOST}:${PORT})"
exit 1

# FIX 4: RAG semantic-vs-BM25 honesty banner
_ebu="${EMBED_BASE_URL:-}"
if [ -z "$_ebu" ] && [ -f "$(dirname "$0")/../.env" ]; then
  _ebu="$(grep -E '^EMBED_BASE_URL=' "$(dirname "$0")/../.env" | tail -1 | cut -d= -f2-)"
fi
if [ -z "$_ebu" ]; then
  echo "RAG: BM25-only (no EMBED_BASE_URL set — semantic retrieval disabled)"
fi
