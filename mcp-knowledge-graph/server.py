"""mcp-knowledge-graph — embedded SQLite triples store as an MCP server.

Transport: streamable-http on $MCP_KG_PORT (default 9103), path /mcp.
Health:    GET /health.

Independent process; only shared state is the SQLite graph file. If killed,
Hermes reports the tools unavailable and the agent degrades gracefully (it just
can't recall/record structured knowledge that session).
"""

from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import kg_core

PORT = int(os.environ.get("MCP_KG_PORT", "9103"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-knowledge-graph",
    instructions=(
        "Persistent project knowledge graph. At task start, recall_about the "
        "files/services/decisions you're touching. At task end, record_entity "
        "for new decisions/bugs/components and record_relation to link them."
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
    return JSONResponse({"status": "ok", "server": "mcp-knowledge-graph", "port": PORT,
                         **kg_core.stats()})


@mcp.tool()
@_threaded
def record_entity(type: str, name: str, props: dict | None = None) -> dict:
    """Upsert an entity (e.g. type='decision'|'bug'|'file'|'service', name=...).
    Props merge into any existing props."""
    return kg_core.record_entity(type, name, props)


@mcp.tool()
@_threaded
def record_relation(a: str, rel: str, b: str, props: dict | None = None) -> dict:
    """Record a directed relation (a)-[rel]->(b), e.g. ('bug-42','fixed_in','commit-abc').
    Missing endpoints are auto-created as stub entities."""
    return kg_core.record_relation(a, rel, b, props)


@mcp.tool()
@_threaded
def query_graph(
    subject: str | None = None,
    rel: str | None = None,
    obj: str | None = None,
    type: str | None = None,
    contains: str | None = None,
    limit: int = 50,
) -> dict:
    """Query by triple pattern (subject/rel/obj, any subset) and/or by entity
    type / name substring. Returns matching entities and relations."""
    return kg_core.query_graph(subject, rel, obj, type, contains, limit)


@mcp.tool()
@_threaded
def recall_about(name: str) -> dict:
    """Return everything about an entity: its record plus incoming and outgoing
    relations (each annotated with the neighbor's type)."""
    return kg_core.recall_about(name)


@mcp.tool()
@_threaded
def core_memory_get() -> dict:
    """Read the agent-curated CORE MEMORY — the always-in-context, size-bounded
    block of highest-signal facts (conventions, gotchas, the architecture
    one-liner). Wired to Hermes's native MEMORY.md, so it's auto-loaded into
    context. Distinct from the auto-accumulated KG triples / RAG chunks."""
    return kg_core.core_memory_get()


@mcp.tool()
@_threaded
def core_memory_append(fact: str) -> dict:
    """Append ONE high-signal fact to core memory. Rejected if it would overflow
    the char limit (prune with core_memory_replace first) — protect the window."""
    return kg_core.core_memory_append(fact)


@mcp.tool()
@_threaded
def core_memory_replace(old: str | None = None, new: str | None = None,
                        block: str | None = None) -> dict:
    """Deliberately edit core memory: substring-replace (old→new) for a targeted
    prune/update, or pass `block` to replace the whole block (task-boundary
    curation). The MemGPT 'agent owns its working memory' move. Char-bounded."""
    return kg_core.core_memory_replace(old, new, block)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
