"""mcp-docs — sovereign documentation ingestion as an MCP server.

Transport: streamable-http on $MCP_DOCS_PORT (default 9109), path /mcp.
Health:    GET /health (LIVENESS — fast, no upstream calls, the UP/DOWN signal).
           GET /ready  (READINESS — searxng/crawl4ai/distil; informational).

The self-hosted knowledge loop: SearXNG → Crawl4AI → local distil → RAG + KG.
Independent process; if killed, Hermes reports the tools unavailable and the
agent degrades (it just can't learn new frameworks on demand that session). Every
backend (SearXNG, Crawl4AI, the chat model, RAG, KG) is reached over the network
and degrades gracefully when absent — nothing hard-fails.
"""
from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import docs_core

try:
    import session_state  # record lighter-tool attempts for the research exhaustion gate
except Exception:  # noqa: BLE001 - rationing is best-effort; never break the server
    session_state = None  # type: ignore

PORT = int(os.environ.get("MCP_DOCS_PORT", "9109"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-docs",
    instructions=(
        "Sovereign documentation ingestion. When you hit a novel/domain-specific "
        "framework you can't reason about from pretraining, call research_topic("
        "topic) BEFORE coding to search official docs, distil them, and store them "
        "so search_code retrieves real signatures. search_docs/fetch_clean/"
        "ingest_doc are the lower-level steps. Fully local — no external API."
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
async def health(request: Request) -> JSONResponse:
    """LIVENESS — process up + HTTP answering, returned immediately with NO
    upstream calls (sub-10ms). The UP/DOWN signal for status.sh / healthcheck.sh:
    a live docs server must NEVER show DOWN because SearXNG or Crawl4AI is slow.
    Dependency status moved to /ready (informational). `?deep=1` forwards there."""
    if request.query_params.get("deep", "").lower() in ("1", "true", "yes"):
        return await ready(request)
    return JSONResponse({"status": "ok", "server": "mcp-docs", "port": PORT})


@mcp.custom_route("/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    """READINESS — informational dependency snapshot (searxng_up, crawl4ai_up,
    distill model, rag/kg). MAY probe upstreams (so it can be slow); a failing
    dependency here is a WARNING, never DOWN. fetch_clean still falls back
    trafilatura→Crawl4AI and search degrades gracefully per the matrix."""
    return JSONResponse({"status": "ok", "server": "mcp-docs", "port": PORT, **docs_core.stats()})


@mcp.tool()
@_threaded
def search_docs(query: str, category: str | None = None, limit: int = 8) -> dict:
    """Search the self-hosted SearXNG for candidate documentation URLs. Optional
    category (e.g. 'science', 'it', 'files') maps to SearXNG categories. Returns
    title/url/content snippets — the candidate set for ingest_doc/research_topic."""
    return docs_core.search_docs(query, category, limit)


@mcp.tool()
@_threaded
def fetch_clean(url: str) -> dict:
    """Fetch a URL and return clean, RAG-optimised markdown. Extraction ladder is
    fastest-first: trafilatura (local, in-process) then Crawl4AI (the JS-rendering
    fallback). The sovereign replacement for a Firecrawl/Tavily extract — no API key."""
    if session_state is not None:
        try: session_state.record_lighter_tool("fetch_clean", url)
        except Exception: pass  # noqa: BLE001,E722
    return docs_core.fetch_clean(url)


@mcp.tool()
@_threaded
def ingest_doc(url_or_markdown: str, topic: str) -> dict:
    """Fetch (if a URL) → distil with the local chat model → store the high-signal
    note in mcp-codebase-rag under docs/<topic> (co-retrievable with code) AND
    record framework→api edges in mcp-knowledge-graph. Idempotent per (topic,url)."""
    return docs_core.ingest_doc(url_or_markdown, topic)


@mcp.tool()
@_threaded
def research_topic(topic: str, n: int = 3, category: str | None = None) -> dict:
    """Learn a novel framework on demand: search official docs → ingest the top N
    → return a distilled brief. After this, search_code('<topic> ...') returns the
    real signatures, closing the hallucinated-API trap at the knowledge layer."""
    if session_state is not None:
        try: session_state.record_lighter_tool("research_topic", topic)
        except Exception: pass  # noqa: BLE001,E722
    return docs_core.research_topic(topic, n, category)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
