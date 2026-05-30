"""mcp-research — SOTA local deep-research (port 9110).

Transport: streamable-http on $MCP_RESEARCH_PORT (default 9110), path /mcp.
Health:    GET /health (reports backends + bounds).

The canonical four-stage deep-research loop — plan -> develop -> explore -> verify
-> synthesize — built as bounded, deterministic MCP tools on TOP of the existing
sovereign stack (SearXNG + Crawl4AI/trafilatura via mcp-docs + the local chat model
+ RAG/KG). Engineered against the four named failure modes (echo chamber, source-
quality bias, planning hallucination, overspawning). Fully sovereign — no external
API on either deploy profile.

Independent process. If killed, Hermes reports the tools unavailable and the agent
degrades to single-shot search; it never crashes Hermes. Every backend degrades
gracefully (SearXNG down -> empty explore; Crawl4AI down -> trafilatura; reranker
unset -> authority-only; $VLLM_BASE_URL unset -> deterministic plan/queries/synth).
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import research_core
import sources

PORT = int(os.environ.get("MCP_RESEARCH_PORT", "9110"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-research",
    instructions=(
        "Local deep-research that approaches proprietary quality via a few patterns, "
        "not a framework. For current/external knowledge beyond pretraining+RAG, call "
        "deep_research(question) — it plans, develops DIVERSE queries, explores with "
        "URL/n-gram dedup + authority-aware ranking, verifies each claim against >=2 "
        "INDEPENDENT sources, and synthesizes a CITATION-BACKED report, then compounds "
        "it into RAG/KG. Use the lower-level plan_research/develop_queries/explore/"
        "verify_claims/synthesize for finer control. Always verify before asserting, "
        "cite every claim, prefer primary sources, and stop at the loop/budget cap "
        "with an honest confidence+gaps note rather than padding. Fully sovereign."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-research", "port": PORT,
                         **research_core.stats(), "sources": sources.source_stats()})


@mcp.tool()
def plan_research(question: str) -> dict:
    """Decompose a research question into 2-5 complementary sub-goals + an ordered
    roadmap, written to external PLAN.md state so the plan itself is checkable
    (planning hallucination is most damaging here). Degrades to a single-sub-goal
    plan without the chat model."""
    return research_core.plan_research(question)


@mcp.tool()
def develop_queries(subgoal: str, n: int = 4) -> dict:
    """Generate diverse, COMPLEMENTARY search queries for a sub-goal (varied
    abstraction/angle), deduped by n-gram similarity — the direct counter to
    echo-chamber retrieval. Returns near-duplicate-free queries."""
    return research_core.develop_queries(subgoal, n)


@mcp.tool()
def explore(queries: list, seen_urls: list | None = None,
            max_sources_per_query: int = 3, max_total: int = 8,
            category: str | None = None) -> dict:
    """Iterative web exploration over SearXNG + Crawl4AI/trafilatura. Applies URL +
    n-gram CONTENT dedup (break echo chambers — pass prior seen_urls across loops),
    authority-aware re-ranking (primary/official/papers > SEO farms; optional
    cross-encoder on top), and HARD breadth caps (no overspawning). Returns fetched
    sources with clean markdown + provenance + echo/low-authority filter counts."""
    return research_core.explore(queries, seen_urls, max_sources_per_query, max_total, category)


@mcp.tool()
def verify_claims(claims: list, min_sources: int = 2) -> dict:
    """Cross-check each material claim against >= min_sources INDEPENDENT sources
    (distinct domains). Flags single-sourced/conflicting instead of asserting them —
    intermediate verification that catches a wrong plan/finding BEFORE synthesis.
    claims = [{"claim": str, "sources": [{"url","snippet"} | url, ...]}]."""
    return research_core.verify_claims(claims, min_sources)


@mcp.tool()
def synthesize(question: str, verified_findings: list, plan: dict | None = None) -> dict:
    """Compile a structured, CITATION-BACKED report from verified findings, labeling
    well-supported vs single-sourced vs conflicting, preserving quotes/code verbatim,
    and ending with confidence + gaps. Degrades (no chat model) to a deterministic
    cited bullet list — still every-claim-to-a-URL, never invented."""
    return research_core.synthesize(question, verified_findings, plan)


@mcp.tool()
def deep_research(question: str, max_loops: int = 3, max_total_sources: int = 8,
                  category: str | None = None, compound: bool = True) -> dict:
    """End-to-end deep research: plan -> (develop -> explore -> verify) x bounded
    loops -> citation-backed synthesis. Single-threaded (no overspawning), bounded
    by max_loops + source cap + wall-clock budget. Compounds the final brief + key
    entities into RAG/KG so a later related run starts ahead. Fully sovereign."""
    return research_core.deep_research(question, max_loops, max_total_sources, category, compound)


# ── Stage 1: structured source fan-out (alongside the SearXNG web layer) ──────
@mcp.tool()
def multi_source_search(query: str) -> dict:
    """Structured source fan-out: classify the query -> route to the right free
    APIs (arXiv / Semantic Scholar / GitHub / HN / Stack Exchange) with bounded
    per-source budgets -> RRF-fuse the ranked lists. NOT load-bearing — every
    structured source degrades to empty and the SearXNG web layer (explore /
    deep_research) always answers. Returns fused candidates + per-source status."""
    return sources.source_fanout(query)


@mcp.tool()
def classify_query(query: str) -> dict:
    """Lightweight keyword router: maps a query to a source set + per-source budget
    (crypto/protocol, applied-ML, library-how-to, or general). Always includes
    searxng as the catch-all. Returns the chosen category, sources, and budgets."""
    return sources.classify_query(query)


@mcp.tool()
def arxiv_search(query: str, days_back: int | None = None,
                 categories: list | None = None, limit: int = 8) -> dict:
    """arXiv Atom API (keyless). days_back is OPTIONAL — omit it to reach seminal
    work (no 90-day window). categories targets cs.CR / cs.LG / cs.DC / cs.AI etc.
    Degrades to an error string if arXiv is unreachable."""
    return sources.arxiv_search(query, days_back, categories, limit)


@mcp.tool()
def semantic_scholar_search(query: str, limit: int = 10) -> dict:
    """Semantic Scholar relevance search (keyless 5k/5min pool). Returns papers
    with abstracts, authors, year, and citation counts. Attribution required when
    displayed. Pair with semantic_scholar_citations to map a topic's canon+frontier."""
    return sources.semantic_scholar_search(query, limit)


@mcp.tool()
def semantic_scholar_citations(paper_id: str, direction: str = "references",
                               limit: int = 25) -> dict:
    """Citation-graph traversal. direction='references' -> backward (what this
    paper cites -> seminal); 'citations' -> forward (what cites it -> frontier).
    paper_id accepts S2 id, 'arXiv:NNNN.NNNNN', 'DOI:...'. The feature that turns
    search into 'find the canonical + latest work on a topic'."""
    return sources.semantic_scholar_citations(paper_id, direction, limit)


@mcp.tool()
def github_search(query: str, search_type: str = "repositories", limit: int = 10) -> dict:
    """GitHub REST search over repositories / code / issues. Presence-gated on
    GITHUB_TOKEN — absent => no-op {"skipped": true} (web layer still answers).
    Reaches the specific repo/code/issue that answers a question, not just trends."""
    return sources.github_search(query, search_type, limit)


@mcp.tool()
def hn_search(query: str, limit: int = 10, tags: str = "story") -> dict:
    """Hacker News search via Algolia (keyless). Practitioner signal — what people
    actually adopt/discuss. Degrades to an error string if Algolia is unreachable."""
    return sources.hn_search(query, limit, tags)


@mcp.tool()
def stackexchange_search(query: str, site: str = "stackoverflow", limit: int = 10) -> dict:
    """Stack Exchange Q&A search (keyless 300/day; STACKEXCHANGE_KEY -> 10k/day).
    Vote/tag-ranked answers; routed for library/how-to queries. Degrades cleanly."""
    return sources.stackexchange_search(query, site, limit)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
