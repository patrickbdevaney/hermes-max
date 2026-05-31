#!/usr/bin/env bash
curl -fsS -m 5 "http://127.0.0.1:${MCP_LSP_PORT:-9112}/health" >/dev/null 2>&1
