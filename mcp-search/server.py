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

import asyncio
import functools
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


def _threaded(fn):
    """Run a sync @mcp.tool() body on a worker thread so it never blocks the event
    loop. FastMCP (1.27) calls sync tool handlers directly in the single event-loop
    thread, so any long tool (running tests, indexing a repo, an LLM/cloud call,
    fetching+distilling a page) stalls EVERY other request — including GET /health,
    which is what made a live server show DOWN while it was actively serving the
    agent. asyncio.to_thread offloads the body so /health and concurrent calls stay
    responsive; functools.wraps preserves the typed signature for the schema, and
    the body runs in a thread with no running loop (so MCP-to-MCP asyncio.run works).
    """
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-search", "port": PORT,
                         **search_core.status()})


@mcp.tool()
@_threaded
def generate_and_select(task_spec: str, n: int = 0, language: str = "python",
                        target_path: str = "solution.py", tests: dict | None = None,
                        base_files: dict | None = None,
                        candidates: list | None = None,
                        early_exit: bool = True, quality_threshold: float = 0.0) -> dict:
    """Bounded verifier-guided search. Two modes:

    * SELECTOR (supply `candidates=[{"id","files":{path:content}}, ...]` + `tests`):
      runs each candidate through mcp-verify and returns the green one (most tests
      passed, smallest diff). Cheap, always available, no model calls.
    * GENERATE (omit `candidates`): generates N patches from $VLLM_BASE_URL for the
      `task_spec` (writing `target_path`), then selects against `tests`. HARD
      subtasks only — N is capped because samples compete for the one GPU.

    early_exit (default True): generate-and-verify one at a time and return the
    moment a candidate goes GREEN — saving the cost of the remaining samples
    (RASC: large savings at comparable accuracy). quality_threshold (0-1, 0=off):
    when no test oracle exists, score candidates with the reranker and return the
    first above threshold. Never returns a red selection; degrades to a clear error.
    """
    return search_core.generate_and_select(task_spec, n, language, target_path, tests,
                                            base_files, candidates, early_exit, quality_threshold)


@mcp.tool()
@_threaded
def parallel_draft(task_spec: str, language: str = "python",
                   target_path: str = "solution.py", tests: dict | None = None,
                   base_files: dict | None = None, n: int = 0,
                   draft_brief: str | None = None) -> dict:
    """Verifier-selected best-of-N across the FREE/cheap conductor pool (Cerebras/
    Groq/… + optional DeepInfra anchor) — the optimal use of 'slop' models.

    VERIFIABLE subtasks ONLY: `tests` (the objective oracle, {path:content}) is
    REQUIRED. Without it the subtask is AMBIGUOUS and is routed to the synthesize
    role (route_to='synthesize') — no oracle means the verifier can't select.

    Fans out ONE draft per present pool family for cross-family DIVERSITY, runs
    every candidate through mcp-verify, and returns the one that goes GREEN (most
    tests, smallest diff) in `selected_files` for the LOCAL model to integrate +
    checkpoint. None pass -> route_to='synthesize'. Pool empty/unreachable ->
    local generation fallback or route_to='local'. Never raises."""
    return search_core.parallel_draft(task_spec, language, target_path, tests,
                                       base_files, n, draft_brief)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
