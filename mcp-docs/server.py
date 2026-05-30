"""mcp-docs — sovereign documentation ingestion as an MCP server.

Transport: streamable-http on $MCP_DOCS_PORT (default 9109), path /mcp.
Health:    GET /health.

The self-hosted knowledge loop: SearXNG → Crawl4AI → local distil → RAG + KG.
Independent process; if killed, Hermes reports the tools unavailable and the
agent degrades (it just can't learn new frameworks on demand that session). Every
backend (SearXNG, Crawl4AI, the chat model, RAG, KG) is reached over the network
and degrades gracefully when absent — nothing hard-fails.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import docs_core

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


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-docs", "port": PORT, **docs_core.stats()})


@mcp.tool()
def search_docs(query: str, category: str | None = None, limit: int = 8) -> dict:
    """Search the self-hosted SearXNG for candidate documentation URLs. Optional
    category (e.g. 'science', 'it', 'files') maps to SearXNG categories. Returns
    title/url/content snippets — the candidate set for ingest_doc/research_topic."""
    return docs_core.search_docs(query, category, limit)


@mcp.tool()
def fetch_clean(url: str) -> dict:
    """Fetch a URL and return clean, RAG-optimised markdown via the self-hosted
    Crawl4AI (trafilatura local fallback). The sovereign replacement for a
    Firecrawl/Tavily extract call — no API key."""
    return docs_core.fetch_clean(url)


@mcp.tool()
def ingest_doc(url_or_markdown: str, topic: str) -> dict:
    """Fetch (if a URL) → distil with the local chat model → store the high-signal
    note in mcp-codebase-rag under docs/<topic> (co-retrievable with code) AND
    record framework→api edges in mcp-knowledge-graph. Idempotent per (topic,url)."""
    return docs_core.ingest_doc(url_or_markdown, topic)


@mcp.tool()
def research_topic(topic: str, n: int = 3, category: str | None = None) -> dict:
    """Learn a novel framework on demand: search official docs → ingest the top N
    → return a distilled brief. After this, search_code('<topic> ...') returns the
    real signatures, closing the hallucinated-API trap at the knowledge layer."""
    return docs_core.research_topic(topic, n, category)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
