"""mcp-codebase-rag — hybrid (BM25 + dense) code retrieval as an MCP server.

Transport: streamable-http on $MCP_RAG_PORT (default 9102), path /mcp.
Health:    GET /health.

Dual-mode retrieval:
  (a) per-task injection — the workflow-task-start skill calls search_code at job
      start and injects the hits into context;
  (b) agent-callable search_code — the agent re-retrieves mid-task when it hits
      something unfamiliar.

Independent process; the only shared state is the SQLite index file, accessed
through these tools. If killed, Hermes reports the tools unavailable and
degrades gracefully.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import graph_core
import rag_core

PORT = int(os.environ.get("MCP_RAG_PORT", "9102"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-codebase-rag",
    instructions=(
        "Semantic + lexical retrieval over the user's own repositories. Call "
        "search_code to ground yourself in the codebase before and during work. "
        "Index a repo with index_repo(path) first; the index starts empty."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-codebase-rag", "port": PORT,
                         **rag_core.stats()})


@mcp.tool()
def index_repo(path: str) -> dict:
    """Index (or re-index) a repository directory into the hybrid store.

    Code-aware chunking (tree-sitter by function/class, heuristic fallback),
    lexical FTS5 + optional dense embeddings. Re-indexing replaces the repo's
    prior entries. Returns counts and whether dense embeddings were applied.
    """
    return rag_core.index_repo(path)


@mcp.tool()
def search_code(query: str, k: int = 8) -> dict:
    """Hybrid search (BM25 + dense + graph via RRF, then optional cross-encoder
    rerank) over indexed code.

    Returns up to k results with symbol, kind, path:line location, and a code
    snippet. The `mode` field reports exactly which lanes were active
    (e.g. "hybrid+graph+rerank" or "bm25-only"); each lane degrades gracefully
    when its endpoint is absent, so retrieval never hard-fails.
    """
    return rag_core.search_code(query, k)


@mcp.tool()
def index_document(text: str, namespace: str, source: str = "", title: str = "") -> dict:
    """Ingest a distilled document (markdown) into the hybrid store under a
    `namespace` (e.g. 'docs/fastapi'), co-retrievable with code via search_code.
    Idempotent per (namespace, source). Used by mcp-docs to land learned-framework
    knowledge alongside code-trace memory."""
    return rag_core.index_document(text, namespace, source, title)


@mcp.tool()
def get_symbol_context(symbol: str, k: int = 5) -> dict:
    """Return the full chunk(s) defining a named symbol (function/class/etc.)."""
    return rag_core.get_symbol_context(symbol, k)


@mcp.tool()
def find_similar(snippet: str, k: int = 8) -> dict:
    """Find code most similar to a snippet (dense if available, else lexical)."""
    return rag_core.find_similar(snippet, k)


@mcp.tool()
def retrieve_related(symbol: str, hops: int = 1, k: int = 20) -> dict:
    """Graph/AST-aware retrieval: the multi-hop neighbors of a symbol — what it
    calls (callees), what calls it (callers), and imports. Most real fixes need
    a multi-hop connection, so use this to pull the surrounding code after
    search_code/get_symbol_context locates a starting symbol. Falls back with a
    'graph retrieval unavailable' note if the graph isn't built."""
    return graph_core.retrieve_related(symbol, hops, k)


@mcp.tool()
def repo_map(token_budget: int = 2000, repo: str | None = None) -> dict:
    """A PageRank-ranked, token-budgeted map of the repo's symbols (Aider-style
    repo map) — the highest-leverage symbols first. Use it to orient on an
    unfamiliar codebase before diving in. Falls back gracefully if no graph."""
    return graph_core.repo_map(token_budget, repo)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
