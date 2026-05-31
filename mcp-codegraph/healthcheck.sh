#!/usr/bin/env bash
curl -fsS -m 5 "http://127.0.0.1:${MCP_CODEGRAPH_PORT:-9114}/health" >/dev/null 2>&1
