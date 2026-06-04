"""mcp-edit — validated edit application (port 9118).

Transport: streamable-http on $MCP_EDIT_PORT (default 9118), path /mcp.
Health:    GET /health.

A presence-gated MCP that enforces mechanical edit formats so a small model can't silently
corrupt files with elided whole-file rewrites or ambiguous patches:
  • validated_write(path, content) — whole file, rejects elision markers.
  • validated_edit(path, search, replace) — SEARCH/REPLACE, unique anchor + difflib fuzzy
    fallback, atomic, returns a unified diff.
  • apply_edit_blocks(path, response_text) — apply all SEARCH/REPLACE blocks in a response.

Additive: the executor's native write_file / edit_file remain the fallback — if this server
is down nothing breaks. Pure stdlib; cwd-confined via $AGENT_WORK_DIR.
"""
from __future__ import annotations

import asyncio
import functools
import os
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import edit_core

PORT = int(os.environ.get("MCP_EDIT_PORT", "9118"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-edit",
    instructions=(
        "Validated edits. For an EDIT to an existing file prefer validated_edit(path, search, "
        "replace): the SEARCH block must be UNIQUE in the file; on a miss you get the nearest "
        "candidate (fuzzy) instead of a silent wrong patch. For a NEW or fully-rewritten file "
        "use validated_write(path, content) — it refuses a partial file with '...'/placeholder "
        "gaps, so send the whole file. These never corrupt a file on an ambiguous match."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _threaded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Run a sync @mcp.tool() body on a worker thread so it never blocks the event loop."""
    @functools.wraps(fn)
    async def _aw(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-edit", "port": PORT})


@mcp.tool()
@_threaded
def validated_write(path: str, content: str) -> dict:
    """Write the COMPLETE file at `path`. REJECTS any elision marker ('...', '# rest of
    code', '[existing code]', '# unchanged', …) — you must return the whole file, never a
    partial with omissions. Returns {ok, path, bytes} or {ok:false, error}."""
    return edit_core.validated_write(path, content)


@mcp.tool()
@_threaded
def validated_edit(path: str, search: str, replace: str) -> dict:
    """Replace `search` with `replace` in `path`. The SEARCH block MUST appear exactly once
    (unique anchor). On an exact miss, a difflib fuzzy match returns the nearest candidate +
    ratio so you can fix SEARCH instead of guessing; an ambiguous (>1) match is rejected.
    Applies atomically and returns {ok, diff} or {ok:false, error, fuzzy_ratio?, nearest_candidate?}."""
    return edit_core.validated_edit(path, search, replace)


@mcp.tool()
@_threaded
def apply_edit_blocks(path: str, response_text: str) -> dict:
    """Apply every '<<<<<<< SEARCH / ======= / >>>>>>> REPLACE' block in `response_text` to
    `path`, in order, stopping on the first failure. Returns {ok, applied} or {ok:false,
    error, applied}."""
    return edit_core.apply_search_replace_blocks(path, response_text)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
