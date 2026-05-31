"""mcp-codegraph — deterministic AST code-intelligence as an MCP server (Phase 1.2).

Transport: streamable-http on $MCP_CODEGRAPH_PORT (default 9114), path /mcp.
Health:    GET /health.

Structural code queries that RAG (semantic) and LSP (per-symbol) don't give you:
blast-radius/impact, call hierarchy, importers, dead code, structural pattern match.
Python-ast graph in SQLite. Independent process; pure static analysis, no model.
"""
from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import codegraph_core

PORT = int(os.environ.get("MCP_CODEGRAPH_PORT", "9114"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
DEFAULT_REPO = os.path.abspath(os.path.expanduser(
    os.environ.get("CODEGRAPH_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

mcp = FastMCP(
    "mcp-codegraph",
    instructions=(
        "Deterministic AST code-intelligence. Before editing a function call "
        "code_impact to see its blast radius; code_callers/code_callees for call "
        "flow; code_importers for module deps; code_dead_code for unreferenced "
        "symbols; code_structural_search for pattern-based refactoring."
    ),
    host=HOST, port=PORT, stateless_http=True, json_response=True,
)


def _threaded(fn):
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-codegraph", "port": PORT,
                         **codegraph_core.stats()})


@mcp.tool()
@_threaded
def index_codegraph(repo_path: str = "") -> dict:
    """(Re)build the AST graph for a repo (defaults to the hermes-max root). Run once
    per repo before querying; idempotent (full rebuild). Fast (Python-ast, no model)."""
    return codegraph_core.index_repo(repo_path or DEFAULT_REPO)


@mcp.tool()
@_threaded
def code_impact(symbol: str, max_depth: int = 4, repo_path: str = "") -> dict:
    """BLAST RADIUS: the transitive set of symbols that (in)directly call `symbol` —
    what could break if you change it. Call this BEFORE editing a function/method.
    name-resolved reverse-call closure (conservative over-approximation)."""
    return codegraph_core.code_impact(symbol, max_depth, repo_path or DEFAULT_REPO)


@mcp.tool()
@_threaded
def code_callers(symbol: str, repo_path: str = "") -> dict:
    """Direct callers of `symbol` (reverse call edges) — who depends on it now."""
    return codegraph_core.code_callers(symbol, repo_path or DEFAULT_REPO)


@mcp.tool()
@_threaded
def code_callees(symbol: str, repo_path: str = "") -> dict:
    """What `symbol` calls (forward call edges) — its outgoing dependencies."""
    return codegraph_core.code_callees(symbol, repo_path or DEFAULT_REPO)


@mcp.tool()
@_threaded
def code_importers(file_or_module: str, repo_path: str = "") -> dict:
    """Which files import `file_or_module` — the module-level dependents."""
    return codegraph_core.code_importers(file_or_module, repo_path or DEFAULT_REPO)


@mcp.tool()
@_threaded
def code_dead_code(repo_path: str = "") -> dict:
    """Candidate dead code: functions/classes never called by name in the repo
    (advisory — excludes dunders/tests/entrypoints; may be an API or dynamic dispatch)."""
    return codegraph_core.code_dead_code(repo_path or DEFAULT_REPO)


@mcp.tool()
@_threaded
def code_structural_search(pattern: str, repo_path: str = "", language: str = "python") -> dict:
    """ast-grep structural pattern match (e.g. 'def $F($$$): pass') — the structural
    primitive grep/embeddings can't do. Degrades with a clear note if ast-grep is
    not installed."""
    return codegraph_core.code_structural_search(pattern, repo_path or DEFAULT_REPO, language)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
