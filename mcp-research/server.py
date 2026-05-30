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
import corpus
import extract
import rank
import kg_provenance
import verify_gate

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
                         **research_core.stats(), "sources": sources.source_stats(),
                         "corpus": corpus.corpus_stats(), "extract": extract.extract_stats(),
                         "rank": rank.rank_stats(), "kg_provenance": kg_provenance.kg_provenance_stats(),
                         "verify_gate": verify_gate.verify_gate_stats()})


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


# ── Stage 2: crypto / standards adapters (keyless; the domain edge) ───────────
@mcp.tool()
def ethresearch_search(query: str, limit: int = 8) -> dict:
    """Search ethresear.ch (Ethereum research forum, Discourse) — NO auth, public
    read via .json. Returns frontier-research topics with blurbs + canonical URLs.
    Use ethresearch_topic to pull a topic's full post text."""
    return sources.ethresearch_search(query, limit)


@mcp.tool()
def ethresearch_topic(topic_id: int, slug: str = "") -> dict:
    """Fetch one ethresear.ch topic's FULL concatenated post text (no auth)."""
    return sources.ethresearch_topic(topic_id, slug)


@mcp.tool()
def eip_erc(query: str, limit: int = 6) -> dict:
    """Read ethereum/EIPs + ethereum/ERCs FULL spec text. Naming a number
    (EIP-4844, ERC-20) fetches the raw markdown KEYLESS with front-matter parsed
    (status/type/author/created). The canonical spec, not a blog summary."""
    return sources.eip_erc(query, limit)


@mcp.tool()
def ietf_rfc(query: str, limit: int = 5) -> dict:
    """IETF RFC full text (keyless, RFC-Editor). Naming an RFC number fetches its
    full text. Routed only when a query mentions rfc/ietf (optional per spec)."""
    return sources.ietf_rfc(query, limit)


# ── Stage 3: on-disk corpus + provenance + lazy distillation ──────────────────
@mcp.tool()
def ingest_research(namespace: str, source_type: str, content: str,
                    meta: dict | None = None, index: bool = True) -> dict:
    """Write FULL untruncated content to the on-disk markdown corpus
    (corpus/{namespace}/{source_type}/{slug}.md with YAML front-matter provenance)
    AND index the full text into the hybrid RAG store. NO distill-on-ingest — the
    technical nuance is preserved; distillation happens lazily at query time. Each
    RAG chunk resolves back to its corpus file. meta: source_url/title/authors/date/
    retrieval_query/citation_count/authority_score/session_id."""
    return corpus.ingest_research(namespace, source_type, content, meta, index)


@mcp.tool()
def distill_for_query(query: str, chunks: list, source_type: str = "web",
                      max_tokens: int = 1500) -> dict:
    """Lazily distill ONLY the retrieved chunks, at QUERY time. Dense technical
    sources (arxiv/semantic_scholar/eip_erc/ietf_rfc/audit) route to cheap-cloud
    (DeepSeek via conductor) when RESEARCH_CLOUD_DISTILL is on; else local Qwen.
    Degrades to raw chunk concatenation with no model — fully sovereign."""
    return corpus.distill_for_query(query, chunks, source_type, max_tokens)


@mcp.tool()
def resolve_source(source: str) -> dict:
    """Resolve a RAG chunk's `source` (a corpus relpath) back to its backing on-disk
    document: full content + parsed front-matter provenance. The seam the Stage-5
    verify gate uses to map a claim -> the exact stored chunk it came from."""
    return corpus.resolve_source(source)


# ── Stage 4: extraction ladder + dedup/authority/citation-graph ───────────────
@mcp.tool()
def extract_url(url: str, prefer: list | None = None) -> dict:
    """Extraction ladder: Trafilatura (fast, static) -> Crawl4AI (JS, via mcp-docs)
    -> Jina Reader (blocked/complex/PDF). Picks the order by page type and falls
    through on failure/empty. Returns markdown + which rung produced it + attempts."""
    return extract.extract_url(url, prefer)


@mcp.tool()
def semantic_dedup(items: list, threshold: float = 0.92) -> dict:
    """Collapse NEAR-duplicate sources by embedding cosine (not just URL/n-gram),
    keeping the most AUTHORITATIVE instance of each cluster — so paraphrased SEO
    mirrors don't dominate. Degrades to n-gram Jaccard if embeddings are down."""
    return rank.semantic_dedup(items, threshold)


@mcp.tool()
def authority_rank(items: list) -> dict:
    """Rank sources by composite authority = domain authority + log(citation_count)
    + recency. Surfaces an arXiv primary over a blog summary; anchors to seminal
    work while rewarding recency. Returns items sorted with the score annotated."""
    return {"ok": True, "ranked": rank.authority_rank(items)}


@mcp.tool()
def citation_edges(paper: dict, refs: list | None = None, cites: list | None = None) -> dict:
    """Turn a paper + its Semantic Scholar references (backward) / citations
    (forward) into normalized {src, rel:'cites', dst} edges with provenance, ready
    to become KG edges in Stage 5. Pure transform."""
    return rank.citation_edges(paper, refs, cites)


# ── Stage 5: KG provenance + decomposed verification gate ─────────────────────
@mcp.tool()
def kg_add_episode(namespace: str, summary: str, source_id: str,
                   entities: list | None = None, edges: list | None = None) -> dict:
    """Land a finished research finding into the KG: an episode entity + its
    entities + fact edges, all carrying source_id + ingested_at (provenance) and
    optional valid_from/valid_until (temporal validity). Degrades if the KG is down."""
    return kg_provenance.add_episode(namespace, summary, source_id, entities, edges)


@mcp.tool()
def kg_add_fact_edge(a: str, rel: str, b: str, source_id: str,
                     valid_from: str | None = None, valid_until: str | None = None) -> dict:
    """Record a fact edge (a)-[rel]->(b) with its source_id + temporal validity. rel
    must be one of cites/supersedes/implements/audits/contradicts/authored_by — an
    invented relation is rejected, not stored."""
    return kg_provenance.add_fact_edge(a, rel, b, source_id, valid_from, valid_until)


@mcp.tool()
def kg_ingest_citation_edges(edges: list, source_id: str) -> dict:
    """Bulk-record citation_edges() output as `cites` fact edges carrying source_id
    (the Stage-4 citation graph -> KG)."""
    return kg_provenance.ingest_citation_edges(edges, source_id)


@mcp.tool()
def kg_mark_superseded(old: str, new: str, source_id: str, as_of: str | None = None) -> dict:
    """Mark `old` superseded by `new` (fast-moving fields): records new-[supersedes]
    ->old and stamps old.valid_until so the graph says which is current — instead of
    silently keeping both."""
    return kg_provenance.mark_superseded(old, new, source_id, as_of)


@mcp.tool()
def verify_findings(findings: list, min_sources: int = 2) -> dict:
    """Decomposed verification gate (grounding, not generation): each claim's
    sources are RESOLVED to stored chunks and the claim is ENTAILMENT-checked against
    them; ≥2 independent supporting domains => well-supported; contradictions =>
    'conflicting', surfaced with BOTH citations (never averaged). findings =
    [{"claim", "sources":[{source_id|url|snippet, source_type?}]}]."""
    return verify_gate.verify_findings(findings, min_sources)


@mcp.tool()
def verify_claim(claim: str, sources: list, min_sources: int = 2) -> dict:
    """Verify ONE claim by decomposed retrieval — resolve each source to its stored
    chunk, entail, count independent support. Returns status + resolvable source IDs
    + per-source verdicts. Flags unsupported/unresolvable rather than asserting."""
    return verify_gate.verify_claim(claim, sources, min_sources)


@mcp.tool()
def decompose_question(question: str, hyde: bool = False) -> dict:
    """Echo-chamber fix: break a question into complementary sub-questions, each with
    diverse search paraphrases + per-source query syntax (arXiv fields != GitHub
    qualifiers != web), optional HyDE. The searches then fuse via RRF. Degrades to
    deterministic variants with no model."""
    return verify_gate.decompose_question(question, hyde)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
