"""mcp-search — verifier-guided test-time search (Stage 1.2), port 9108.

Transport: streamable-http on $MCP_SEARCH_PORT (default 9108), path /mcp.
Health:    GET /health (reports generation availability + N caps).

Bounded best-of-N selection, lossless by construction: candidates are chosen by
EXECUTION (each run through mcp-verify), never by a model judging itself. Default
N is small and capped because best-of-N competes for the single your inference host GPU — use it
on HARD subtasks only.

Independent process. If killed, Hermes reports the tool unavailable and the
agent writes the single best patch itself — it never crashes Hermes.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import search_core

PORT = int(os.environ.get("MCP_SEARCH_PORT", "9108"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-search",
    instructions=(
        "Verifier-guided test-time search for HARD subtasks only. "
        "generate_and_select produces N bounded candidate patches and selects the "
        "one that verifies GREEN (most tests passed, smallest diff) — selection is "
        "execution-based, never self-judged. Supply `candidates` to use the "
        "selector directly (cheap, no model). Default-low N; it competes for the "
        "one GPU, so do NOT use it on easy work."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-search", "port": PORT,
                         **search_core.status()})


@mcp.tool()
def generate_and_select(task_spec: str, n: int = 0, language: str = "python",
                        target_path: str = "solution.py", tests: dict | None = None,
                        base_files: dict | None = None,
                        candidates: list | None = None) -> dict:
    """Bounded verifier-guided search. Two modes:

    * SELECTOR (supply `candidates=[{"id","files":{path:content}}, ...]` + `tests`):
      runs each candidate through mcp-verify and returns the green one (most tests
      passed, smallest diff). Cheap, always available, no model calls.
    * GENERATE (omit `candidates`): generates N patches from $VLLM_BASE_URL for the
      `task_spec` (writing `target_path`), then selects against `tests`. HARD
      subtasks only — N is capped because samples compete for the one GPU.

    Never returns a red selection: if nothing verifies green, selected is None.
    Degrades to a clear error (never crashes) when the model is unreachable.
    """
    return search_core.generate_and_select(task_spec, n, language, target_path, tests,
                                            base_files, candidates)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
