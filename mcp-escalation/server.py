"""mcp-escalation — cloud model router with a hard daily USD cap. OFF by default.

Transport: streamable-http on $MCP_ESCALATION_PORT (default 9105), path /mcp.
Health:    GET /health (reports enabled state + today's spend vs cap).

Independent process. If killed, Hermes reports the tool unavailable and the
agent stays on the free local model — which is the default behavior anyway.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import escalation_core

PORT = int(os.environ.get("MCP_ESCALATION_PORT", "9105"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-escalation",
    instructions=(
        "Escalate ONLY genuinely-hard, well-scoped subproblems to a cheap cloud "
        "tier. OFF by default; a hard daily USD cap is enforced server-side. "
        "Never for routine work; never for Tier-3 (Opus/Claude Code)."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-escalation", "port": PORT,
                         **escalation_core.status()})


@mcp.tool()
def escalate(task: str, tier: str = "cheap") -> dict:
    """Route a hard, self-contained subproblem to a cheap cloud tier.

    Returns the model's result, the call cost, and today's spend vs the cap.
    Returns a disabled/cap-reached marker (never raises) when escalation is off
    or the daily cap is hit — callers must fall back to local work in that case.
    Tier-3 (Opus/Claude Code) is rejected by design.
    """
    return escalation_core.escalate(task, tier)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
