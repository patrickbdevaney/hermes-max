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


@mcp.tool()
def quick_check(path: str, language: str = "auto") -> dict:
    """Fast incremental check — lint + typecheck only, NO tests.

    Run this after EACH diff/search-replace edit for cheap well-formed-edit
    feedback (the edit-format discipline), then run the full verify() at subtask
    end. Same structured shape as verify(), with the test stage omitted.
    """
    return verify_core.quick_check(path, language)


@mcp.tool()
def deep_verify(path: str, language: str = "auto", difficulty: str = "medium",
                layers: list | None = None) -> dict:
    """Full gate PLUS difficulty-gated deeper layers — closes silent-wrong-answer.

    difficulty: easy -> base only; medium -> +property(hypothesis);
    hard -> +property, mutation(mutmut, reports surviving mutants), fuzz(atheris).
    Each extra layer is independently skippable (missing tool -> skipped+warning)
    and advisory (won't flip a green base gate red, except property test failures).
    Use on subtasks the difficulty classifier flags non-trivial; don't run
    mutation testing on trivial changes.
    """
    return verify_core.deep_verify(path, language, difficulty, layers)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
