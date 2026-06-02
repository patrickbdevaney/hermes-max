"""mcp-scopemap — the two-phase context protocol as an MCP server (Fix 1).

Transport: streamable-http on $MCP_SCOPEMAP_PORT (default 9115), path /mcp.
Health:    GET /health.

Solves cold-start (a new repo has no RAG index) and context overflow (a large repo
exceeds the planner's window): get_repo_map() returns a static, ~8-12k-token
structural map of ANY directory in <2s (no LLM), and request_context() fetches
exactly the files the planner asks for at the requested depth. Greenfield → an empty
map. Pure static analysis; independent process — if it dies the agent degrades to
RAG/LSP/reading files.
"""
from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import scopemap_core

PORT = int(os.environ.get("MCP_SCOPEMAP_PORT", "9115"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-scopemap",
    instructions=(
        "Two-phase context for any repo. ALWAYS call get_repo_map(cwd) FIRST on a new "
        "task to see the codebase structure (or confirm it's greenfield). Then, before "
        "execution, issue a CONTEXT_REQUEST via request_context(cwd, files) to pull "
        "exactly the files you need at the right depth — full bodies for what you'll "
        "edit, signatures for what you'll call, nothing for the rest. This keeps the "
        "planner's window surgical instead of ingesting everything or flying blind."
    ),
    host=HOST,
    port=PORT,
)


def _threaded(fn):
    """Run a sync @mcp.tool() body on a worker thread so it never blocks the loop."""
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-scopemap", "port": PORT})


@mcp.tool()
@_threaded
def get_repo_map(cwd: str, force: bool = False) -> dict:
    """Return REPO_MAP.md for `cwd` — one line per source file (path + a one-sentence
    description from the module docstring / top-level names). ~8-12k tokens regardless
    of repo size, generated in <2s by static analysis (no LLM). Cached and invalidated
    when the repo changes. Greenfield (no source files) → an empty map.

    Call this FIRST on any new task. Args: cwd (the project dir), force (rebuild cache).
    Returns {ok, map, greenfield, cwd}."""
    m = scopemap_core.get_repo_map(cwd, force=force)
    greenfield = "greenfield" in m.split("\n", 4)[3] if m.count("\n") >= 3 else False
    return {"ok": True, "map": m, "greenfield": greenfield,
            "cwd": os.path.abspath(os.path.expanduser(cwd or "."))}


@mcp.tool()
@_threaded
def request_context(cwd: str, need_full: list[str] | None = None,
                    need_signatures: list[str] | None = None,
                    need_nothing: list[str] | None = None) -> dict:
    """Phase 2 — fetch exactly the files you asked for, at the requested depth. After
    seeing the repo map, the planner's CONTEXT_REQUEST names: need_full (whole file
    bodies for what you'll edit), need_signatures (function/class signatures only, for
    what you'll call), need_nothing (explicitly excluded — honored by not fetching).
    Returns {ok, context} — one blob ready to inject into the executor's window."""
    files = {"need_full": need_full or [], "need_signatures": need_signatures or [],
             "need_nothing": need_nothing or []}
    return {"ok": True, "context": scopemap_core.request_context(cwd, files)}


@mcp.tool()
@_threaded
def invalidate_map(cwd: str) -> dict:
    """Drop the cached REPO_MAP for `cwd`. The checkpoint MCP calls this after a
    verified-green commit (the repo changed meaningfully), so the next get_repo_map
    rebuilds from the new state."""
    return scopemap_core.invalidate(cwd)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
