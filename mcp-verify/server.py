"""mcp-verify — deterministic verification gate as an independent MCP server.

Transport: streamable-http on $MCP_VERIFY_PORT (default 9101), path /mcp.
Health:    GET /health (independent of the MCP protocol, for healthcheck.sh).

This process shares no mutable state with any other component. If it dies,
Hermes's MCP client simply reports the `verify` tool unavailable and the agent
degrades gracefully — it never takes the harness down.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import verify_core

PORT = int(os.environ.get("MCP_VERIFY_PORT", "9101"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-verify",
    instructions=(
        "Deterministic code verification gate. Call verify(path) before "
        "declaring any coding task done; iterate until passed is true."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-verify", "port": PORT})


@mcp.tool()
def verify(path: str, language: str = "auto") -> dict:
    """Run lint -> typecheck -> unit tests on a file or directory.

    Args:
        path: File or directory to verify.
        language: "auto" (default), "python", "ts", or "rust".

    Returns a structured result with a top-level `passed` boolean, a per-stage
    breakdown (lint/typecheck/tests, each passed|failed|skipped|error with
    diagnostics), and a human-readable `summary`. The gate is green only when at
    least one stage ran and none failed; missing tools are reported as skipped.
    """
    return verify_core.verify(path, language)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
