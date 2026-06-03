# The search & research engines — architecture & code

How hermes-max searches the world. There are **two distinct "search" engines**,
plus a shared web-fetch substrate:

| Engine | Port | "Search" over… | Entry |
|---|---|---|---|
| **mcp-research** | 9110 | the live web + a compounding corpus | `deep_research()` |
| **mcp-search** | 9108 | the *solution space* (verifier-guided code generation / best-of-N) | `generate_and_select()` / `parallel_draft()` |
| mcp-docs | 9109 | the fetch/crawl substrate (SearXNG → Crawl4AI → trafilatura) | `search_docs` / `fetch_clean` |

This doc explains the deep-research engine in depth (query formulation, fan-out,
parallelism, dedup for unique directions, convergence, inference), the web search
methods, and the verifier-guided solution search. **Verbatim source is in
Appendix A.**

The governing philosophy (stated at the top of `research_core.py`): it is *not* a
framework import — the value-bearing **patterns** are built as native, bounded,
deterministic-first MCP tools, engineered against the four named failure modes of
open deep-research:

```
echo-chamber retrieval     → query diversity + URL & n-gram content dedup
source-quality / SEO bias  → authority-aware re-ranking (primary > content farm)
planning hallucination     → external checkable PLAN + intermediate verify_claims
sub-agent overspawning     → hard per-query / per-loop / total-source caps
```

Every backend degrades gracefully: SearXNG down → explore returns nothing;
Crawl4AI down → trafilatura; reranker unset → authority heuristic only;
`$VLLM_BASE_URL` unset → deterministic (non-LLM) plan/queries/synthesis.

---

## 1. The deep-research pipeline (mcp-research)

`deep_research()` (`research_core.py:847`) is the orchestrator:

```
plan → (develop_queries → explore → ) × bounded loops → extract → verify → synthesize → cite-audit → compound
```

But before any of that runs, a **five-gate ladder** decides whether to research at
all — each gate deterministic (no model self-judgment):

```
deep_research(question)
  0. PARAMETRIC pre-screen   classify_research_need()  research_core.py:757  — textbook algo / "how does X" → BLOCK (implement from knowledge)
  1. CORPUS-FIRST gate       corpus_hit_check via RAG  research_core.py:881  — ≥2 chunks > sim 0.75 → answer from corpus, SKIP cascade
  2. BUDGET + COOLDOWN gate  session_state.research_gate  :921             — recent call / session budget spent → BLOCK
  3. EXHAUSTION-FIRST gate   session_state.lighter_tools_attempted  :947    — Tiers 1-3 not tried on a related query → BLOCK
  4. → the cascade
```

The ladder encodes a tool hierarchy: `Tier0 parametric (no tool) → Tier1
search_code/corpus → Tier2 fetch_clean → Tier3 research_topic → Tier4
deep_research`. Tier 4 only fires when the cheaper tiers are exhausted — the
anti-reflexive-research design.

### 1.1 Query formulation (inference)

Two LLM stages turn a question into diverse search directions, each with a
deterministic fallback:

- **`plan_research(question)`** (`:327`) — system prompt `_PLAN_SYS` (`:319`):
  *"Decompose into 2-5 focused, COMPLEMENTARY sub-goals (not overlapping) + a
  roadmap."* Returns strict JSON; written to an external `PLAN.md` (planning
  hallucination is most damaging, so the plan is made inspectable). No LLM →
  the question is its own single sub-goal.
- **`develop_queries(subgoal, n=4)`** (`:392`) — `_QUERY_SYS` (`:369`):
  *"Generate diverse, COMPLEMENTARY queries — vary abstraction/phrasing/angle so
  they retrieve DIFFERENT sources, NOT near-duplicates."* No LLM → deterministic
  angle variants `[subgoal, "{subgoal} documentation", "{subgoal} example",
  "{subgoal} best practices"]`.
- **`verify_gate.decompose_question()`** — optional echo-chamber breaker: one
  question → sub-questions + paraphrases + **per-source query-dialect
  translation** (web vs arXiv vs GitHub vs Semantic Scholar syntax) + optional
  **HyDE** (hypothetical-document embeddings for dense retrieval).

**Diversity is enforced, not assumed** — `_dedup_queries()` (`:377`) drops any
query whose 2-shingle Jaccard ≥ `QUERY_DUP_THRESHOLD` (0.8) against one already
kept. This is the first of four dedup layers; its job is *unique search
directions* (so the fan-out doesn't recycle phrasings).

### 1.2 Fan-out — how one question becomes many searches

The fan-out is **bounded breadth, not unbounded parallelism** (the overspawning
guard). One question expands as:

```
question
  └─ subgoals[]            (≤ MAX_SUBGOALS = 5,        from plan_research)
       └─ queries[]        (≤ QUERIES_PER_SUBGOAL = 4, from develop_queries, deduped)
            └─ candidates[] (search limit ≈ per_query×3)
                 └─ sources (≤ MAX_SOURCES_PER_QUERY = 3, ≤ MAX_TOTAL_SOURCES = 8 total)
```

The `deep_research` loop (`:976`) walks `max_loops` (≤ 3), each loop taking the
next subgoal round-robin, developing queries, and exploring — accumulating into a
shared `all_sources` capped at `MAX_TOTAL_SOURCES`, bounded by a
`WALL_BUDGET_S = 600` wall-clock and a "no new sources" early stop.

**Crucially, the in-run loop is single-threaded and sequential** (`research_core.py:1-16`
and the `for loop` at `:976`) — a *deliberate* anti-overspawn choice, not a
limitation. The breadth is the fan-out; depth across loops is the convergence.

**Cross-run fan-out — Banyan (`banyan.py`).** Banyan is *not* an in-request tree;
it is the **long-horizon direction selector** across sessions. It keeps per-
namespace state (`visit_count`, `utility`, `gain_history`, `centroid`, …) and
picks the next research direction with a **UCB1 explore-exploit** score
(`banyan.py:203`):

```
U_i = utility·priority  +  c·sqrt(ln N / n_i)         c = BANYAN_UCB_C = 1.414
      └ exploitation ┘     └ exploration bonus ┘       unvisited n_i=0 → ∞ (explore first)
```

It also **detects saturation** (stop investing in a direction) via two signals:
embedding **drift** (new texts cosine ≥ 0.95 to the namespace centroid →
retreading) and **marginal-gain decline** (last-10 gains trending down, mean below
a floor) — only once ≥10 samples exist (empty-base guard). Saturation surfaces to
the operator rather than silently looping.

### 1.3 Web search methods (sources.py + the mcp-docs substrate)

Two layers of "how we hit the web":

**(a) The generic substrate (via mcp-docs, `research_core.py:225/234`):**
- `_search(query)` → `search_docs` → **SearXNG** (self-hosted meta-search;
  `searXNG.sh`) returns candidate `{title,url,content}`.
- `_fetch(url)` → `fetch_clean` → an **extraction ladder**: **Crawl4AI** (JS-
  rendering, `crawl4ai.sh`) → **trafilatura** → **Jina** reader — returns clean
  markdown (`extract.py`).

**(b) Structured source adapters (`sources.py`)** — bounded, keyless/presence-
gated readers over free public APIs, each normalized to one `_item` shape
(`sources.py:102`) and routed by `classify_query()`:

| Adapter | Endpoint | Auth | Signal |
|---|---|---|---|
| arXiv | export.arxiv.org/api/query (Atom) | keyless | papers, category-filtered |
| Semantic Scholar | api.semanticscholar.org graph | optional key | **citation-graph traversal** (refs=backward, citations=forward) |
| GitHub | api.github.com/search/{repos,code,issues} | PAT-gated | code/issues |
| Hacker News | hn.algolia.com | keyless | practitioner signal |
| Stack Exchange | api.stackexchange.com | keyless 300/day | Q&A / how-to |
| ethresear.ch | ethresear.ch/search.json | keyless | crypto research frontier |
| EIP/ERC, IETF RFC | raw GitHub / rfc-editor.org | keyless | canonical spec text |

`source_fanout()` queries the routed adapters and **fuses** their ranked lists
with **Reciprocal Rank Fusion** (`RRF_K = 60`): `score(doc) = Σ 1/(k + rank)` —
the convergence operator for heterogeneous source lists.

### 1.4 Parallelism & concurrency

- **Deep-research loop: sequential by design** (anti-overspawn). The only
  concurrency in the request path is the **`@_threaded` decorator**
  (`server.py:62`) running each MCP tool on a worker thread (via
  `asyncio.to_thread`) so a multi-minute research call never blocks `/health` or
  other requests; and `_run_coro` (`research_core.py:183`) which completes a
  coroutine on a fresh-loop worker thread when FastMCP already holds the event
  loop (the "smoke passes, live fails" trap, fixed).
- **mcp-search: genuinely parallel** (see §2) — `ThreadPoolExecutor`
  (`search_core.py:102`) verifies candidates concurrently; `parallel_draft` fans
  one draft per inference family.

### 1.5 Deduplication — keeping directions unique & convergent

Four dedup layers, each at a different granularity, are what make the fan-out
*diverge* and the results *converge cleanly*:

| Layer | Where | Mechanism | Purpose |
|---|---|---|---|
| **Query** | `research_core.py:377` | 2-shingle Jaccard ≥ 0.8 | unique *search directions* (no recycled phrasing) |
| **URL** | `research_core.py:452` | normalized netloc+path set (`_normalize_url`) | break the echo chamber (same page from many queries) |
| **Content** | `research_core.py:464` | 3-shingle Jaccard ≥ 0.85 across *different* URLs | near-duplicate content from mirror sites |
| **Semantic** | `rank.py:semantic_dedup` | embedding cosine ≥ 0.92, keep most authoritative | paraphrased duplicates; falls back to n-gram |

Plus **authority filtering** (`:456`): once a primary source (authority ≥ 2) is
held, a known content farm (authority 0) for the same direction is dropped.

### 1.6 Ranking & convergence

The fan-out converges through ranking then verification:

- **Authority score** (`research_core.py:125`, `authority_score`): TLD/domain
  heuristic 0–3 (`.gov/.edu`, arxiv/github/docs → 3; w3schools/medium/quora → 0).
- **Cross-encoder rerank** (`_rerank`, `:276`): optional `RERANK_BASE_URL`
  (shared with the RAG reranker, `serve-rerank.sh`) re-orders candidates on top
  of authority.
- **`rank.py`** composes `authority + log(citations)/3 + recency` and exposes
  `authority_rank` / `semantic_dedup` / `citation_edges`. **`relevance.py`**
  applies a query-token-containment + authority floor filter.
- **`verify_claims()`** (`:517`) is the convergence gate: each claim must be
  backed by **≥ MIN_INDEPENDENT_SOURCES (2) distinct domains** ("one vote per
  domain → independence"); with an LLM it entails each (claim, source) pair,
  otherwise it counts independent-domain support deterministically. Output:
  `well-supported` / `single-sourced` / `conflicting`.

### 1.7 Synthesis, citation audit, compounding

- **`synthesize()`** (`:591`, `_SYNTH_SYS`) — every claim inline-cited `[n]`,
  labelled well-supported vs single-sourced vs conflicting, with a **gap note**
  (not a confidence-as-retry signal — the agent proceeds and notes gaps).
  No LLM → a deterministic, still-fully-cited bullet list.
- **`_citation_verify()`** (`:803`, Anthropic's CitationAgent pattern) — a final
  audit of each report claim against the sources, routed to the conductor's
  cheap-cloud steer tier first, local fallback; flags unsupported claims (never
  rewrites them).
- **Compounding** (`:1026`) — the report is ingested into RAG/KG so a later
  related run hits the **corpus-first gate** instead of researching again.
  `corpus.py` persists markdown with YAML provenance + lazy query-time
  distillation; `kg_provenance.py` writes episodes + temporal-validity edges
  (`cites`/`supersedes`/`contradicts`/…).

---

## 2. The solution-space search (mcp-search)

A *different* "search": over candidate **code solutions**, guided by a verifier
(tests). `search_core.py`.

- **`generate_and_select(task_spec, …)`** (`:237`) — **RASC interleaved
  early-exit**: generate one candidate, verify it immediately, and **stop the
  moment one passes** (`early_exit=True`) — ~85% sample savings vs generating N
  then ranking. The "search" is best-of-N with an early-accept.
- **`parallel_draft(…)`** (`:330`) — **cross-family diversity**: fan out **one
  draft per present inference pool family** (Cerebras / Groq / DeepInfra / …),
  verify all via a `ThreadPoolExecutor` (`:102`), and pick the first green. This
  is the genuinely parallel search, exploiting provider diversity for varied
  attempts.

Both are verifier-guided (the tests are the scoring function) — the convergence
criterion is "passes the gate," not a learned score.

---

## 3. Inference points (every model/embedding/reranker call)

Deterministic-first: every inference has a graceful non-LLM fallback.

| Step | File:line | Role / temp | Fallback |
|---|---|---|---|
| Plan decompose | research_core.py:338 | planner, 0.2 | single sub-goal |
| Query diversity | research_core.py:398 | query-gen, 0.5 | angle variants |
| Claim extraction | research_core.py:699 | extractor, 0.1 | one claim/source |
| Entailment (verify) | research_core.py:506 | fact-check, 0 | counts as unchecked support |
| Synthesis | research_core.py:614 | synthesizer, 0.2 | cited bullet list |
| Citation audit | research_core.py:820 | conductor-steer → local, 0 | skip |
| Decompose / HyDE | verify_gate.py | echo-breaker, 0.4 | deterministic variants |
| Cross-encoder rerank | research_core.py:276 | `RERANK_BASE_URL` | authority heuristic |
| Embeddings (semantic dedup, banyan drift) | rank.py / banyan.py | `EMBED_BASE_URL` | n-gram Jaccard |
| Code generation | search_core.py | inference pools (per family) | — |

---

## 4. Component interrelation (one diagram)

```
                         ┌───────────────── mcp-research (9110) ─────────────────┐
question ──gates──► plan_research ─LLM─► subgoals
                         │                   │ round-robin × loops (≤3, sequential)
                         │            develop_queries ─LLM─► queries ─[Jaccard dedup]─► unique directions
                         │                   │
                         │              explore ──► _search ─► mcp-docs(9109) ─► SearXNG ─► candidates
                         │                   │         │                          (Crawl4AI→trafilatura→Jina = fetch_clean)
                         │                   │     sources.py adapters (arXiv/S2/GH/HN/SE/…) ─[RRF k=60]─┐
                         │                   ▼                                                          │
                         │           [URL dedup]→[content dedup]→[authority filter]→[rerank]◄───────────┘
                         │                   │  (embed semantic-dedup via EMBED_BASE_URL; rerank via RERANK_BASE_URL)
                         │              accumulate ≤ 8 sources
                         │                   ▼
                         │   extract_claims ─LLM─► verify_claims (≥2 independent domains) ─LLM─► synthesize ─LLM─► citation audit ─cloud/local─►
                         │                   ▼
                         │            compound → RAG(9102)+KG(9103) ──feeds──► corpus-first gate (next run)
                         │   Banyan (UCB1 + saturation) selects the next direction ACROSS runs
                         └────────────────────────────────────────────────────────────────────┘

mcp-search (9108):  task_spec ─► generate_and_select (RASC early-exit)  /  parallel_draft (one per inference family, ThreadPoolExecutor) ─► first green
```

One question fans out (subgoals × diverse queries × routed sources), each path is
kept **unique** (4 dedup layers), and the paths **converge** through authority
rank → cross-encoder → independent-source verification → cited synthesis →
citation audit → corpus/KG — bounded at every step, deterministic when no model
is present.

---

## 5. File map

| File | Role |
|---|---|
| `mcp-research/research_core.py` | the orchestrator: gates, plan, develop, explore, dedup, verify, synthesize, cite-audit, compound |
| `mcp-research/sources.py` | structured web-source adapters + RRF fusion + `classify_query` routing |
| `mcp-research/banyan.py` | cross-run direction selection (UCB1) + saturation detection |
| `mcp-research/rank.py` | authority rank + semantic dedup + citation edges |
| `mcp-research/relevance.py` | query-token + authority relevance filter |
| `mcp-research/extract.py` | the extraction ladder (trafilatura/Crawl4AI/Jina) |
| `mcp-research/corpus.py` | on-disk corpus + lazy distillation + corpus-first feed |
| `mcp-research/verify_gate.py` | decomposed verification, HyDE, per-source query dialects |
| `mcp-research/session_state.py` | budget / cooldown / exhaustion gates |
| `mcp-research/kg_provenance.py` | KG episodes + temporal-validity edges |
| `mcp-research/server.py` | the MCP tool surface (9110) |
| `mcp-search/search_core.py` | verifier-guided solution search (RASC + cross-family) |
| `mcp-search/server.py` | the MCP tool surface (9108) |

---

## Appendix A — verbatim source

*Generated from the repo below this line.*


### A.1  mcp-research — tool surface & orchestrator

#### `mcp-research/server.py`
```python
"""mcp-research — SOTA local deep-research (port 9110).

Transport: streamable-http on $MCP_RESEARCH_PORT (default 9110), path /mcp.
Health:    GET /health (LIVENESS — fast, no upstream calls, the UP/DOWN signal).
           GET /ready  (READINESS — backends + bounds; informational, may probe).

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

import asyncio
import functools
import os
from typing import Any, Callable

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
import banyan

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


def _threaded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Run a sync tool body on a worker thread so it NEVER blocks the event loop.

    FastMCP (1.27) calls sync @mcp.tool() handlers directly in the single event-
    loop thread, so a long tool (deep_research runs for minutes) stalls EVERY
    other request — including GET /health. That is the real reason mcp-research
    showed DOWN while it was alive and actively serving the agent: status.sh's
    `curl /health` timed out against a loop busy inside deep_research. Offloading
    the body with asyncio.to_thread keeps the loop free to answer liveness and
    concurrent tool calls. functools.wraps preserves the typed signature so
    FastMCP still derives the correct input schema; the body now runs in a thread
    with no running loop, so research_core's MCP-to-MCP calls work too."""
    @functools.wraps(fn)
    async def _aw(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """LIVENESS — is this process up and answering HTTP? Returns 200 immediately
    with NO upstream network calls (sub-10ms). This is the UP/DOWN signal used by
    status.sh / healthcheck.sh: a live server must NEVER show DOWN because a
    dependency (SearXNG / Crawl4AI / the chat model / a source API) is slow or
    down. Dependency status lives at /ready (informational). `?deep=1` forwards to
    the readiness check for convenience."""
    if request.query_params.get("deep", "").lower() in ("1", "true", "yes"):
        return await ready(request)
    return JSONResponse({"status": "ok", "server": "mcp-research", "port": PORT})


@mcp.custom_route("/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    """READINESS — the rich, INFORMATIONAL dependency snapshot (sources, docs_up,
    chat-model endpoint, corpus state, rank/extract/verify/banyan config). MAY do
    bounded upstream probes, so it can be slow; a failing dependency here is a
    WARNING (status.sh shows it as a readiness note), never DOWN. The agent can
    still call every tool — individual tools degrade per the graceful-degradation
    matrix if a dependency is actually down."""
    return JSONResponse({"status": "ok", "server": "mcp-research", "port": PORT,
                         **research_core.stats(), "sources": sources.source_stats(),
                         "corpus": corpus.corpus_stats(), "extract": extract.extract_stats(),
                         "rank": rank.rank_stats(), "kg_provenance": kg_provenance.kg_provenance_stats(),
                         "verify_gate": verify_gate.verify_gate_stats(), "banyan": banyan.banyan_stats()})


@mcp.tool()
@_threaded
def plan_research(question: str) -> dict:
    """Decompose a research question into 2-5 complementary sub-goals + an ordered
    roadmap, written to external PLAN.md state so the plan itself is checkable
    (planning hallucination is most damaging here). Degrades to a single-sub-goal
    plan without the chat model."""
    return research_core.plan_research(question)


@mcp.tool()
@_threaded
def develop_queries(subgoal: str, n: int = 4) -> dict:
    """Generate diverse, COMPLEMENTARY search queries for a sub-goal (varied
    abstraction/angle), deduped by n-gram similarity — the direct counter to
    echo-chamber retrieval. Returns near-duplicate-free queries."""
    return research_core.develop_queries(subgoal, n)


@mcp.tool()
@_threaded
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
@_threaded
def verify_claims(claims: list, min_sources: int = 2) -> dict:
    """Cross-check each material claim against >= min_sources INDEPENDENT sources
    (distinct domains). Flags single-sourced/conflicting instead of asserting them —
    intermediate verification that catches a wrong plan/finding BEFORE synthesis.
    claims = [{"claim": str, "sources": [{"url","snippet"} | url, ...]}]."""
    return research_core.verify_claims(claims, min_sources)


@mcp.tool()
@_threaded
def synthesize(question: str, verified_findings: list, plan: dict | None = None) -> dict:
    """Compile a structured, CITATION-BACKED report from verified findings, labeling
    well-supported vs single-sourced vs conflicting, preserving quotes/code verbatim,
    and ending with confidence + gaps. Degrades (no chat model) to a deterministic
    cited bullet list — still every-claim-to-a-URL, never invented."""
    return research_core.synthesize(question, verified_findings, plan)


@mcp.tool()
@_threaded
def deep_research(question: str, max_loops: int = 3, max_total_sources: int = 8,
                  category: str | None = None, compound: bool = True) -> dict:
    """End-to-end deep research: plan -> (develop -> explore -> verify) x bounded
    loops -> citation-backed synthesis. Single-threaded (no overspawning), bounded
    by max_loops + source cap + wall-clock budget. Compounds the final brief + key
    entities into RAG/KG so a later related run starts ahead. Fully sovereign."""
    return research_core.deep_research(question, max_loops, max_total_sources, category, compound)


@mcp.tool()
@_threaded
def note_lighter_tools_attempted(question: str) -> dict:
    """Tell the rationing layer you ALREADY tried the cheaper tools (search_code /
    fetch_clean / research_topic) for `question` and found them insufficient — the
    explicit precondition that lets deep_research escalate (R-Stage 3 exhaustion
    gate). Use this only when you genuinely tried the lighter tiers; deep_research
    will otherwise refuse until a related lighter-tool call is on record."""
    import session_state
    session_state.note_lighter_tools_attempted(question)
    return {"ok": True, "noted": True, "question": question}


# ── Stage 1: structured source fan-out (alongside the SearXNG web layer) ──────
@mcp.tool()
@_threaded
def multi_source_search(query: str) -> dict:
    """Structured source fan-out: classify the query -> route to the right free
    APIs (arXiv / Semantic Scholar / GitHub / HN / Stack Exchange) with bounded
    per-source budgets -> RRF-fuse the ranked lists. NOT load-bearing — every
    structured source degrades to empty and the SearXNG web layer (explore /
    deep_research) always answers. Returns fused candidates + per-source status."""
    return sources.source_fanout(query)


@mcp.tool()
@_threaded
def classify_query(query: str) -> dict:
    """Lightweight keyword router: maps a query to a source set + per-source budget
    (crypto/protocol, applied-ML, library-how-to, or general). Always includes
    searxng as the catch-all. Returns the chosen category, sources, and budgets."""
    return sources.classify_query(query)


@mcp.tool()
@_threaded
def arxiv_search(query: str, days_back: int | None = None,
                 categories: list | None = None, limit: int = 8) -> dict:
    """arXiv Atom API (keyless). days_back is OPTIONAL — omit it to reach seminal
    work (no 90-day window). categories targets cs.CR / cs.LG / cs.DC / cs.AI etc.
    Degrades to an error string if arXiv is unreachable."""
    return sources.arxiv_search(query, days_back, categories, limit)


@mcp.tool()
@_threaded
def semantic_scholar_search(query: str, limit: int = 10) -> dict:
    """Semantic Scholar relevance search (keyless 5k/5min pool). Returns papers
    with abstracts, authors, year, and citation counts. Attribution required when
    displayed. Pair with semantic_scholar_citations to map a topic's canon+frontier."""
    return sources.semantic_scholar_search(query, limit)


@mcp.tool()
@_threaded
def semantic_scholar_citations(paper_id: str, direction: str = "references",
                               limit: int = 25) -> dict:
    """Citation-graph traversal. direction='references' -> backward (what this
    paper cites -> seminal); 'citations' -> forward (what cites it -> frontier).
    paper_id accepts S2 id, 'arXiv:NNNN.NNNNN', 'DOI:...'. The feature that turns
    search into 'find the canonical + latest work on a topic'."""
    return sources.semantic_scholar_citations(paper_id, direction, limit)


@mcp.tool()
@_threaded
def github_search(query: str, search_type: str = "repositories", limit: int = 10) -> dict:
    """GitHub REST search over repositories / code / issues. Presence-gated on
    GITHUB_TOKEN — absent => no-op {"skipped": true} (web layer still answers).
    Reaches the specific repo/code/issue that answers a question, not just trends."""
    return sources.github_search(query, search_type, limit)


@mcp.tool()
@_threaded
def hn_search(query: str, limit: int = 10, tags: str = "story") -> dict:
    """Hacker News search via Algolia (keyless). Practitioner signal — what people
    actually adopt/discuss. Degrades to an error string if Algolia is unreachable."""
    return sources.hn_search(query, limit, tags)


@mcp.tool()
@_threaded
def stackexchange_search(query: str, site: str = "stackoverflow", limit: int = 10) -> dict:
    """Stack Exchange Q&A search (keyless 300/day; STACKEXCHANGE_KEY -> 10k/day).
    Vote/tag-ranked answers; routed for library/how-to queries. Degrades cleanly."""
    return sources.stackexchange_search(query, site, limit)


# ── Stage 2: crypto / standards adapters (keyless; the domain edge) ───────────
@mcp.tool()
@_threaded
def ethresearch_search(query: str, limit: int = 8) -> dict:
    """Search ethresear.ch (Ethereum research forum, Discourse) — NO auth, public
    read via .json. Returns frontier-research topics with blurbs + canonical URLs.
    Use ethresearch_topic to pull a topic's full post text."""
    return sources.ethresearch_search(query, limit)


@mcp.tool()
@_threaded
def ethresearch_topic(topic_id: int, slug: str = "") -> dict:
    """Fetch one ethresear.ch topic's FULL concatenated post text (no auth)."""
    return sources.ethresearch_topic(topic_id, slug)


@mcp.tool()
@_threaded
def eip_erc(query: str, limit: int = 6) -> dict:
    """Read ethereum/EIPs + ethereum/ERCs FULL spec text. Naming a number
    (EIP-4844, ERC-20) fetches the raw markdown KEYLESS with front-matter parsed
    (status/type/author/created). The canonical spec, not a blog summary."""
    return sources.eip_erc(query, limit)


@mcp.tool()
@_threaded
def ietf_rfc(query: str, limit: int = 5) -> dict:
    """IETF RFC full text (keyless, RFC-Editor). Naming an RFC number fetches its
    full text. Routed only when a query mentions rfc/ietf (optional per spec)."""
    return sources.ietf_rfc(query, limit)


# ── Stage 3: on-disk corpus + provenance + lazy distillation ──────────────────
@mcp.tool()
@_threaded
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
@_threaded
def distill_for_query(query: str, chunks: list, source_type: str = "web",
                      max_tokens: int = 1500) -> dict:
    """Lazily distill ONLY the retrieved chunks, at QUERY time. Dense technical
    sources (arxiv/semantic_scholar/eip_erc/ietf_rfc/audit) route to cheap-cloud
    (DeepSeek via conductor) when RESEARCH_CLOUD_DISTILL is on; else local Qwen.
    Degrades to raw chunk concatenation with no model — fully sovereign."""
    return corpus.distill_for_query(query, chunks, source_type, max_tokens)


@mcp.tool()
@_threaded
def resolve_source(source: str) -> dict:
    """Resolve a RAG chunk's `source` (a corpus relpath) back to its backing on-disk
    document: full content + parsed front-matter provenance. The seam the Stage-5
    verify gate uses to map a claim -> the exact stored chunk it came from."""
    return corpus.resolve_source(source)


# ── Stage 4: extraction ladder + dedup/authority/citation-graph ───────────────
@mcp.tool()
@_threaded
def extract_url(url: str, prefer: list | None = None) -> dict:
    """Extraction ladder: Trafilatura (fast, static) -> Crawl4AI (JS, via mcp-docs)
    -> Jina Reader (blocked/complex/PDF). Picks the order by page type and falls
    through on failure/empty. Returns markdown + which rung produced it + attempts."""
    return extract.extract_url(url, prefer)


@mcp.tool()
@_threaded
def semantic_dedup(items: list, threshold: float = 0.92) -> dict:
    """Collapse NEAR-duplicate sources by embedding cosine (not just URL/n-gram),
    keeping the most AUTHORITATIVE instance of each cluster — so paraphrased SEO
    mirrors don't dominate. Degrades to n-gram Jaccard if embeddings are down."""
    return rank.semantic_dedup(items, threshold)


@mcp.tool()
@_threaded
def authority_rank(items: list) -> dict:
    """Rank sources by composite authority = domain authority + log(citation_count)
    + recency. Surfaces an arXiv primary over a blog summary; anchors to seminal
    work while rewarding recency. Returns items sorted with the score annotated."""
    return {"ok": True, "ranked": rank.authority_rank(items)}


@mcp.tool()
@_threaded
def citation_edges(paper: dict, refs: list | None = None, cites: list | None = None) -> dict:
    """Turn a paper + its Semantic Scholar references (backward) / citations
    (forward) into normalized {src, rel:'cites', dst} edges with provenance, ready
    to become KG edges in Stage 5. Pure transform."""
    return rank.citation_edges(paper, refs, cites)


# ── Stage 5: KG provenance + decomposed verification gate ─────────────────────
@mcp.tool()
@_threaded
def kg_add_episode(namespace: str, summary: str, source_id: str,
                   entities: list | None = None, edges: list | None = None) -> dict:
    """Land a finished research finding into the KG: an episode entity + its
    entities + fact edges, all carrying source_id + ingested_at (provenance) and
    optional valid_from/valid_until (temporal validity). Degrades if the KG is down."""
    return kg_provenance.add_episode(namespace, summary, source_id, entities, edges)


@mcp.tool()
@_threaded
def kg_add_fact_edge(a: str, rel: str, b: str, source_id: str,
                     valid_from: str | None = None, valid_until: str | None = None) -> dict:
    """Record a fact edge (a)-[rel]->(b) with its source_id + temporal validity. rel
    must be one of cites/supersedes/implements/audits/contradicts/authored_by — an
    invented relation is rejected, not stored."""
    return kg_provenance.add_fact_edge(a, rel, b, source_id, valid_from, valid_until)


@mcp.tool()
@_threaded
def kg_ingest_citation_edges(edges: list, source_id: str) -> dict:
    """Bulk-record citation_edges() output as `cites` fact edges carrying source_id
    (the Stage-4 citation graph -> KG)."""
    return kg_provenance.ingest_citation_edges(edges, source_id)


@mcp.tool()
@_threaded
def kg_mark_superseded(old: str, new: str, source_id: str, as_of: str | None = None) -> dict:
    """Mark `old` superseded by `new` (fast-moving fields): records new-[supersedes]
    ->old and stamps old.valid_until so the graph says which is current — instead of
    silently keeping both."""
    return kg_provenance.mark_superseded(old, new, source_id, as_of)


@mcp.tool()
@_threaded
def verify_findings(findings: list, min_sources: int = 2) -> dict:
    """Decomposed verification gate (grounding, not generation): each claim's
    sources are RESOLVED to stored chunks and the claim is ENTAILMENT-checked against
    them; ≥2 independent supporting domains => well-supported; contradictions =>
    'conflicting', surfaced with BOTH citations (never averaged). findings =
    [{"claim", "sources":[{source_id|url|snippet, source_type?}]}]."""
    return verify_gate.verify_findings(findings, min_sources)


@mcp.tool()
@_threaded
def verify_claim(claim: str, sources: list, min_sources: int = 2) -> dict:
    """Verify ONE claim by decomposed retrieval — resolve each source to its stored
    chunk, entail, count independent support. Returns status + resolvable source IDs
    + per-source verdicts. Flags unsupported/unresolvable rather than asserting."""
    return verify_gate.verify_claim(claim, sources, min_sources)


@mcp.tool()
@_threaded
def decompose_question(question: str, hyde: bool = False) -> dict:
    """Echo-chamber fix: break a question into complementary sub-questions, each with
    diverse search paraphrases + per-source query syntax (arXiv fields != GitHub
    qualifiers != web), optional HyDE. The searches then fuse via RRF. Degrades to
    deterministic variants with no model."""
    return verify_gate.decompose_question(question, hyde)


# ── Stage 6: Banyan content-evolution (CONTENT only — never machinery) ────────
@mcp.tool()
@_threaded
def banyan_select(c: float = 1.414) -> dict:
    """Pick the next RESEARCH direction for an unattended cycle (UCB1 is scoped to
    RESEARCH/search ONLY — do NOT use it to pick a code/build subtask; use
    build_select_subtask for that). A pending human DIRECTIVE preempts (operator
    steer); else UCB1 explore-exploit over non-saturated namespaces (unvisited get
    an infinite exploration bonus). Returns mode + chosen namespace + UCB scores."""
    return banyan.banyan_select(c)


@mcp.tool()
@_threaded
def build_select_subtask(subtasks: list[dict], in_progress: str | None = None) -> dict:
    """Pick the next BUILD/coding subtask WITHOUT UCB1 — the build loop needs
    sustained focus, so this is finish-what-you-started then dependency-order
    (BANYAN_SCOPE=research_only, the default: UCB1 governs research only, never code).
    Each subtask: {id, status:'complete'|'incomplete', deps:[ids]}. Keeps an
    in-progress incomplete subtask rather than switching to a shinier one."""
    return banyan.select_next("build", subtasks=subtasks, in_progress=in_progress)


@mcp.tool()
@_threaded
def banyan_update(namespace: str, utility_sample: float, gain: float) -> dict:
    """After a research/skill task: visit_count++, running utility (0.8 history /
    0.2 new), append marginal gain (last 20). Drives the explore-exploit balance."""
    return banyan.banyan_update(namespace, utility_sample, gain)


@mcp.tool()
@_threaded
def banyan_detect_saturation(namespace: str, new_texts: list | None = None) -> dict:
    """Two-signal saturation: embedding-drift (new research too similar to the
    corpus centroid => retreading) + marginal-gain decline. On saturation: flag,
    STOP investing, and SURFACE TO THE OPERATOR (never silently churn)."""
    return banyan.detect_saturation(namespace, new_texts)


@mcp.tool()
@_threaded
def banyan_generate_standing_tasks(namespace: str) -> dict:
    """When a namespace queue empties, generate standing RESEARCH tasks (content)
    so unattended cycles never idle (e.g. 'what's new in {ns} since {last_ingest}')."""
    return banyan.generate_standing_tasks(namespace)


@mcp.tool()
@_threaded
def banyan_set_directive(text: str, namespace: str | None = None) -> dict:
    """Operator drops a directive that preempts UCB1 on the next cycle (supervised
    steer). Absent a directive, the loop self-directs via Banyan (unattended explore)."""
    return banyan.set_directive(text, namespace)


@mcp.tool()
@_threaded
def banyan_next_action() -> dict:
    """Top of an unattended cycle: directive interrupt OR Banyan self-direction,
    with the chosen namespace's next standing task. Selection only — never runs
    research and never touches machinery."""
    return banyan.next_action()


@mcp.tool()
@_threaded
def banyan_write_skill(name: str, content: str, tasks_done: int = 0,
                       days_active: int = 0, skills_count: int = 0) -> dict:
    """Write/refine a markdown SKILL into the skill library (CONTENT). Gated by the
    maturity check (SELF_IMPROVEMENT_ENABLED + 200 tasks / 30 days / 50 skills) AND
    the machinery guard — a non-.md / machinery path is refused. Never edits tool code."""
    return banyan.write_skill(name, content, tasks_done, days_active, skills_count)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

#### `mcp-research/research_core.py`
```python
"""SOTA-pattern local deep-research: plan → develop → explore → verify → synthesize.

Free and fully sovereign. It orchestrates the EXISTING stack — SearXNG + Crawl4AI/
trafilatura (via mcp-docs), the local chat model ($VLLM_BASE_URL), and mcp-codebase-
rag / mcp-knowledge-graph for compounding — into the canonical four-stage deep-
research architecture as bounded, deterministic tools. It is NOT a framework import
(no local-deep-research / LangChain): the research shows a well-configured agent +
SearXNG beats pre-packaged frameworks, so the value-bearing PATTERNS are built here
as native MCP tools.

Engineered against the four named failure modes of open deep-research:
  * echo-chamber retrieval     -> query diversity + URL & n-gram content dedup
  * source-quality / SEO bias  -> authority-aware re-ranking (primary > content farm)
  * planning hallucination     -> external checkable PLAN + intermediate verify_claims
  * sub-agent overspawning      -> hard per-query / per-loop / total source caps

Every backend is reached over the network and degrades gracefully: SearXNG down ->
explore returns nothing (reported); Crawl4AI down -> mcp-docs falls back to
trafilatura; reranker unset -> authority-heuristic-only ranking; $VLLM_BASE_URL
unset -> deterministic (non-LLM) plan/queries/synthesis. Nothing hard-fails.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

try:
    import otel_emit  # best-effort spans to Phoenix; no-op if unavailable
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

try:
    import heartbeat  # watchdog liveness stamp around long inference (no wd import)
except Exception:  # noqa: BLE001
    class _NoHB:
        @staticmethod
        def beat(*_a, **_k):
            return None
    heartbeat = _NoHB()  # type: ignore

import session_state  # per-session research budget/cooldown + exhaustion gate

# ── config (all local defaults; the chat endpoint is the only "model" dep) ────
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
VLLM_MODEL = os.environ.get("VLLM_MODEL", os.environ.get("DISTILL_MODEL", "/model"))
DOCS_MCP_URL = os.environ.get("DOCS_MCP_URL", "http://127.0.0.1:9109/mcp")
RAG_MCP_URL = os.environ.get("RAG_MCP_URL", "http://127.0.0.1:9102/mcp")
KG_MCP_URL = os.environ.get("KG_MCP_URL", "http://127.0.0.1:9103/mcp")
# Optional cross-encoder rerank endpoint (shared with mcp-codebase-rag). When set,
# explore re-orders candidate sources by it ON TOP of the authority heuristic.
RERANK_BASE_URL = os.environ.get("RERANK_BASE_URL", "").rstrip("/")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "/model")

LLM_TIMEOUT = float(os.environ.get("RESEARCH_LLM_TIMEOUT", "300"))
# Reasoning model: it spends a big hidden budget before the answer (content=None if
# max_tokens is too small). Keep generous (see memory: vllm-reasoning-model).
LLM_MAX_TOKENS = int(os.environ.get("RESEARCH_LLM_MAX_TOKENS", "6000"))

# Bounds — the overspawning guard. Conservative by default, all configurable.
MAX_RESEARCH_LOOPS = int(os.environ.get("MAX_RESEARCH_LOOPS", "3"))
MAX_SUBGOALS = int(os.environ.get("RESEARCH_MAX_SUBGOALS", "5"))
QUERIES_PER_SUBGOAL = int(os.environ.get("RESEARCH_QUERIES_PER_SUBGOAL", "4"))
MAX_SOURCES_PER_QUERY = int(os.environ.get("RESEARCH_MAX_SOURCES_PER_QUERY", "3"))
MAX_TOTAL_SOURCES = int(os.environ.get("RESEARCH_MAX_TOTAL_SOURCES", "8"))
WALL_BUDGET_S = float(os.environ.get("RESEARCH_WALL_BUDGET_S", "600"))
MIN_INDEPENDENT_SOURCES = int(os.environ.get("RESEARCH_MIN_SOURCES", "2"))

# ── Adaptive-retrieval CORPUS-FIRST gate (gbrain "brain-first lookup") ────────
# Before the expensive cascade, deep_research asks the RAG store whether prior
# research already covers the question. Gate on the EXTERNAL corpus signal (chunks
# above a similarity threshold), never the model's own confidence.
RESEARCH_CORPUS_HIT_THRESHOLD = float(os.environ.get("RESEARCH_CORPUS_HIT_THRESHOLD", "0.75"))
RESEARCH_CORPUS_MIN_CHUNKS = int(os.environ.get("RESEARCH_CORPUS_MIN_CHUNKS", "2"))
RESEARCH_CORPUS_NS_PREFIX = os.environ.get("RESEARCH_CORPUS_NS_PREFIX", "docs/research")
RESEARCH_CORPUS_FIRST = os.environ.get("RESEARCH_CORPUS_FIRST", "1") not in ("0", "false", "False")
# R-Stage 3 — exhaustion-first ladder + parametric pre-screen (both env-gated).
RESEARCH_BLOCK_PARAMETRIC = os.environ.get("RESEARCH_BLOCK_PARAMETRIC", "1") not in ("0", "false", "False")
RESEARCH_EXHAUSTION_GATE = os.environ.get("RESEARCH_EXHAUSTION_GATE", "1") not in ("0", "false", "False")

# Similarity thresholds (Jaccard over word-shingles).
QUERY_DUP_THRESHOLD = float(os.environ.get("RESEARCH_QUERY_DUP_THRESHOLD", "0.8"))
CONTENT_DUP_THRESHOLD = float(os.environ.get("RESEARCH_CONTENT_DUP_THRESHOLD", "0.85"))

STATE_DIR = os.path.expanduser(os.environ.get("RESEARCH_STATE_DIR", "~/.hermes-max/research"))


# ── domain authority heuristic (counters SEO/source-quality bias) ─────────────
# Higher = more primary/authoritative. Used to re-rank candidate sources so a
# primary doc/paper/official repo outranks an SEO content farm for the same query.
_AUTH_TLDS = {".gov": 3, ".edu": 3, ".mil": 3, ".int": 3}
_AUTH_DOMAINS_HIGH = (
    "arxiv.org", "github.com", "gitlab.com", "python.org", "docs.python.org",
    "readthedocs.io", "rust-lang.org", "golang.org", "go.dev", "nodejs.org",
    "developer.mozilla.org", "kubernetes.io", "pytorch.org", "tensorflow.org",
    "w3.org", "ietf.org", "rfc-editor.org", "iso.org", "nist.gov", "acm.org",
    "ieee.org", "nature.com", "sciencedirect.com", "springer.com", "pubmed.ncbi.nlm.nih.gov",
    "openreview.net", "aclanthology.org", "neurips.cc", "huggingface.co",
)
_AUTH_DOMAINS_LOW = (  # SEO/content-farm-ish — downranked, never dropped outright
    "w3schools.com", "geeksforgeeks.org", "tutorialspoint.com", "javatpoint.com",
    "medium.com", "quora.com", "pinterest.com", "slideshare.net", "scribd.com",
    "answers.com", "ehow.com", "wikihow.com", "coursehero.com", "studocu.com",
)


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:  # noqa: BLE001
        return ""


def authority_score(url: str) -> int:
    """0..3 authority for a URL: primary/official/paper high, SEO farm low."""
    host = _domain(url)
    if not host:
        return 1
    for tld, sc in _AUTH_TLDS.items():
        if host.endswith(tld):
            return sc
    if any(host == d or host.endswith("." + d) or d in host for d in _AUTH_DOMAINS_HIGH):
        return 3
    if any(host == d or host.endswith("." + d) for d in _AUTH_DOMAINS_LOW):
        return 0
    # Official-looking docs subdomains get a bump.
    if host.startswith("docs.") or host.startswith("developer.") or ".docs." in host:
        return 2
    return 1


# ── text similarity (echo-chamber dedup) ──────────────────────────────────────
def _shingles(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if (a | b) else 0.0


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        path = p.path.rstrip("/")
        return f"{p.netloc.lower().removeprefix('www.')}{path}".lower()
    except Exception:  # noqa: BLE001
        return url.strip().lower()


# ── MCP client helper (call docs / rag / kg over streamable-http) ─────────────
async def _mcp_call_async(url: str, tool: str, args: dict) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            data = res.structuredContent or (
                json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and len(data) == 1:
                data = data["result"]
            return data


def _run_coro(coro: Any) -> Any:
    """Run a coroutine to completion whether or not an event loop is ALREADY
    running in this thread. FastMCP executes tool handlers inside a live event
    loop, so a bare asyncio.run() here raises "asyncio.run() cannot be called
    from a running event loop" — and because _mcp_call swallowed that error,
    EVERY MCP-to-MCP call (search_docs/fetch_clean/ingest_doc) silently returned
    nothing in the live server while passing in main-thread smoke tests (which
    have no running loop). That is the exact "smoke passes, live agent fails"
    trap. When a loop is running we complete the coroutine on a dedicated worker
    thread with its own fresh loop; otherwise asyncio.run() is fine."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import threading
    box: dict[str, Any] = {}
    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["v"] = loop.run_until_complete(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        finally:
            loop.close()
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _mcp_call(url: str, tool: str, args: dict) -> dict[str, Any]:
    try:
        return {"ok": True, "result": _run_coro(_mcp_call_async(url, tool, args))}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── injectable backends (so smoke tests can run with NO live services) ────────
# These wrap the existing sovereign loop (mcp-docs) and the chat model. Tests
# monkeypatch them to assert the failure-mode invariants deterministically.
def _search(query: str, limit: int = 8, category: str | None = None) -> list[dict[str, Any]]:
    """SearXNG candidate URLs via mcp-docs.search_docs. -> [{title,url,content}]."""
    r = _mcp_call(DOCS_MCP_URL, "search_docs", {"query": query, "category": category, "limit": limit})
    if not r.get("ok"):
        return []
    res = (r.get("result") or {})
    return res.get("results", []) if isinstance(res, dict) else []


def _fetch(url: str) -> dict[str, Any]:
    """Clean markdown via mcp-docs.fetch_clean (Crawl4AI -> trafilatura)."""
    r = _mcp_call(DOCS_MCP_URL, "fetch_clean", {"url": url})
    if not r.get("ok"):
        return {"ok": False, "url": url, "error": r.get("error", "fetch failed")}
    res = r.get("result") or {}
    return res if isinstance(res, dict) else {"ok": False, "url": url}


# Current logical phase, set by the public pipeline functions, used only to LABEL
# the heartbeat in the live log (plan / verify / synthesis / distill / ...). The
# heartbeat fires around EVERY _llm call regardless of the label.
_HB_PHASE = "inference"


def _llm(messages: list[dict], max_tokens: int = LLM_MAX_TOKENS, temperature: float = 0.2) -> str | None:
    """Chat completion via $VLLM_BASE_URL. None if unset/unreachable/empty (the
    reasoning model can spend its whole budget thinking -> content=None).

    A single synthesis/verify/distill inference here can legitimately run minutes
    with no other signal — the finish-line killer. So every blocking call stamps a
    watchdog heartbeat immediately BEFORE it starts and (via finally) immediately
    AFTER it returns or raises. check_stall(task_id=...) then sees a fresh heartbeat
    and never kills a slow-but-alive inference. See heartbeat.py / watchdog_core."""
    if not VLLM_BASE_URL:
        return None
    body = {"model": VLLM_MODEL, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    heartbeat.beat("deep_research", progress=f"{_HB_PHASE}: inference start")
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as c:
            r = c.post(f"{VLLM_BASE_URL}/chat/completions", json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content")
        return content.strip() if content else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        # AFTER the inference returns/raises — proves we reached the finish line.
        heartbeat.beat("deep_research", progress=f"{_HB_PHASE}: inference done")


def _rerank(query: str, documents: list[str]) -> list[int] | None:
    """Optional cross-encoder re-order; None if endpoint unset/unreachable."""
    if not RERANK_BASE_URL or not documents:
        return None
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{RERANK_BASE_URL}/rerank",
                       json={"model": RERANK_MODEL, "query": query,
                             "documents": [d[:2000] for d in documents]})
            r.raise_for_status()
            payload = r.json()
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        if not isinstance(results, list) or not results:
            return None
        order = sorted(results, key=lambda x: x.get("relevance_score", x.get("score", 0.0)), reverse=True)
        out = [int(x["index"]) for x in order if 0 <= int(x.get("index", -1)) < len(documents)]
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _json_from_llm(text: str | None) -> Any:
    """Pull the first JSON array/object out of an LLM reply (tolerant of fences)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    blob = m.group(1).strip() if m else text.strip()
    for candidate in (blob, re.search(r"(\[.*\]|\{.*\})", blob, re.DOTALL)):
        if candidate is None:
            continue
        s = candidate if isinstance(candidate, str) else candidate.group(1)
        try:
            return json.loads(s)
        except Exception:  # noqa: BLE001
            continue
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-")[:80] or "research"


# ── STAGE 1: plan_research ────────────────────────────────────────────────────
_PLAN_SYS = (
    "You are a research planner. Decompose the user's question into 2-5 focused, "
    "COMPLEMENTARY sub-goals (not overlapping), and a short ordered roadmap of how "
    "findings will support the final synthesis. Return STRICT JSON: "
    '{"subgoals": ["...", "..."], "roadmap": "..."}. No prose outside the JSON.'
)


def plan_research(question: str) -> dict[str, Any]:
    """Decompose a question into checkable sub-goals + roadmap, written to external
    PLAN state (so the plan itself is inspectable — planning hallucination is most
    damaging here). Degrades to a single-subgoal plan with no LLM."""
    global _HB_PHASE
    _HB_PHASE = "plan"
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}
    subgoals: list[str] = []
    roadmap = ""
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _PLAN_SYS},
         {"role": "user", "content": question}], temperature=0.2))
    if isinstance(parsed, dict):
        subgoals = [str(s).strip() for s in (parsed.get("subgoals") or []) if str(s).strip()]
        roadmap = str(parsed.get("roadmap", "")).strip()
    if not subgoals:
        subgoals = [question]  # graceful: the question is its own sub-goal
        roadmap = roadmap or "single-pass lookup (no decomposition available)"
    subgoals = subgoals[:MAX_SUBGOALS]

    slug = _slug(question)
    plan_md = (f"# Research plan\n\n**Question:** {question}\n\n## Sub-goals\n"
               + "".join(f"{i + 1}. {s}\n" for i, s in enumerate(subgoals))
               + f"\n## Roadmap\n{roadmap}\n")
    plan_path = ""
    try:
        d = os.path.join(STATE_DIR, slug)
        os.makedirs(d, exist_ok=True)
        plan_path = os.path.join(d, "PLAN.md")
        with open(plan_path, "w") as f:
            f.write(plan_md)
    except Exception:  # noqa: BLE001 - external state is best-effort
        plan_path = ""
    otel_emit.record("research_planned", {"question": question, "subgoals": len(subgoals),
                                          "llm": bool(VLLM_BASE_URL)})
    return {"ok": True, "question": question, "slug": slug, "subgoals": subgoals,
            "roadmap": roadmap, "plan_md": plan_md, "plan_path": plan_path}


# ── STAGE 2: develop_queries (diversity → counters echo chamber) ──────────────
_QUERY_SYS = (
    "Generate diverse, COMPLEMENTARY web-search queries for the sub-goal — vary the "
    "abstraction level (broad vs specific), phrasing, and angle so they retrieve "
    "DIFFERENT sources, NOT near-duplicates. Prefer queries likely to surface "
    "primary/official sources. Return a STRICT JSON array of strings only."
)


def _dedup_queries(queries: list[str]) -> list[str]:
    out: list[str] = []
    shings: list[set[str]] = []
    for q in queries:
        q = q.strip()
        if not q:
            continue
        sh = _shingles(q, n=2)
        if any(_jaccard(sh, s) >= QUERY_DUP_THRESHOLD for s in shings):
            continue  # too similar to one we already kept -> diversity guard
        out.append(q)
        shings.append(sh)
    return out


def develop_queries(subgoal: str, n: int = QUERIES_PER_SUBGOAL) -> dict[str, Any]:
    """Diverse, complementary queries (deduped by n-gram similarity) for a sub-goal."""
    subgoal = (subgoal or "").strip()
    if not subgoal:
        return {"ok": False, "error": "empty subgoal", "queries": []}
    n = max(1, min(int(n), 8))
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _QUERY_SYS},
         {"role": "user", "content": f"Sub-goal: {subgoal}\nGenerate {n} queries."}],
        temperature=0.5))
    queries = [str(q).strip() for q in parsed if str(q).strip()] if isinstance(parsed, list) else []
    if not queries:  # graceful deterministic fallback: a few angle variants
        queries = [subgoal, f"{subgoal} documentation", f"{subgoal} example", f"{subgoal} best practices"]
    queries = _dedup_queries(queries)[:n]
    otel_emit.record("queries_developed", {"subgoal": subgoal, "n": len(queries)})
    return {"ok": True, "subgoal": subgoal, "queries": queries}


# ── STAGE 3: explore (dedup + authority rank + bounded breadth) ───────────────
def explore(queries: list[str], seen_urls: list[str] | None = None,
            max_sources_per_query: int = MAX_SOURCES_PER_QUERY,
            max_total: int = MAX_TOTAL_SOURCES,
            category: str | None = None) -> dict[str, Any]:
    """Iterative web exploration over the sovereign loop. Applies URL + n-gram
    content dedup (break echo chambers), authority-aware re-ranking (primary >
    SEO farm; optional cross-encoder on top), and HARD breadth caps (no
    overspawning). Returns fetched sources with clean markdown + provenance."""
    queries = [q for q in (queries or []) if q and q.strip()]
    if not queries:
        return {"ok": False, "error": "no queries", "sources": []}
    max_sources_per_query = max(1, min(int(max_sources_per_query), 10))
    max_total = max(1, min(int(max_total), 50))

    seen_norm: set[str] = {_normalize_url(u) for u in (seen_urls or [])}
    seen_shingles: list[set[str]] = []
    sources: list[dict[str, Any]] = []
    echo_blocked = 0
    low_authority_filtered = 0
    fetch_attempts = 0

    for q in queries:
        if len(sources) >= max_total:
            break
        candidates = _search(q, limit=max(max_sources_per_query * 3, 8), category=category)
        # authority-aware re-rank of candidates for THIS query (primary first).
        for c in candidates:
            c["_authority"] = authority_score(c.get("url", ""))
        order = sorted(range(len(candidates)), key=lambda i: candidates[i]["_authority"], reverse=True)
        rr = _rerank(q, [f"{candidates[i].get('title','')} {candidates[i].get('content','')}"
                         for i in order])
        if rr:  # blend: cross-encoder order, but keep authority as the primary key
            order = [order[j] for j in rr]
        taken_this_query = 0
        for i in order:
            if len(sources) >= max_total or taken_this_query >= max_sources_per_query:
                break
            cand = candidates[i]
            url = cand.get("url", "")
            if not url:
                continue
            nu = _normalize_url(url)
            if nu in seen_norm:           # URL-level dedup -> break echo chamber
                echo_blocked += 1
                continue
            if cand["_authority"] == 0 and any(s["authority"] >= 2 for s in sources):
                # we already have primary sources; skip a known content farm
                low_authority_filtered += 1
                continue
            seen_norm.add(nu)
            fetch_attempts += 1
            fetched = _fetch(url)
            md = fetched.get("markdown", "") if fetched.get("ok") else ""
            sh = _shingles(md or cand.get("content", ""), n=3)
            if md and any(_jaccard(sh, prev) >= CONTENT_DUP_THRESHOLD for prev in seen_shingles):
                echo_blocked += 1          # near-duplicate CONTENT across a different URL
                continue
            if sh:
                seen_shingles.append(sh)
            sources.append({
                "url": url,
                "title": cand.get("title", ""),
                "domain": _domain(url),
                "authority": cand["_authority"],
                "query": q,
                "fetched": bool(fetched.get("ok")),
                "backend": fetched.get("backend"),
                "snippet": (cand.get("content", "") or "")[:500],
                "markdown": md[:20000],
                "chars": len(md),
            })
            taken_this_query += 1

    otel_emit.record("sources_explored", {
        "queries": len(queries), "sources": len(sources), "fetch_attempts": fetch_attempts,
        "echo_chamber_blocked": echo_blocked, "low_authority_filtered": low_authority_filtered,
    })
    if echo_blocked:
        otel_emit.record("echo_chamber_blocked", {"count": echo_blocked})
    if low_authority_filtered:
        otel_emit.record("low_authority_filtered", {"count": low_authority_filtered})
    return {"ok": True, "queries": queries, "count": len(sources), "sources": sources,
            "seen_urls": sorted(seen_norm), "echo_chamber_blocked": echo_blocked,
            "low_authority_filtered": low_authority_filtered}


# ── STAGE 4a: verify_claims (the differentiator — ≥2 independent sources) ──────
_VERIFY_SYS = (
    "You are a fact-checker. For the claim and the candidate source excerpt, answer "
    "with STRICT JSON {\"label\": \"supports\"|\"contradicts\"|\"neutral\"}. "
    "'supports' only if the excerpt clearly backs the claim."
)


def _label_support(claim: str, snippet: str) -> str:
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _VERIFY_SYS},
         {"role": "user", "content": f"Claim: {claim}\n\nSource excerpt:\n{snippet[:2000]}"}],
        temperature=0, max_tokens=2000))
    if isinstance(parsed, dict):
        lab = str(parsed.get("label", "")).lower().strip()
        if lab in ("supports", "contradicts", "neutral"):
            return lab
    return "unchecked"


def verify_claims(claims: list[dict], min_sources: int = MIN_INDEPENDENT_SOURCES) -> dict[str, Any]:
    """Cross-check each material claim against >= min_sources INDEPENDENT sources
    (independent = distinct domain, post-dedup). Flags single-sourced / conflicting
    rather than asserting them. Intermediate verification — a wrong plan/finding is
    caught HERE, before synthesis (planning-hallucination guard). When the chat
    model is available it also entails each (claim, source) pair; otherwise it
    counts independent-domain support deterministically."""
    global _HB_PHASE
    _HB_PHASE = "verify"
    claims = claims or []
    out: list[dict[str, Any]] = []
    for c in claims:
        claim = str(c.get("claim", "")).strip()
        srcs = c.get("sources", []) or []
        if not claim:
            continue
        # normalize to {url, snippet}
        norm = []
        for s in srcs:
            if isinstance(s, str):
                norm.append({"url": s, "snippet": ""})
            elif isinstance(s, dict):
                norm.append({"url": s.get("url", ""), "snippet": s.get("snippet", s.get("markdown", ""))})
        by_domain: dict[str, dict] = {}
        contradicts = 0
        supporters: list[str] = []
        for s in norm:
            dom = _domain(s["url"])
            if not dom or dom in by_domain:
                continue  # one vote per domain -> independence
            label = _label_support(claim, s["snippet"]) if (VLLM_BASE_URL and s["snippet"]) else "unchecked"
            by_domain[dom] = {"url": s["url"], "label": label}
            if label == "contradicts":
                contradicts += 1
            elif label in ("supports", "unchecked"):
                # 'unchecked' counts as candidate support (deterministic fallback),
                # but the status wording stays honest about the lack of entailment.
                supporters.append(s["url"])
        independent = len(by_domain)
        support_n = len([d for d in by_domain.values() if d["label"] in ("supports", "unchecked")])
        if contradicts and support_n:
            status = "conflicting"
        elif support_n >= min_sources:
            status = "well-supported"
        else:
            status = "single-sourced"
        out.append({
            "claim": claim, "status": status,
            "independent_sources": independent,
            "support_count": support_n,
            "contradictions": contradicts,
            "sources": [d["url"] for d in by_domain.values()],
            "entailed": any(d["label"] == "supports" for d in by_domain.values()),
        })
    otel_emit.record("claims_verified", {
        "claims": len(out),
        "well_supported": sum(1 for o in out if o["status"] == "well-supported"),
        "single_sourced": sum(1 for o in out if o["status"] == "single-sourced"),
        "conflicting": sum(1 for o in out if o["status"] == "conflicting"),
    })
    return {"ok": True, "verified": out, "min_sources": min_sources}


# ── STAGE 4b: synthesize (citation-backed report) ─────────────────────────────
_SYNTH_SYS = (
    "You are a research synthesizer. Using ONLY the verified findings, write a "
    "structured markdown report that answers the question. EVERY claim must cite its "
    "source URL inline like [1], with a numbered Sources list at the end. Clearly "
    "label what is well-supported vs single-sourced vs conflicting. Preserve quotes, "
    "code, and figures VERBATIM (compress, do not paraphrase technical content). End "
    "with a short 'Confidence & gaps' section. Do NOT invent facts or sources."
)


def synthesize(question: str, verified_findings: list[dict], plan: dict | None = None) -> dict[str, Any]:
    """Compile a structured, citation-backed report distinguishing well-supported /
    single-sourced / conflicting findings. Degrades (no LLM) to a deterministic
    cited bullet list — still honest, still every-claim-to-a-URL."""
    global _HB_PHASE
    _HB_PHASE = "synthesis"
    verified_findings = verified_findings or []
    citations: list[str] = []
    seen_c: set[str] = set()
    for f in verified_findings:
        for u in f.get("sources", []):
            if u and u not in seen_c:
                seen_c.add(u)
                citations.append(u)

    confidence = "low"
    well = sum(1 for f in verified_findings if f.get("status") == "well-supported")
    if verified_findings:
        ratio = well / len(verified_findings)
        confidence = "high" if ratio >= 0.66 else ("medium" if ratio >= 0.33 else "low")
    gaps = [f["claim"] for f in verified_findings if f.get("status") != "well-supported"][:10]

    findings_blob = json.dumps(verified_findings, indent=2)[:16000]
    report = _llm(
        [{"role": "system", "content": _SYNTH_SYS},
         {"role": "user", "content":
            f"Question: {question}\n\nVerified findings (JSON):\n{findings_blob}\n\n"
            f"Sources (numbered):\n" + "\n".join(f"[{i + 1}] {u}" for i, u in enumerate(citations))}],
        temperature=0.2)
    if not report:  # deterministic, still-cited fallback
        lines = [f"# Research brief: {question}", ""]
        idx = {u: i + 1 for i, u in enumerate(citations)}
        for f in verified_findings:
            cites = " ".join(f"[{idx[u]}]" for u in f.get("sources", []) if u in idx)
            lines.append(f"- ({f.get('status')}) {f.get('claim')} {cites}")
        lines += ["", "## Sources"] + [f"[{i + 1}] {u}" for i, u in enumerate(citations)]
        lines += ["", f"## Confidence & gaps", f"Confidence: {confidence}.",
                  "Gaps (not well-supported): " + ("; ".join(gaps) if gaps else "none")]
        report = "\n".join(lines)
        synthesized = False
    else:
        synthesized = True
    # ── R-Stage 4: GAP ANALYSIS, not confidence ──────────────────────────────
    # The old confidence=low/high was misread by the agent as "retry". Replace it
    # with gbrain-style quality metrics + an `actionable` flag + a `gap_note` that
    # says what's covered and what isn't. A low-corroboration synthesis is STILL
    # actionable (claims are single-sourced by design after echo-chamber dedup) —
    # the agent proceeds and notes the gaps, exactly like gbrain's "heads up: the
    # brain doesn't know X yet". The ONLY non-actionable case is a genuinely
    # empty/broken result (no sources AND no claims). The agent NEVER retries
    # deep_research on the quality score; it proceeds, or uses a lighter tool for a
    # specific follow-up. The real quality gate is the verify gate on the code.
    claims_total = len(verified_findings)
    claims_corroborated = sum(1 for f in verified_findings if f.get("status") == "well-supported")
    claims_single_sourced = sum(1 for f in verified_findings if f.get("status") == "single-sourced")
    claims_conflicting = sum(1 for f in verified_findings if f.get("status") == "conflicting")
    citation_count = len(citations)
    unsupported_rate = (round((claims_single_sourced + claims_conflicting) / claims_total, 3)
                        if claims_total else 0.0)
    actionable = bool(report) and (citation_count > 0 or claims_total > 0)

    if not actionable:
        gap_note = ("NOT actionable — research returned no usable sources or claims "
                    "(0 citations, 0 claims). Use a lighter targeted tool or proceed "
                    "from parametric knowledge; do not re-run deep_research.")
    else:
        bits = [f"covers {claims_total} claim(s); {claims_corroborated} corroborated "
                f"(>=2 independent sources), {claims_single_sourced} single-sourced, "
                f"{claims_conflicting} conflicting; {citation_count} citation(s)"]
        if gaps:
            bits.append("not fully corroborated: " + "; ".join(gaps[:5]))
        bits.append("proceed with implementation and note these as risks; do NOT "
                    "re-run deep_research — use a lighter tool for any specific follow-up")
        gap_note = ". ".join(bits)

    if actionable and report and "Research sufficiency" not in report:
        report += (f"\n\n> _Research sufficiency: **{'actionable' if actionable else 'not actionable'}**. "
                   f"{gap_note}_")
    otel_emit.record("report_synthesized", {
        "question": question, "citations": citation_count, "claims_total": claims_total,
        "claims_corroborated": claims_corroborated, "unsupported_rate": unsupported_rate,
        "actionable": actionable, "llm": synthesized})
    return {"ok": True, "question": question, "report_md": report, "synthesized": synthesized,
            "citations": citations,
            # gap-analysis quality metrics (NOT a retry-triggering confidence)
            "actionable": actionable, "gap_note": gap_note,
            "citation_count": citation_count, "claims_total": claims_total,
            "claims_corroborated": claims_corroborated,
            "claims_single_sourced": claims_single_sourced,
            "claims_conflicting": claims_conflicting, "unsupported_rate": unsupported_rate,
            "gaps": gaps}


# ── claim extraction (sources -> candidate claims grouped by support) ─────────
_EXTRACT_SYS = (
    "Extract the atomic, checkable factual claims from the sources that help answer "
    "the question. For EACH claim, list the source URLs (from those provided) that "
    "support it. Return STRICT JSON: [{\"claim\": \"...\", \"source_urls\": [\"...\"]}]. "
    "Only use the provided URLs; do not invent."
)


def _extract_claims(question: str, sources: list[dict]) -> list[dict]:
    if not sources:
        return []
    catalog = "\n\n".join(
        f"URL: {s['url']}\nTITLE: {s.get('title','')}\nCONTENT:\n{(s.get('markdown') or s.get('snippet',''))[:4000]}"
        for s in sources)[:24000]
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _EXTRACT_SYS},
         {"role": "user", "content": f"Question: {question}\n\nSources:\n{catalog}"}],
        temperature=0.1))
    by_url = {s["url"]: s for s in sources}
    claims: list[dict] = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim", "")).strip()
            urls = [u for u in (item.get("source_urls") or []) if u in by_url]
            if claim:
                claims.append({"claim": claim,
                               "sources": [{"url": u, "snippet": (by_url[u].get("markdown") or by_url[u].get("snippet", ""))[:2000]} for u in urls]})
    if not claims:  # deterministic fallback: one claim per source (its title)
        for s in sources:
            title = s.get("title") or s.get("snippet", "")[:120]
            if title:
                claims.append({"claim": title, "sources": [{"url": s["url"], "snippet": s.get("snippet", "")}]})
    return claims


# ── R-Stage 3: rule-based research-need classifier (NO LLM) ───────────────────
# Pre-screens a question into parametric / targeted / synthesis on cheap lexical
# signals (NOT the model's self-judgment). Parametric (textbook algorithms, "how
# does X work", standard patterns) warrants NO research tool — implement from
# parametric knowledge. Only synthesis (novel/recent/multi-source/exact-spec)
# warrants Tier-4 deep_research.
_SYNTHESIS_SIGNALS = (
    "current state", "state of the art", "state-of-the-art", "latest", "recent",
    "newest", "compare", " vs ", "versus", "trade-off", "tradeoff", "survey",
    "landscape", "ecosystem", "which is better", "best approach", "best practice",
    "emerging", "novel", "cutting edge", "2024", "2025", "2026", "benchmark",
    "test vector", "test-vector", "specification", "whitepaper", "rfc ", "eip ",
    "erc ", "protocol spec", "primary literature", "reconcile", "triangulate",
)
_PARAMETRIC_ALGOS = (
    "miller-rabin", "miller rabin", "quicksort", "merge sort", "mergesort",
    "binary search", "bubble sort", "insertion sort", "dijkstra", "bellman-ford",
    "breadth-first", "depth-first", "bfs", "dfs", "a-star", "a*", "dynamic programming",
    "memoization", "hash table", "hash map", "linked list", "binary tree", "heap sort",
    "fibonacci", "sieve of eratosthenes", "euclidean algorithm", "gcd", "fizzbuzz",
    "two pointer", "sliding window", "kmp", "rabin-karp", "union-find", "topological sort",
    "newton's method", "gradient descent", "linear regression", "k-means",
)
_PARAMETRIC_FRAMES = (
    "how does", "how do i implement", "how to implement", "implement a", "implement the",
    "explain the", "explain how", "what is a", "what is the", "write a function",
    "standard way to", "common pattern", "textbook",
)
_TARGETED_SIGNALS = (
    "what version", "which version", "api", "parameter", "return value", "syntax",
    "flag", "option", "default value", "exact value", "signature", "endpoint",
    "config", "environment variable", "error code", "status code",
)


def classify_research_need(question: str) -> dict[str, Any]:
    """Return {class: parametric|targeted|synthesis, signals, block} for `question`.
    Precedence: a synthesis signal wins (open-ended/novel/recent → allow Tier 4);
    else a textbook-algorithm or how-does-X-work frame → parametric (block research);
    else a precise-fact frame → targeted (lighter tools, not Tier 4); else default
    synthesis-eligible (the agent chose research; don't over-block)."""
    q = (question or "").lower()
    # Precedence: synthesis wins; then DEFINITE parametric (named textbook algorithm);
    # then targeted (precise fact — a broad "what is the" frame must NOT mask a
    # "return value"/"exact value" lookup); then generic parametric frames; else
    # default synthesis-eligible (don't over-block a deliberate research call).
    syn = [s.strip() for s in _SYNTHESIS_SIGNALS if s in q]
    if syn:
        return {"class": "synthesis", "signals": syn[:5], "block": False}
    algo = [s for s in _PARAMETRIC_ALGOS if s in q]
    if algo:
        return {"class": "parametric", "signals": algo[:5], "block": True}
    tgt = [s for s in _TARGETED_SIGNALS if s in q]
    if tgt:
        return {"class": "targeted", "signals": tgt[:5], "block": False}
    frame = [s for s in _PARAMETRIC_FRAMES if s in q]
    if frame:
        return {"class": "parametric", "signals": frame[:5], "block": True}
    return {"class": "synthesis", "signals": [], "block": False}


# ── M-Stage 5: CitationAgent pass ─────────────────────────────────────────────
# Anthropic's multi-agent research system runs a final CitationAgent after the
# research loop: it checks each claim in the report against the source documents and
# flags claims not directly supported. Run it ONCE after synthesis. Route to the
# conductor's steer tier (cheap cloud, better attribution) when available; fall back
# to the local model. Conservative: mark a claim unsupported if the source is ambiguous.
ESCALATION_MCP_URL = os.environ.get(
    "ESCALATION_MCP_URL", f"http://127.0.0.1:{os.environ.get('MCP_ESCALATION_PORT', '9105')}/mcp")
CITATION_VERIFY = os.environ.get("RESEARCH_CITATION_VERIFY", "1") not in ("0", "false", "False")

_CITE_SYS = (
    "You are a citation auditor. Given a synthesized research REPORT and the SOURCE "
    "passages it was built from, check EACH factual claim in the report against the "
    "sources. Be CONSERVATIVE: mark a claim unsupported if no source directly and "
    "unambiguously supports it. Return STRICT JSON only: {\"supported_claims\": <int>, "
    "\"unsupported_claims\": [\"<claim text>\", ...], \"source_attribution\": "
    "{\"<claim text>\": \"<source url or id>\"}}. No prose outside the JSON."
)


def _citation_verify(report_md: str, sources: list[dict]) -> dict[str, Any]:
    """Audit the report's claims against the sources. Tries the conductor steer tier
    first (better attribution), falls back to the local model. Returns
    {supported_claims, unsupported_claims, source_attribution, sources_checked,
    backend}. Best-effort: on any failure returns an empty/neutral result so the
    research run never fails on the citation pass."""
    if not report_md or not sources:
        return {"supported_claims": 0, "unsupported_claims": [], "source_attribution": {},
                "sources_checked": 0, "backend": "skipped"}
    catalog = "\n\n".join(
        f"[{i + 1}] {s.get('url','')} :: {(s.get('markdown') or s.get('title') or s.get('snippet',''))[:1500]}"
        for i, s in enumerate(sources))[:18000]
    user = f"REPORT:\n{report_md[:12000]}\n\nSOURCES:\n{catalog}"
    backend = "local"
    raw = None
    # 1) conductor steer (cheap cloud) — better attribution than the local 35B
    try:
        r = _mcp_call(ESCALATION_MCP_URL, "conductor_steer",
                      {"prompt": f"{_CITE_SYS}\n\n{user}", "max_tokens": 1500})
        res = (r.get("result") or {}) if isinstance(r, dict) else {}
        if r.get("ok") and isinstance(res, dict) and not res.get("proceed_local") and res.get("content"):
            raw, backend = res["content"], "conductor_steer"
    except Exception:  # noqa: BLE001
        raw = None
    # 2) local fallback
    if not raw:
        raw = _llm([{"role": "system", "content": _CITE_SYS},
                    {"role": "user", "content": user}], temperature=0)
        backend = "local"
    parsed = _json_from_llm(raw)
    if not isinstance(parsed, dict):
        return {"supported_claims": 0, "unsupported_claims": [], "source_attribution": {},
                "sources_checked": len(sources), "backend": backend, "parse_failed": True}
    unsupported = [str(c) for c in (parsed.get("unsupported_claims") or []) if str(c).strip()]
    attribution = parsed.get("source_attribution") if isinstance(parsed.get("source_attribution"), dict) else {}
    try:
        supported = int(parsed.get("supported_claims") or 0)
    except Exception:  # noqa: BLE001
        supported = 0
    return {"supported_claims": supported, "unsupported_claims": unsupported,
            "source_attribution": attribution, "sources_checked": len(sources), "backend": backend}


# ── ORCHESTRATOR: deep_research ───────────────────────────────────────────────
def deep_research(question: str, max_loops: int = MAX_RESEARCH_LOOPS,
                  max_total_sources: int = MAX_TOTAL_SOURCES,
                  category: str | None = None, compound: bool = True) -> dict[str, Any]:
    """plan -> (develop -> explore -> verify) x bounded loops -> synthesize.

    Single-threaded by default (no sub-agent overspawning); bounded by max_loops,
    total-source cap, and a wall-clock budget. Writes the final brief + key
    entities into RAG/KG so a later related run starts ahead (compounding)."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}
    t0 = time.monotonic()
    max_loops = max(1, min(int(max_loops), 8))

    # ── TIER-0 PARAMETRIC pre-screen (R-Stage 3; cheapest gate, no tool call) ─
    # A textbook algorithm / "how does X work" / standard pattern warrants NO
    # research tool — implement from parametric knowledge. Hard-block it from the
    # cascade. Gates on a cheap lexical signal, not the model's self-judgment.
    cls = classify_research_need(question)
    otel_emit.record("query_classification", {
        "tool": "deep_research", "class": cls["class"],
        "signals": ", ".join(cls["signals"]) or None, "blocked": cls["block"] and RESEARCH_BLOCK_PARAMETRIC})
    if cls["block"] and RESEARCH_BLOCK_PARAMETRIC:
        return {"ok": False, "gated": True, "gate_reason": "parametric",
                "classification": cls["class"], "signals": cls["signals"],
                "error": ("This is a textbook/parametric topic (signals: "
                          f"{', '.join(cls['signals'])}) — implement directly from "
                          "parametric knowledge; no research tool is warranted."),
                "use_instead": "implement from parametric knowledge (Tier 0 — no tool call)"}

    # ── CORPUS-FIRST gate (adaptive retrieval / gbrain brain-first lookup) ────
    # Ask the RAG store whether prior research already covers this question. A hit
    # (>= MIN_CHUNKS chunks above the similarity THRESHOLD in the research namespace)
    # answers instantly from the corpus and SKIPS the expensive cascade entirely.
    if RESEARCH_CORPUS_FIRST:
        pc = _mcp_call(RAG_MCP_URL, "corpus_hit_check", {
            "query": question, "namespace_prefix": RESEARCH_CORPUS_NS_PREFIX,
            "threshold": RESEARCH_CORPUS_HIT_THRESHOLD,
            "min_chunks": RESEARCH_CORPUS_MIN_CHUNKS})
        res = (pc.get("result") or {}) if isinstance(pc, dict) else {}
        hit = bool(res.get("hit"))
        chunks = res.get("chunks") or []
        session_state.mark_corpus_checked()  # precondition satisfied for this session
        otel_emit.record("corpus_precheck", {
            "tool": "deep_research", "question": question,
            "hit": hit, "chunks_found": res.get("chunks_found", 0),
            "threshold": res.get("threshold"), "scoring": res.get("scoring"),
            "launched_cascade": not hit})
        if hit and chunks:
            n = res.get("chunks_found", len(chunks))
            report = (f"# Prior research: {question}\n\n"
                      f"> _Answered from the existing corpus — {n} prior research "
                      f"chunk(s) above similarity {res.get('threshold')} covered this. "
                      f"No external research cascade was launched (corpus-first gate)._\n\n"
                      + "\n\n".join(
                          f"## Source: {c.get('source','?')} "
                          f"(namespace {c.get('namespace','?')}, score {c.get('score')})\n\n"
                          f"{c.get('snippet','')}" for c in chunks))
            return {
                "ok": True, "question": question,
                "answered_from_corpus": True, "launched_cascade": False,
                "corpus_chunks": chunks, "report_md": report,
                "actionable": True, "confidence_is_advisory": True,
                "sources_explored": len(chunks),
                "stop_reason": "corpus-first hit",
                "note": f"answered from existing corpus — {n} prior research chunk(s) covered this",
                "elapsed_s": round(time.monotonic() - t0, 2), "sovereign": True}

    # ── RESEARCH BUDGET + COOLDOWN gate (R-Stage 2; SWE-agent per-task budget) ─
    # The corpus didn't cover it — but research still can't consume the whole task
    # budget or re-fire reflexively. Block (not the skill — the SERVER) if a
    # deep_research fired < cooldown ago, or cumulative research time this session
    # would exceed the budget. Demand-driven with a cooldown: a genuinely novel
    # later need still fires after the cooldown; reflexive re-firing does not.
    gate = session_state.research_gate(est_s=WALL_BUDGET_S)
    otel_emit.record("research_budget_gate", {
        "tool": "deep_research", "allowed": gate["allowed"], "reason": gate["reason"],
        "cooldown_remaining_s": gate["cooldown_remaining_s"],
        "cumulative_s": gate["cumulative_s"], "budget_s": gate["budget_s"],
        "calls": gate["calls"]})
    if not gate["allowed"]:
        if gate["reason"] == "cooldown":
            msg = (f"deep_research is on cooldown — a call fired recently and "
                   f"{gate['cooldown_remaining_s']:.0f}s remain of the "
                   f"{int(session_state.RESEARCH_COOLDOWN_S)}s window. Do NOT re-run it.")
        else:
            msg = (f"deep_research budget exhausted this session "
                   f"({gate['cumulative_s']:.0f}s used of {int(gate['budget_s'])}s).")
        return {"ok": False, "gated": True, "gate_reason": gate["reason"],
                "error": msg,
                "use_instead": ("search_code against the corpus, or mcp-docs "
                                "research_topic / fetch_clean for the specific "
                                "sub-question — never another deep_research now"),
                **{k: gate[k] for k in ("cooldown_remaining_s", "cumulative_s",
                                        "budget_s", "calls")}}

    # ── EXHAUSTION-FIRST gate (R-Stage 3): prove cheaper tools were tried ──────
    # deep_research is Tier 4. The corpus check above is Tier 1; Tiers 2-3
    # (fetch_clean / research_topic) must be attempted on a RELATED query first, or
    # the agent must explicitly note it tried them (note_lighter_tools_attempted).
    if RESEARCH_EXHAUSTION_GATE:
        lt = session_state.lighter_tools_attempted(question)
        otel_emit.record("tool_ladder_gate", {
            "tool": "deep_research", "tier_attempted": 4,
            "lighter_tools_flag": lt["attempted"], "best_sim": lt["best_sim"],
            "best_tool": lt["best_tool"], "considered": lt["considered"],
            "escalation_allowed": lt["attempted"]})
        if not lt["attempted"]:
            return {"ok": False, "gated": True, "gate_reason": "lighter_tools_not_attempted",
                    "error": ("Lighter tools not yet attempted for this question. Run "
                              "search_code against the corpus, then mcp-docs fetch_clean "
                              "or research_topic on the specific sub-question. Call "
                              "deep_research only after those return insufficient "
                              "results. For textbook algorithms and standard patterns, "
                              "implement from parametric knowledge without any research tool."),
                    "use_instead": "search_code → fetch_clean / research_topic (Tiers 1-3)",
                    "ladder": ["Tier0 parametric (no tool)", "Tier1 search_code/corpus",
                               "Tier2 fetch_clean", "Tier3 research_topic", "Tier4 deep_research"],
                    "best_related_sim": lt["best_sim"]}

    plan = plan_research(question)
    subgoals = plan["subgoals"]
    all_sources: list[dict] = []
    seen_urls: list[str] = []
    loops = 0
    echo_blocked_total = 0
    low_authority_total = 0
    stop_reason = "completed"

    for loop in range(max_loops):
        loops = loop + 1
        if time.monotonic() - t0 > WALL_BUDGET_S:
            stop_reason = "wall-clock budget"
            break
        if len(all_sources) >= max_total_sources:
            stop_reason = "source cap"
            break
        subgoal = subgoals[loop % len(subgoals)]
        dq = develop_queries(subgoal)
        ex = explore(dq["queries"], seen_urls=seen_urls,
                     max_total=max(1, max_total_sources - len(all_sources)),
                     category=category)
        new = ex.get("sources", [])
        all_sources.extend(new)
        seen_urls = ex.get("seen_urls", seen_urls)
        echo_blocked_total += ex.get("echo_chamber_blocked", 0)
        low_authority_total += ex.get("low_authority_filtered", 0)
        # tqdm-style empirical progress (Stage 7a): item N/total, per-loop yield,
        # running elapsed — the live log derives the ETA. Real movement, not "running…".
        otel_emit.record("research_progress", {
            "tool": "deep_research", "done": len(all_sources), "total": max_total_sources,
            "item": f"loop {loops}/{max_loops}: {subgoal[:48]}",
            "per_item": f"+{len(new)} sources", "elapsed_s": round(time.monotonic() - t0, 1)})
        if not new and loop > 0:
            stop_reason = "no new sources"
            break

    # extract -> verify (intermediate) -> synthesize
    claims = _extract_claims(question, all_sources)
    verified = verify_claims(claims)["verified"]
    synth = synthesize(question, verified, plan)

    # ── CitationAgent pass (M-Stage 5): audit the report's claims vs the sources ─
    citation = {"supported_claims": 0, "unsupported_claims": [], "source_attribution": {},
                "sources_checked": 0, "backend": "off"}
    if CITATION_VERIFY:
        citation = _citation_verify(synth.get("report_md", ""), all_sources)
        unc = citation.get("unsupported_claims") or []
        otel_emit.record("citation_verified", {
            "tool": "deep_research", "supported_claims": citation.get("supported_claims"),
            "unsupported_count": len(unc), "sources_checked": citation.get("sources_checked"),
            "backend": citation.get("backend")})
        if unc:
            warn = (f"Warning: {len(unc)} claim(s) in this synthesis are not directly "
                    f"attributed to a source — treat with caution: "
                    + "; ".join(c[:80] for c in unc[:5]))
            synth["gap_note"] = ((synth.get("gap_note", "") + " ") if synth.get("gap_note") else "") + warn

    compounded = {"rag_stored": False, "kg_entities": 0}
    if compound and synth.get("report_md"):
        topic = f"research/{plan['slug']}"
        ing = _mcp_call(DOCS_MCP_URL, "ingest_doc",
                        {"url_or_markdown": synth["report_md"], "topic": topic})
        if ing.get("ok"):
            res = ing.get("result") or {}
            compounded = {"rag_stored": bool(res.get("rag_stored")),
                          "kg_entities": res.get("kg_entities_written", 0),
                          "namespace": res.get("namespace")}

    elapsed = round(time.monotonic() - t0, 2)
    # record against the per-session budget/cooldown (R-Stage 2)
    session_state.record_research(elapsed)
    otel_emit.record("deep_research_done", {
        "question": question, "loops": loops, "sources": len(all_sources),
        "claims": len(verified), "actionable": synth.get("actionable"),
        "claims_corroborated": synth.get("claims_corroborated"),
        "unsupported_rate": synth.get("unsupported_rate"),
        "unsupported_claims": len(citation.get("unsupported_claims") or []),
        "citation_backend": citation.get("backend"),
        "elapsed_s": elapsed, "stop_reason": stop_reason})
    return {
        "ok": True,
        "question": question,
        "plan": {"subgoals": subgoals, "roadmap": plan.get("roadmap"), "plan_path": plan.get("plan_path")},
        "loops": loops,
        "stop_reason": stop_reason,
        "sources_explored": len(all_sources),
        "sources": [{"url": s["url"], "domain": s["domain"], "authority": s["authority"],
                     "title": s["title"]} for s in all_sources],
        "verified_findings": verified,
        "report_md": synth["report_md"],
        "synthesized": synth["synthesized"],
        # GAP ANALYSIS, not confidence (R-Stage 4): this result is actionable unless
        # genuinely empty/broken; the agent proceeds and notes the gaps, and NEVER
        # re-runs deep_research on the quality score. Real quality gate = verify on code.
        "actionable": synth.get("actionable", True),
        "gap_note": synth.get("gap_note"),
        # CitationAgent pass (M-Stage 5): claims audited against the sources
        "unsupported_claims": citation.get("unsupported_claims", []),
        "source_attribution": citation.get("source_attribution", {}),
        "citation_backend": citation.get("backend"),
        "citation_count": synth.get("citation_count"),
        "claims_total": synth.get("claims_total"),
        "claims_corroborated": synth.get("claims_corroborated"),
        "claims_single_sourced": synth.get("claims_single_sourced"),
        "unsupported_rate": synth.get("unsupported_rate"),
        "gaps": synth["gaps"],
        "citations": synth["citations"],
        "echo_chamber_blocked": echo_blocked_total,
        "low_authority_filtered": low_authority_total,
        "compounded": compounded,
        "elapsed_s": elapsed,
        "sovereign": True,
    }


def stats() -> dict[str, Any]:
    def _up(url: str, path: str = "") -> bool:
        try:
            with httpx.Client(timeout=3) as c:
                return c.get(url + path).status_code < 500
        except Exception:  # noqa: BLE001
            return False
    docs_http = DOCS_MCP_URL.replace("/mcp", "/health")
    return {
        "chat_model": VLLM_BASE_URL or "(unset — deterministic plan/queries/synthesis)",
        "docs_mcp": DOCS_MCP_URL,
        "docs_up": _up(docs_http),
        "rag_mcp": RAG_MCP_URL,
        "kg_mcp": KG_MCP_URL,
        "rerank": RERANK_BASE_URL or "(unset — authority-heuristic ranking only)",
        "max_loops": MAX_RESEARCH_LOOPS,
        "max_total_sources": MAX_TOTAL_SOURCES,
        "max_sources_per_query": MAX_SOURCES_PER_QUERY,
        "min_independent_sources": MIN_INDEPENDENT_SOURCES,
        "wall_budget_s": WALL_BUDGET_S,
    }
```

### A.2  mcp-research — web search methods & fan-out

#### `mcp-research/sources.py`
```python
"""Stage 1 — structured source fan-out for mcp-research.

Native MCP source adapters that sit ALONGSIDE the existing sovereign SearXNG ->
Crawl4AI/trafilatura web layer (mcp-docs). Each adapter is a thin, bounded reader
over a free/public API. The design rules (identical to research_core's discipline):

  * NEVER raise — every adapter returns {"ok": bool, "results": [...], "error": str}
    so a dead/blocked source degrades to an empty list, never a crash.
  * Presence-gated — an adapter that needs a token (github_search -> GITHUB_TOKEN)
    no-ops with {"skipped": true} when the token is absent. Keyless sources
    (arXiv, Semantic Scholar unauth pool, HN Algolia, Stack Exchange) always run.
  * Degrade to web — no structured source is load-bearing. The classifier-router
    ALWAYS includes "searxng" so the existing web layer answers even if every
    structured adapter is down.

Network lives in two monkeypatchable helpers (_get_json / _get_text) so the smoke
tests can assert parsing, gating, RRF, and routing with NO live services.

Normalized result item (every adapter emits this shape):
  {"title","url","content","source_type","authors":[...],"date":str|"",
   "citation_count":int|None,"extra":{...}}

NOTE (honest): the upstream spec assumed arxiv_search / hn_search already existed
to be "extended" — they did not; mcp-research was SearXNG-only. These are built
fresh here as native adapters.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

try:
    import otel_emit  # best-effort spans; no-op if unavailable
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

# ── env (all optional — keyless sources work without any of these) ────────────
# GITHUB_TOKEN is the canonical name; GITHUB_ACCESS_TOKEN is accepted as a fallback
# (GitHub PAT installs commonly export the latter). GITHUB_TOKEN wins if both set.
GITHUB_TOKEN = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_ACCESS_TOKEN") or "").strip()
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
STACKEXCHANGE_KEY = os.environ.get("STACKEXCHANGE_KEY", "").strip()

HTTP_TIMEOUT = float(os.environ.get("RESEARCH_SOURCE_TIMEOUT", "12"))
RRF_K = int(os.environ.get("RESEARCH_RRF_K", "60"))

ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1"
GITHUB_API = "https://api.github.com"
HN_API = "https://hn.algolia.com/api/v1/search"
SE_API = "https://api.stackexchange.com/2.3"

# Attribution required by Semantic Scholar's API terms when results are displayed.
S2_ATTRIBUTION = "Data from Semantic Scholar (https://www.semanticscholar.org/)"


# ── monkeypatchable network primitives (smoke tests stub these) ───────────────
def _get_json(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    """GET -> parsed JSON. Returns {"ok":False,"error":...} on ANY failure."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, params=params, headers=headers)
            r.raise_for_status()
            return {"ok": True, "json": r.json()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _post_json(url: str, json_body: Any, params: dict | None = None,
               headers: dict | None = None, timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.post(url, params=params, headers=headers, json=json_body)
            r.raise_for_status()
            return {"ok": True, "json": r.json()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _get_text(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    """GET -> raw text (for arXiv Atom XML, Discourse, etc.)."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, params=params, headers=headers)
            r.raise_for_status()
            return {"ok": True, "text": r.text}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _item(title: str, url: str, content: str = "", source_type: str = "web",
          authors: list[str] | None = None, date: str = "",
          citation_count: int | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "content": (content or "").strip(),
        "source_type": source_type,
        "authors": authors or [],
        "date": date or "",
        "citation_count": citation_count,
        "extra": extra,
    }


# ── arXiv (keyless Atom API; optional days_back + category targeting) ─────────
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_search(query: str, days_back: int | None = None,
                 categories: list[str] | None = None, limit: int = 8) -> dict[str, Any]:
    """arXiv Atom API. days_back OPTIONAL (None => no recency filter, so seminal
    work is reachable). categories targets cs.CR / cs.LG / cs.DC / cs.AI etc.
    Keyless, ~1 req / 3s upstream. Errors returned as strings, never raised."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "arxiv", "results": []}
    limit = max(1, min(int(limit), 50))
    terms = [f"all:{query}"]
    if categories:
        cat_clause = " OR ".join(f"cat:{c.strip()}" for c in categories if c.strip())
        if cat_clause:
            terms = [f"({cat_clause})", f"all:{query}"]
    search_query = " AND ".join(terms)
    params = {"search_query": search_query, "start": 0, "max_results": limit,
              "sortBy": "relevance", "sortOrder": "descending"}
    if days_back is not None:
        # arXiv has no date query param; sort by recency and filter client-side.
        params["sortBy"] = "submittedDate"
    r = _get_text(ARXIV_API, params=params)
    if not r.get("ok"):
        return {"ok": False, "source": "arxiv", "error": r["error"], "results": []}
    try:
        root = ET.fromstring(r["text"])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "source": "arxiv", "error": f"parse: {e}", "results": []}
    results: list[dict[str, Any]] = []
    cutoff = None
    if days_back is not None:
        import datetime
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days_back))
    for entry in root.findall("a:entry", _ATOM_NS):
        title = (entry.findtext("a:title", default="", namespaces=_ATOM_NS) or "").strip()
        summary = (entry.findtext("a:summary", default="", namespaces=_ATOM_NS) or "").strip()
        url = (entry.findtext("a:id", default="", namespaces=_ATOM_NS) or "").strip()
        published = (entry.findtext("a:published", default="", namespaces=_ATOM_NS) or "").strip()
        if cutoff is not None and published:
            try:
                import datetime
                pub = datetime.datetime.strptime(published[:10], "%Y-%m-%d")
                if pub < cutoff:
                    continue
            except Exception:  # noqa: BLE001
                pass
        authors = [a.findtext("a:name", default="", namespaces=_ATOM_NS).strip()
                   for a in entry.findall("a:author", _ATOM_NS)]
        prim = entry.find("arxiv:primary_category", _ATOM_NS)
        cat = prim.get("term") if prim is not None else ""
        results.append(_item(title, url, summary, "arxiv", [a for a in authors if a],
                             published[:10], None, category=cat))
    otel_emit.record("source_arxiv", {"query": query, "results": len(results),
                                       "categories": categories or [], "days_back": days_back})
    return {"ok": True, "source": "arxiv", "results": results}


# ── Semantic Scholar (keyless shared pool; citation-graph traversal) ──────────
_S2_FIELDS = "title,abstract,year,authors,citationCount,url,externalIds"


def _s2_headers() -> dict[str, str]:
    return {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}


def _s2_paper_to_item(p: dict[str, Any]) -> dict[str, Any]:
    ext = p.get("externalIds") or {}
    url = p.get("url") or (f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv")
                           else (f"https://doi.org/{ext['DOI']}" if ext.get("DOI") else ""))
    authors = [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")]
    return _item(p.get("title", ""), url, p.get("abstract") or "", "semantic_scholar",
                 authors, str(p.get("year") or ""), p.get("citationCount"),
                 paper_id=p.get("paperId"), external_ids=ext,
                 attribution=S2_ATTRIBUTION)


def semantic_scholar_search(query: str, limit: int = 10) -> dict[str, Any]:
    """Relevance paper search. Keyless (5,000 req/5min shared pool); optional
    SEMANTIC_SCHOLAR_API_KEY -> 1 RPS dedicated. The killer feature is the citation
    graph (see semantic_scholar_citations)."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "semantic_scholar", "results": []}
    limit = max(1, min(int(limit), 100))
    r = _get_json(f"{S2_API}/paper/search",
                  params={"query": query, "limit": limit, "fields": _S2_FIELDS},
                  headers=_s2_headers())
    if not r.get("ok"):
        return {"ok": False, "source": "semantic_scholar", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = [_s2_paper_to_item(p) for p in (data.get("data") or [])]
    otel_emit.record("source_semantic_scholar", {"query": query, "results": len(results),
                                                  "keyed": bool(SEMANTIC_SCHOLAR_API_KEY)})
    return {"ok": True, "source": "semantic_scholar", "results": results,
            "attribution": S2_ATTRIBUTION}


def semantic_scholar_citations(paper_id: str, direction: str = "references",
                               limit: int = 25) -> dict[str, Any]:
    """Citation-graph traversal — the feature that turns search into 'find the
    canonical + frontier of a topic'. direction='references' -> backward (papers
    THIS one cites -> seminal); 'citations' -> forward (papers citing THIS ->
    latest). paper_id may be an S2 id, 'arXiv:2106.01345', 'DOI:...', etc."""
    paper_id = (paper_id or "").strip()
    if not paper_id:
        return {"ok": False, "source": "semantic_scholar", "error": "empty paper_id", "results": []}
    direction = direction if direction in ("references", "citations") else "references"
    limit = max(1, min(int(limit), 100))
    nested = "citedPaper" if direction == "references" else "citingPaper"
    r = _get_json(f"{S2_API}/paper/{quote(paper_id, safe=':')}/{direction}",
                  params={"limit": limit, "fields": _S2_FIELDS}, headers=_s2_headers())
    if not r.get("ok"):
        return {"ok": False, "source": "semantic_scholar", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = []
    for edge in (data.get("data") or []):
        paper = edge.get(nested) or {}
        if paper:
            it = _s2_paper_to_item(paper)
            it["extra"]["edge"] = "cites" if direction == "references" else "cited_by"
            results.append(it)
    otel_emit.record("source_s2_citation_graph", {"paper_id": paper_id, "direction": direction,
                                                  "results": len(results)})
    return {"ok": True, "source": "semantic_scholar", "direction": direction,
            "results": results, "attribution": S2_ATTRIBUTION}


# ── GitHub search (PAT-gated; repos / code / issues) ──────────────────────────
def _github_headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def github_search(query: str, search_type: str = "repositories",
                  limit: int = 10) -> dict[str, Any]:
    """GitHub REST search over repositories / code / issues. Presence-gated on
    GITHUB_TOKEN — absent => no-op {"skipped": true} (the web layer still answers).
    With a free PAT: 30 req/min search, ~9 req/min code-search."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "github", "results": []}
    if not GITHUB_TOKEN:
        return {"ok": True, "source": "github", "skipped": True, "results": [],
                "error": "GITHUB_TOKEN/GITHUB_ACCESS_TOKEN absent — github_search skipped (web layer covers it)"}
    search_type = search_type if search_type in ("repositories", "code", "issues") else "repositories"
    limit = max(1, min(int(limit), 50))
    r = _get_json(f"{GITHUB_API}/search/{search_type}",
                  params={"q": query, "per_page": limit}, headers=_github_headers())
    if not r.get("ok"):
        return {"ok": False, "source": "github", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results: list[dict[str, Any]] = []
    for it in (data.get("items") or [])[:limit]:
        if search_type == "repositories":
            results.append(_item(it.get("full_name", ""), it.get("html_url", ""),
                                 it.get("description") or "", "github_repo",
                                 [(it.get("owner") or {}).get("login", "")],
                                 (it.get("pushed_at") or "")[:10],
                                 None, stars=it.get("stargazers_count")))
        elif search_type == "code":
            repo = (it.get("repository") or {}).get("full_name", "")
            results.append(_item(f"{repo}:{it.get('path','')}", it.get("html_url", ""),
                                 it.get("path") or "", "github_code", [], "", None, repo=repo))
        else:  # issues (and PRs)
            results.append(_item(it.get("title", ""), it.get("html_url", ""),
                                 (it.get("body") or "")[:2000], "github_issue",
                                 [(it.get("user") or {}).get("login", "")],
                                 (it.get("created_at") or "")[:10], None,
                                 comments=it.get("comments"), state=it.get("state")))
    otel_emit.record("source_github", {"query": query, "type": search_type, "results": len(results)})
    return {"ok": True, "source": "github", "results": results}


# ── Hacker News (Algolia; keyless) ────────────────────────────────────────────
def hn_search(query: str, limit: int = 10, tags: str = "story") -> dict[str, Any]:
    """HN search via Algolia (keyless). tags='story' for submissions; comments and
    discussion via the item URL. Good signal for what practitioners actually use."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "hn", "results": []}
    limit = max(1, min(int(limit), 50))
    r = _get_json(HN_API, params={"query": query, "tags": tags, "hitsPerPage": limit})
    if not r.get("ok"):
        return {"ok": False, "source": "hn", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = []
    for h in (data.get("hits") or [])[:limit]:
        oid = h.get("objectID", "")
        url = h.get("url") or (f"https://news.ycombinator.com/item?id={oid}" if oid else "")
        results.append(_item(h.get("title") or h.get("story_title") or "", url,
                             (h.get("story_text") or h.get("comment_text") or "")[:1000],
                             "hn", [h.get("author", "")], (h.get("created_at") or "")[:10],
                             None, points=h.get("points"), num_comments=h.get("num_comments"),
                             hn_url=f"https://news.ycombinator.com/item?id={oid}"))
    otel_emit.record("source_hn", {"query": query, "results": len(results)})
    return {"ok": True, "source": "hn", "results": results}


# ── Stack Exchange (keyless 300/day; optional key) ────────────────────────────
def stackexchange_search(query: str, site: str = "stackoverflow",
                         limit: int = 10) -> dict[str, Any]:
    """Q&A with votes/tags. Keyless = 300 req/day; STACKEXCHANGE_KEY -> 10k/day.
    Medium value — the classifier routes it for library/how-to queries."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "stackexchange", "results": []}
    limit = max(1, min(int(limit), 50))
    params = {"order": "desc", "sort": "relevance", "q": query, "site": site,
              "pagesize": limit, "filter": "withbody"}
    if STACKEXCHANGE_KEY:
        params["key"] = STACKEXCHANGE_KEY
    r = _get_json(f"{SE_API}/search/advanced", params=params)
    if not r.get("ok"):
        return {"ok": False, "source": "stackexchange", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = []
    for q in (data.get("items") or [])[:limit]:
        body = re.sub(r"<[^>]+>", " ", q.get("body") or "")[:1500]
        results.append(_item(q.get("title", ""), q.get("link", ""), body, "stackexchange",
                             [(q.get("owner") or {}).get("display_name", "")],
                             "", None, score=q.get("score"), tags=q.get("tags"),
                             is_answered=q.get("is_answered")))
    otel_emit.record("source_stackexchange", {"query": query, "results": len(results)})
    return {"ok": True, "source": "stackexchange", "results": results}


# ══ STAGE 2: crypto / standards adapters (keyless; degrade to web) ════════════
ETHRESEARCH_BASE = "https://ethresear.ch"
EIPS_RAW = "https://raw.githubusercontent.com/ethereum/EIPs/master/EIPS"
ERCS_RAW = "https://raw.githubusercontent.com/ethereum/ERCs/master/ERCS"
RFC_EDITOR = "https://www.rfc-editor.org/rfc"


def _front_matter(md: str) -> tuple[dict[str, str], str]:
    """Split YAML-ish front-matter (--- ... ---) from a markdown body. Tolerant:
    no real YAML parser needed for EIP/RFC headers (flat key: value lines)."""
    fm: dict[str, str] = {}
    body = md
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", md, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip().lower()] = v.strip()
        body = m.group(2)
    return fm, body


def ethresearch_search(query: str, limit: int = 8) -> dict[str, Any]:
    """ethresear.ch is Discourse — public read needs NO auth (append .json). Uses
    /search.json to find topics, joining matched posts to their topics so each
    result carries a real blurb + the canonical topic URL. The Ethereum-research
    frontier consumer tools miss. Degrades to an error string if unreachable."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "ethresearch", "results": []}
    limit = max(1, min(int(limit), 50))
    r = _get_json(f"{ETHRESEARCH_BASE}/search.json", params={"q": query})
    if not r.get("ok"):
        return {"ok": False, "source": "ethresearch", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    topics = {t.get("id"): t for t in (data.get("topics") or [])}
    blurbs: dict[int, dict] = {}
    for p in (data.get("posts") or []):
        tid = p.get("topic_id")
        if tid is not None and tid not in blurbs:
            blurbs[tid] = p
    results: list[dict[str, Any]] = []
    for tid, t in topics.items():
        if len(results) >= limit:
            break
        slug = t.get("slug", "")
        url = f"{ETHRESEARCH_BASE}/t/{slug}/{tid}" if slug else f"{ETHRESEARCH_BASE}/t/{tid}"
        post = blurbs.get(tid, {})
        results.append(_item(t.get("title", ""), url, post.get("blurb") or "", "ethresearch",
                             [post.get("username", "")] if post.get("username") else [],
                             (t.get("created_at") or "")[:10], None,
                             posts_count=t.get("posts_count")))
    otel_emit.record("source_ethresearch", {"query": query, "results": len(results)})
    return {"ok": True, "source": "ethresearch", "results": results}


def ethresearch_topic(topic_id: int | str, slug: str = "") -> dict[str, Any]:
    """Fetch a single ethresear.ch topic's FULL post text (no auth). Returns the
    concatenated post bodies — used to pull a frontier discussion in full."""
    tid = str(topic_id).strip()
    if not tid:
        return {"ok": False, "source": "ethresearch", "error": "empty topic_id", "results": []}
    path = f"/t/{slug}/{tid}.json" if slug else f"/t/{tid}.json"
    r = _get_json(f"{ETHRESEARCH_BASE}{path}")
    if not r.get("ok"):
        return {"ok": False, "source": "ethresearch", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    posts = ((data.get("post_stream") or {}).get("posts") or [])
    body = "\n\n".join(re.sub(r"<[^>]+>", " ", p.get("cooked") or "") for p in posts)
    url = f"{ETHRESEARCH_BASE}/t/{data.get('slug', slug)}/{tid}"
    it = _item(data.get("title", ""), url, body.strip(), "ethresearch",
               sorted({p.get("username", "") for p in posts if p.get("username")}),
               (data.get("created_at") or "")[:10], None, posts=len(posts))
    return {"ok": True, "source": "ethresearch", "results": [it]}


_EIP_RE = re.compile(r"\beip[-\s]?(\d{1,5})\b", re.IGNORECASE)
_ERC_RE = re.compile(r"\berc[-\s]?(\d{1,5})\b", re.IGNORECASE)


def eip_erc(query: str, limit: int = 6) -> dict[str, Any]:
    """Read ethereum/EIPs + ethereum/ERCs FULL spec text. If the query names a
    number (EIP-4844, ERC-20) the raw markdown is fetched KEYLESS from
    raw.githubusercontent.com (front-matter parsed). With no number AND a
    GITHUB_TOKEN present, falls back to a code-search in the repos; otherwise no-ops
    cleanly. High value for crypto — full canonical spec, not a blog summary."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "eip_erc", "results": []}
    limit = max(1, min(int(limit), 25))
    results: list[dict[str, Any]] = []
    wanted = ([("eip", n, f"{EIPS_RAW}/eip-{n}.md", f"https://eips.ethereum.org/EIPS/eip-{n}")
               for n in _EIP_RE.findall(query)]
              + [("erc", n, f"{ERCS_RAW}/erc-{n}.md", f"https://eips.ethereum.org/EIPS/eip-{n}")
                 for n in _ERC_RE.findall(query)])
    for kind, n, raw_url, canon in wanted[:limit]:
        r = _get_text(raw_url)
        if not r.get("ok"):
            continue
        fm, body = _front_matter(r["text"])
        title = fm.get("title", f"{kind.upper()}-{n}")
        authors = [a.strip() for a in re.split(r",|;", fm.get("author", "")) if a.strip()]
        results.append(_item(f"{kind.upper()}-{n}: {title}", canon, body.strip(),
                             "eip_erc", authors, fm.get("created", ""), None,
                             status=fm.get("status"), eip_type=fm.get("type"),
                             category=fm.get("category"), number=int(n)))
    if not results and GITHUB_TOKEN and not wanted:
        # no explicit number — search the repos by content (presence-gated)
        gh = github_search(f"repo:ethereum/EIPs OR repo:ethereum/ERCs {query}", "code", limit)
        if gh.get("ok"):
            for it in gh.get("results", []):
                it = dict(it); it["source_type"] = "eip_erc"
                results.append(it)
    otel_emit.record("source_eip_erc", {"query": query, "results": len(results),
                                        "numbered": len(wanted)})
    return {"ok": True, "source": "eip_erc", "results": results}


_RFC_RE = re.compile(r"\brfc[-\s]?(\d{1,5})\b", re.IGNORECASE)


def ietf_rfc(query: str, limit: int = 5) -> dict[str, Any]:
    """IETF RFC full text (keyless, RFC-Editor). If the query names an RFC number
    its full text is fetched directly. Optional/deferred per spec — routed only
    when the query mentions 'rfc'/'ietf'. Degrades to an error string."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "ietf_rfc", "results": []}
    limit = max(1, min(int(limit), 15))
    results: list[dict[str, Any]] = []
    for n in _RFC_RE.findall(query)[:limit]:
        r = _get_text(f"{RFC_EDITOR}/rfc{n}.txt")
        if not r.get("ok"):
            continue
        text = r["text"]
        title = next((ln.strip() for ln in text.splitlines()[:40] if len(ln.strip()) > 10), f"RFC {n}")
        results.append(_item(f"RFC {n}: {title}"[:160], f"{RFC_EDITOR}/rfc{n}",
                             text[:60000], "ietf_rfc", [], "", None, number=int(n)))
    otel_emit.record("source_ietf_rfc", {"query": query, "results": len(results)})
    return {"ok": True, "source": "ietf_rfc", "results": results}


# ── RRF fusion (pure arithmetic, no model) ────────────────────────────────────
def rrf_fuse(ranked_lists: list[list[dict[str, Any]]], k: int = RRF_K,
             key: str = "url") -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank_in_list). Rewards items
    that rank consistently across multiple sources. Pure arithmetic — the biggest
    robustness-per-effort win when several adapters return ranked lists."""
    scores: dict[str, float] = {}
    merged: dict[str, dict[str, Any]] = {}
    contributing: dict[str, set[str]] = {}
    for lst in ranked_lists or []:
        for rank, item in enumerate(lst or []):
            kv = (item.get(key) or "").strip().lower()
            if not kv:
                continue
            scores[kv] = scores.get(kv, 0.0) + 1.0 / (k + rank + 1)
            contributing.setdefault(kv, set()).add(item.get("source_type", "web"))
            if kv not in merged:
                merged[kv] = dict(item)
            elif not merged[kv].get("content") and item.get("content"):
                merged[kv] = dict(item)  # prefer the richer copy
    out = []
    for kv, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        it = dict(merged[kv])
        it["_rrf_score"] = round(sc, 6)
        it["_rrf_sources"] = sorted(contributing[kv])
        out.append(it)
    return out


# ── classifier-router (keyword heuristic; always includes searxng) ────────────
_CRYPTO_KW = ("zero-knowledge", "zk-", "zksnark", "zk-snark", "snark", "stark", "ethereum",
              "eip-", "erc-", "evm", "solidity", "rollup", "consensus", "validator", "mev",
              "blockchain", "cryptograph", "elliptic curve", "merkle", "proof of stake",
              "smart contract", "danksharding", "blob", "calldata", "plonk", "kzg",
              "protocol", "p2p", "byzantine", "bls signature", "vrf")
_ML_KW = ("neural", "transformer", "llm", "language model", "deep learning", "diffusion",
          "embedding", "fine-tun", "reinforcement learning", "rlhf", "gradient", "dataset",
          "benchmark", "attention", "quantization", "inference", "pretrain", "model weights",
          "convolution", "gan", "vae", "tokeniz")
_LIB_KW = ("how to", "install", "error", "exception", "traceback", "library", "package",
           "api usage", "tutorial", "example", "import", "deprecated", "version", "config",
           "command", "cli", "function", "method", "syntax", "stack trace")


def classify_query(query: str) -> dict[str, Any]:
    """Map a query to a source set + per-source budget. crypto/protocol ->
    arXiv(cs.CR) + github + ethresearch + eip + searxng; applied-ML -> arXiv(cs.LG)
    + semantic_scholar + github + hn; library-how-to -> github + searxng + hn +
    stackexchange. Always includes searxng as catch-all. Bounded budgets prevent
    overspawning. (ethresearch/eip adapters arrive in Stage 2; the router names
    them now and source_fanout simply skips unregistered sources.)"""
    q = (query or "").lower()
    crypto = sum(1 for kw in _CRYPTO_KW if kw in q)
    ml = sum(1 for kw in _ML_KW if kw in q)
    lib = sum(1 for kw in _LIB_KW if kw in q)

    if crypto and crypto >= ml:
        category = "crypto"
        budgets = {"arxiv": 6, "github": 5, "ethresearch": 5, "eip_erc": 3,
                   "semantic_scholar": 4, "searxng": 6}
        arxiv_cats = ["cs.CR", "cs.DC"]
    elif ml and ml >= lib:
        category = "applied_ml"
        budgets = {"arxiv": 6, "semantic_scholar": 8, "github": 5, "hn": 4, "searxng": 6}
        arxiv_cats = ["cs.LG", "cs.AI"]
    elif lib:
        category = "library"
        budgets = {"github": 6, "searxng": 8, "hn": 4, "stackexchange": 6}
        arxiv_cats = []
    else:
        category = "general"
        budgets = {"searxng": 8, "semantic_scholar": 4, "github": 4, "hn": 4}
        arxiv_cats = []

    # protocol-standards signal (RFC/IETF) — keyless, only routed on demand.
    if ("rfc" in q or "ietf" in q) and "ietf_rfc" not in budgets:
        budgets["ietf_rfc"] = 4

    sources = list(budgets.keys())
    if "searxng" not in sources:  # invariant: web is always the catch-all
        sources.append("searxng")
        budgets["searxng"] = 6
    return {"ok": True, "query": query, "category": category, "sources": sources,
            "budgets": budgets, "arxiv_categories": arxiv_cats}


# ── source registry + fan-out orchestrator ────────────────────────────────────
def _registry() -> dict[str, Any]:
    """name -> adapter callable. Stage 2 extends this (ethresearch, eip_erc, ...).
    Resolved at call time so monkeypatched adapters in tests are honored."""
    import sys
    mod = sys.modules[__name__]
    return {
        "arxiv": mod.arxiv_search,
        "semantic_scholar": mod.semantic_scholar_search,
        "github": mod.github_search,
        "hn": mod.hn_search,
        "stackexchange": mod.stackexchange_search,
        # Stage 2 — crypto / standards
        "ethresearch": mod.ethresearch_search,
        "eip_erc": mod.eip_erc,
        "ietf_rfc": mod.ietf_rfc,
    }


def source_fanout(query: str, sources: list[str] | None = None,
                  fuse: bool = True) -> dict[str, Any]:
    """Classify -> call each routed STRUCTURED adapter with its budget -> RRF-fuse.
    'searxng' / unregistered names (e.g. ethresearch before Stage 2) are skipped
    here — the existing web layer (research_core.explore) covers searxng. No source
    is load-bearing: a fully-down structured layer returns an empty fused list and
    the caller falls back to web. Errors are collected per-source, never raised."""
    routing = classify_query(query)
    sources = sources or routing["sources"]
    budgets = routing["budgets"]
    reg = _registry()
    per_source: dict[str, dict[str, Any]] = {}
    ranked_lists: list[list[dict[str, Any]]] = []
    errors: dict[str, str] = {}
    skipped: list[str] = []
    for name in sources:
        if name == "searxng" or name not in reg:
            if name != "searxng":
                skipped.append(name)  # not yet registered (Stage 2+)
            continue
        budget = budgets.get(name, 5)
        try:
            if name == "arxiv":
                res = reg[name](query, days_back=None, categories=routing["arxiv_categories"], limit=budget)
            else:
                res = reg[name](query, limit=budget)
        except Exception as e:  # noqa: BLE001 — belt-and-suspenders; adapters shouldn't raise
            res = {"ok": False, "source": name, "error": f"{type(e).__name__}: {e}", "results": []}
        per_source[name] = {"ok": res.get("ok"), "count": len(res.get("results", [])),
                            "skipped": res.get("skipped", False), "error": res.get("error")}
        if res.get("error") and not res.get("skipped"):
            errors[name] = res["error"]
        if res.get("results"):
            ranked_lists.append(res["results"])
    fused = rrf_fuse(ranked_lists) if fuse else [it for lst in ranked_lists for it in lst]
    otel_emit.record("source_fanout", {"query": query, "category": routing["category"],
                                       "sources": len([s for s in sources if s in reg]),
                                       "results": len(fused), "errors": len(errors)})
    if fuse:
        otel_emit.record("rrf_fused", {"lists": len(ranked_lists), "results": len(fused)})
    return {"ok": True, "query": query, "category": routing["category"],
            "routed_sources": sources, "per_source": per_source, "skipped": skipped,
            "errors": errors, "count": len(fused), "results": fused,
            "attribution": S2_ATTRIBUTION if any(s == "semantic_scholar" for s in sources) else None}


def source_stats() -> dict[str, Any]:
    return {
        "arxiv": "keyless (Atom API)",
        "semantic_scholar": "keyed (1 RPS)" if SEMANTIC_SCHOLAR_API_KEY else "keyless (5k/5min pool)",
        "github": "PAT (30 req/min)" if GITHUB_TOKEN else "SKIPPED (no GITHUB_TOKEN/GITHUB_ACCESS_TOKEN)",
        "hn": "keyless (Algolia)",
        "stackexchange": "keyed (10k/day)" if STACKEXCHANGE_KEY else "keyless (300/day)",
        "ethresearch": "keyless (Discourse .json)",
        "eip_erc": "keyless (raw.githubusercontent) + optional github code-search",
        "ietf_rfc": "keyless (RFC-Editor)",
        "rrf_k": RRF_K,
        "registered": sorted(_registry().keys()),
    }
```

#### `mcp-research/banyan.py`
```python
"""Stage 6 — Banyan content-evolution (the long-horizon autonomy half).

THE HARD LINE, enforced in code: this loop may evolve CONTENT — which research
directions to explore, the RAG corpus, the KG, and the skill library — but NEVER
MACHINERY (no mcp-* server code, no Hermes core, no router, no tool .py/config). A
content write is allowed ONLY to a whitelisted root (corpus / skills / banyan state)
with a content extension (.md/.json/.jsonl/.txt); `_guard_content_write` refuses
anything else, and smoke_banyan.py asserts a full cycle writes no machinery file.

Pieces (all over the EXISTING namespaces/KG/RAG/skills — no new machinery):
  * banyan_select  — UCB1 explore-exploit over research namespaces:
       U_i = utility*priority + c*sqrt(ln(N)/n_i).  Unvisited namespaces get an
       infinite exploration bonus (visited despite lower utility). A pending human
       DIRECTIVE preempts selection (operator-in-the-loop seam).
  * banyan_update  — visit_count++, running utility (0.8 history / 0.2 new),
       marginal-gain history (last 20).
  * saturation     — two signals: (1) embedding-drift (new research too similar to
       the namespace corpus centroid => retreading) and (2) marginal-gain decline
       (last 10 trending down AND below threshold). On saturation: flag, STOP
       investing, and SURFACE TO THE OPERATOR — never silently churn.
  * standing tasks — when a namespace queue empties, generate research tasks (e.g.
       "what's new in {ns} since {last_ingest}") so unattended cycles never idle.
  * skill evolution — may write/refine markdown SKILLS (content), gated behind the
       maturity check (SELF_IMPROVEMENT_ENABLED + 200 tasks / 30 days / 50 skills).

Never raises; persistent state is JSON on disk under BANYAN_STATE_DIR.
"""
from __future__ import annotations

import datetime
import json
import math
import os
from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import rank  # rank._embed / rank._cosine (shared embedding endpoint)

# ── config ────────────────────────────────────────────────────────────────────
BANYAN_STATE_DIR = os.path.expanduser(os.environ.get("BANYAN_STATE_DIR", "~/.hermes-max/banyan"))
SKILLS_DIR = os.path.expanduser(os.environ.get("BANYAN_SKILLS_DIR", "~/.hermes-max/skills"))
STATE_FILE = os.path.join(BANYAN_STATE_DIR, "state.json")
DIRECTIVE_FILE = os.path.join(BANYAN_STATE_DIR, "directive.json")
SURFACED_LOG = os.path.join(BANYAN_STATE_DIR, "surfaced.jsonl")

UCB_C = float(os.environ.get("BANYAN_UCB_C", "1.414"))
GAIN_HISTORY_MAX = 20
SATURATION_DRIFT_COSINE = float(os.environ.get("BANYAN_DRIFT_COSINE", "0.95"))  # >= => too similar
SATURATION_GAIN_FLOOR = float(os.environ.get("BANYAN_GAIN_FLOOR", "0.05"))
# Empty-base correctness: never flag a namespace SATURATED on thin data — saturation
# detection is DISABLED below this many recorded tasks/namespace (Stage-6 gate).
SATURATION_MIN_HISTORY = int(os.environ.get("BANYAN_SATURATION_MIN_HISTORY", "10"))
# RISK-A remedy (Stage-6): UCB1 is a stationary-bandit explorer — good for RESEARCH
# breadth, bad for BUILD-loop focus (it abandons half-finished hard subtasks for
# shinier easy ones). BANYAN_SCOPE scopes UCB1:
#   research_only (DEFAULT) — UCB1 governs research-namespace selection ONLY; the
#       build loop uses finish-what-you-started / dependency-order (select_build_subtask).
#   all — UCB1 governs both loops (the thrash-prone behaviour; kept for A/B eval).
BANYAN_SCOPE = os.environ.get("BANYAN_SCOPE", "research_only").strip().lower()
SELF_IMPROVEMENT_ENABLED = os.environ.get("SELF_IMPROVEMENT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
MATURITY_MIN_TASKS = int(os.environ.get("BANYAN_MIN_TASKS", "200"))
MATURITY_MIN_DAYS = int(os.environ.get("BANYAN_MIN_DAYS", "30"))
MATURITY_MIN_SKILLS = int(os.environ.get("BANYAN_MIN_SKILLS", "50"))

# Content-write whitelist (the machinery guard).
_CONTENT_EXT = (".md", ".json", ".jsonl", ".txt")
_CONTENT_ROOTS = (BANYAN_STATE_DIR, SKILLS_DIR,
                  os.path.expanduser(os.environ.get("RESEARCH_CORPUS_DIR", "~/.hermes-max/corpus")))


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ══ THE MACHINERY GUARD ═══════════════════════════════════════════════════════
def is_machinery_path(path: str) -> bool:
    """True if `path` is MACHINERY (must never be written by the loop): any code/
    config (.py/.yaml/.toml/.cfg/.ini/.sh/.txt-outside-content), or anything under
    an mcp-* server dir / lib / serving / scripts. Used by both the guard and the
    Stage-6 no-machinery-write assertion."""
    ap = os.path.abspath(path)
    if ap.endswith((".py", ".pyc", ".pyi", ".yaml", ".yml", ".toml", ".cfg", ".ini",
                    ".sh", ".lock", ".so")):
        return True
    parts = ap.split(os.sep)
    if any(p.startswith("mcp-") for p in parts) or any(
            p in ("lib", "serving", "scripts", "migration", "hermes-config", "kg") for p in parts):
        return True
    return False


def _guard_content_write(path: str) -> dict[str, Any]:
    """Allow a write ONLY to a whitelisted content root with a content extension and
    NOT a machinery path. Returns {ok} or {ok:False, error} — callers refuse on False."""
    ap = os.path.abspath(path)
    if is_machinery_path(ap):
        return {"ok": False, "error": f"refused: '{path}' is MACHINERY (loop evolves content only)"}
    if not ap.endswith(_CONTENT_EXT):
        return {"ok": False, "error": f"refused: '{path}' is not a content file {_CONTENT_EXT}"}
    if not any(ap.startswith(os.path.abspath(r)) for r in _CONTENT_ROOTS):
        return {"ok": False, "error": f"refused: '{path}' outside content roots"}
    return {"ok": True}


def _write_content(path: str, text: str) -> dict[str, Any]:
    g = _guard_content_write(path)
    if not g["ok"]:
        otel_emit.record("machinery_write_refused", {"path": path})
        return g
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": path}


# ── persistent state ──────────────────────────────────────────────────────────
def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"namespaces": {}}


def _save_state(state: dict[str, Any]) -> dict[str, Any]:
    return _write_content(STATE_FILE, json.dumps(state, indent=2))


def _ns(state: dict, name: str) -> dict[str, Any]:
    return state["namespaces"].setdefault(name, {
        "visit_count": 0, "utility": 0.0, "priority": 1.0, "gain_history": [],
        "saturated": False, "last_ingest": None, "queue": [], "centroid": None})


def register_namespace(name: str, priority: float = 1.0) -> dict[str, Any]:
    state = _load_state()
    ns = _ns(state, name)
    ns["priority"] = float(priority)
    _save_state(state)
    return {"ok": True, "namespace": name, "priority": ns["priority"]}


# ── directive interrupt (operator-in-the-loop seam) ───────────────────────────
def set_directive(text: str, namespace: str | None = None) -> dict[str, Any]:
    """Operator drops a directive; it preempts UCB1 on the next cycle."""
    return _write_content(DIRECTIVE_FILE, json.dumps(
        {"directive": text, "namespace": namespace, "set_at": _now_iso()}))


def pending_directive() -> dict[str, Any] | None:
    try:
        with open(DIRECTIVE_FILE) as f:
            d = json.load(f)
        return d if d.get("directive") else None
    except Exception:  # noqa: BLE001
        return None


def clear_directive() -> dict[str, Any]:
    try:
        if os.path.exists(DIRECTIVE_FILE):
            os.remove(DIRECTIVE_FILE)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


# ── UCB1 selection ─────────────────────────────────────────────────────────────
def banyan_select(c: float = UCB_C) -> dict[str, Any]:
    """Pick the next research direction. A pending DIRECTIVE preempts (human steer).
    Otherwise UCB1 over NON-saturated namespaces: unvisited => infinite exploration
    bonus (picked first); else utility*priority + c*sqrt(ln(N)/n_i)."""
    directive = pending_directive()
    if directive:
        otel_emit.record("directive_interrupt", {"namespace": directive.get("namespace")})
        return {"ok": True, "mode": "directive", "directive": directive["directive"],
                "namespace": directive.get("namespace"), "preempted_ucb1": True}

    state = _load_state()
    candidates = {n: v for n, v in state["namespaces"].items() if not v.get("saturated")}
    if not candidates:
        return {"ok": True, "mode": "idle", "namespace": None,
                "reason": "no non-saturated namespaces"}
    total_visits = sum(v["visit_count"] for v in candidates.values())
    N = max(1, total_visits)
    scores: dict[str, float] = {}
    for name, v in candidates.items():
        n_i = v["visit_count"]
        if n_i == 0:
            scores[name] = float("inf")  # explore the unvisited first
            continue
        exploit = v["utility"] * v.get("priority", 1.0)
        explore = c * math.sqrt(math.log(N) / n_i)
        scores[name] = exploit + explore
    chosen = max(scores, key=lambda k: scores[k])
    otel_emit.record("banyan_selected", {"namespace": chosen,
                                         "ucb_score": None if scores[chosen] == float("inf") else round(scores[chosen], 4),
                                         "visits": candidates[chosen]["visit_count"]})
    return {"ok": True, "mode": "explore", "namespace": chosen,
            "ucb_scores": {k: ("inf" if s == float("inf") else round(s, 4)) for k, s in scores.items()},
            "visit_count": candidates[chosen]["visit_count"]}


# ── RISK-A remedy: BUILD-loop selection (finish-what-you-started, NOT UCB1) ────
def select_build_subtask(subtasks: list[dict], in_progress: str | None = None) -> dict[str, Any]:
    """Pick the next BUILD subtask WITHOUT UCB1 — the build loop needs sustained
    focus, so coherent building is finish-what-you-started then dependency-order:
      1. if a subtask is already in progress and incomplete, KEEP it (never switch
         away from half-finished work for a shinier one — the anti-thrash rule);
      2. else the first INCOMPLETE subtask whose deps are all complete (dep order);
      3. else None (all complete / blocked).
    Each subtask: {id, status:'complete'|'incomplete', deps:[ids]}. This is what
    BANYAN_SCOPE=research_only routes the build loop to, instead of banyan_select()."""
    by_id = {t["id"]: t for t in subtasks}

    def done(tid: str) -> bool:
        return by_id.get(tid, {}).get("status") == "complete"

    incomplete = [t for t in subtasks if t.get("status") != "complete"]
    if not incomplete:
        return {"ok": True, "subtask": None, "reason": "all subtasks complete", "switched": False}
    if in_progress and in_progress in by_id and not done(in_progress):
        return {"ok": True, "subtask": in_progress, "switched": False,
                "strategy": "finish_in_progress", "reason": "finish-what-you-started (no switch)"}
    ready = [t for t in incomplete if all(done(d) for d in t.get("deps", []))]
    pick = (ready or incomplete)[0]
    return {"ok": True, "subtask": pick["id"], "strategy": "dependency_order",
            "switched": bool(in_progress and in_progress != pick["id"]),
            "reason": "dependency-order (deps satisfied)" if ready else "oldest incomplete (deps unmet)"}


def select_next(loop: str, *, subtasks: list[dict] | None = None,
                in_progress: str | None = None, c: float = UCB_C) -> dict[str, Any]:
    """Route selection by loop + BANYAN_SCOPE (the RISK-A config split):
      • BANYAN_SCOPE=research_only (DEFAULT): the BUILD loop uses finish-what-you-
        started (select_build_subtask, no UCB1 thrash); RESEARCH uses UCB1.
      • BANYAN_SCOPE=all: UCB1 (banyan_select) governs BOTH loops (thrash-prone)."""
    if loop == "build" and BANYAN_SCOPE == "research_only":
        out = select_build_subtask(subtasks or [], in_progress)
        out["selector"] = "build:finish-what-you-started"
        return out
    sel = banyan_select(c)
    sel["selector"] = f"{loop}:ucb1"
    return sel


# ── update after a task completes ──────────────────────────────────────────────
def banyan_update(namespace: str, utility_sample: float, gain: float) -> dict[str, Any]:
    """After a research/skill task: visit_count++, running utility (0.8 hist / 0.2
    new), append marginal gain (keep last 20)."""
    state = _load_state()
    ns = _ns(state, namespace)
    ns["visit_count"] += 1
    ns["utility"] = round(0.8 * ns["utility"] + 0.2 * float(utility_sample), 6)
    ns["gain_history"] = (ns["gain_history"] + [round(float(gain), 6)])[-GAIN_HISTORY_MAX:]
    ns["last_ingest"] = _now_iso()
    _save_state(state)
    otel_emit.record("banyan_updated", {"namespace": namespace, "visits": ns["visit_count"],
                                        "utility": ns["utility"]})
    return {"ok": True, "namespace": namespace, "visit_count": ns["visit_count"],
            "utility": ns["utility"], "gain_history_len": len(ns["gain_history"])}


# ── saturation detection (two signals) + surface to operator ──────────────────
def surface_to_operator(message: str, detail: dict | None = None) -> dict[str, Any]:
    """Append to the sovereign operator-surface log (Telegram optional on top). This
    is how saturation/decisions reach a human — never silently churned."""
    line = json.dumps({"at": _now_iso(), "message": message, "detail": detail or {}})
    try:
        os.makedirs(os.path.dirname(SURFACED_LOG), exist_ok=True)
        with open(SURFACED_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
    otel_emit.record("operator_surfaced", {"message": message})
    return {"ok": True, "surfaced": True, "message": message}


def detect_saturation(namespace: str, new_texts: list[str] | None = None) -> dict[str, Any]:
    """Two signals. (1) embedding-drift: new research too SIMILAR to the namespace
    corpus centroid (mean cosine >= drift threshold => retreading). (2) marginal-gain
    decline: last 10 gains trending down AND mean below the floor. On saturation:
    flag, STOP investing, SURFACE TO OPERATOR. (Spec's '< threshold' wording is the
    inverse of 'too similar'; implemented as high-similarity = retreading.)"""
    state = _load_state()
    ns = _ns(state, namespace)
    reasons: list[str] = []
    # Empty-base gate: below the minimum history we still SEED the centroid (so drift
    # works once mature) but NEVER flag saturated — thin data must not stop investment.
    enough_history = ns.get("visit_count", 0) >= SATURATION_MIN_HISTORY

    # (1) embedding drift vs stored centroid
    drift_sim = None
    if new_texts:
        vecs = rank._embed([t for t in new_texts if t and t.strip()])
        if vecs:
            new_centroid = [sum(col) / len(vecs) for col in zip(*vecs)]
            if ns.get("centroid"):
                drift_sim = rank._cosine(new_centroid, ns["centroid"])
                if drift_sim >= SATURATION_DRIFT_COSINE:
                    reasons.append(f"embedding-drift: mean cosine {drift_sim:.3f} >= {SATURATION_DRIFT_COSINE} (retreading)")
            ns["centroid"] = new_centroid  # update running centroid

    # (2) marginal-gain decline
    gains = ns["gain_history"][-10:]
    if len(gains) >= 4:
        first_half = sum(gains[:len(gains) // 2]) / (len(gains) // 2)
        second_half = sum(gains[len(gains) // 2:]) / (len(gains) - len(gains) // 2)
        # diminishing returns NOW = recent gains both trending down AND themselves
        # below the floor (so a topic that was hot but has gone quiet is caught).
        if second_half < first_half and second_half < SATURATION_GAIN_FLOOR:
            reasons.append(f"marginal-gain decline: recent {second_half:.3f} < earlier {first_half:.3f} and below floor {SATURATION_GAIN_FLOOR}")

    note = None
    if not enough_history:
        # thin data — suppress any signal; keep investing until history is sufficient
        note = (f"saturation disabled below {SATURATION_MIN_HISTORY} tasks "
                f"(have {ns.get('visit_count', 0)}) — never flag on thin data")
        reasons = []
    saturated = bool(reasons) and enough_history
    if saturated:
        ns["saturated"] = True
        surface_to_operator(f"namespace '{namespace}' SATURATED — stopping investment, awaiting direction",
                            {"namespace": namespace, "reasons": reasons})
        otel_emit.record("saturation_flagged", {"namespace": namespace, "reasons": len(reasons)})
    _save_state(state)
    return {"ok": True, "namespace": namespace, "saturated": saturated, "reasons": reasons,
            "drift_similarity": drift_sim, "note": note,
            "min_history": SATURATION_MIN_HISTORY, "visit_count": ns.get("visit_count", 0)}


# ── standing-task generation (never idle) ─────────────────────────────────────
def generate_standing_tasks(namespace: str) -> dict[str, Any]:
    """When a namespace's queue empties, generate standing RESEARCH tasks (content,
    never machinery) so unattended cycles never idle."""
    state = _load_state()
    ns = _ns(state, namespace)
    if ns["queue"]:
        return {"ok": True, "namespace": namespace, "queue": ns["queue"], "generated": 0}
    since = ns.get("last_ingest") or "the beginning"
    tasks = [f"what's new in {namespace} since {since}",
             f"open problems and contradictions in {namespace}",
             f"most-cited recent work in {namespace}"]
    ns["queue"] = tasks
    _save_state(state)
    otel_emit.record("standing_tasks_generated", {"namespace": namespace, "n": len(tasks)})
    return {"ok": True, "namespace": namespace, "queue": tasks, "generated": len(tasks)}


def pop_task(namespace: str) -> dict[str, Any]:
    state = _load_state()
    ns = _ns(state, namespace)
    task = ns["queue"].pop(0) if ns["queue"] else None
    _save_state(state)
    return {"ok": True, "namespace": namespace, "task": task, "remaining": len(ns["queue"])}


# ── runtime skill evolution (gated, CONTENT only) ─────────────────────────────
def can_evolve_skills(tasks_done: int = 0, days_active: int = 0,
                      skills_count: int = 0) -> dict[str, Any]:
    """Maturity gate — runtime skill evolution stays OFF until the system has earned
    it (SELF_IMPROVEMENT_ENABLED + 200 tasks / 30 days / 50 skills)."""
    reasons = []
    if not SELF_IMPROVEMENT_ENABLED:
        reasons.append("SELF_IMPROVEMENT_ENABLED=false")
    if tasks_done < MATURITY_MIN_TASKS:
        reasons.append(f"tasks {tasks_done}<{MATURITY_MIN_TASKS}")
    if days_active < MATURITY_MIN_DAYS:
        reasons.append(f"days {days_active}<{MATURITY_MIN_DAYS}")
    if skills_count < MATURITY_MIN_SKILLS:
        reasons.append(f"skills {skills_count}<{MATURITY_MIN_SKILLS}")
    return {"ok": True, "allowed": not reasons, "blocking": reasons}


def write_skill(name: str, content: str, tasks_done: int = 0, days_active: int = 0,
                skills_count: int = 0) -> dict[str, Any]:
    """Write/refine a markdown SKILL (content) into the skill library — gated by the
    maturity check AND the machinery guard (a non-.md / machinery path is refused)."""
    gate = can_evolve_skills(tasks_done, days_active, skills_count)
    if not gate["allowed"]:
        return {"ok": False, "error": "skill evolution gated", "blocking": gate["blocking"]}
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name.lower()).strip("-") or "skill"
    path = os.path.join(SKILLS_DIR, f"{slug}.md")
    w = _write_content(path, content)  # guard ensures .md under skills root only
    if w["ok"]:
        otel_emit.record("skill_evolved", {"name": slug})
    return {"ok": w["ok"], "path": w.get("path"), "error": w.get("error")}


# ── one unattended cycle (selection only — the agent runs the research) ───────
def next_action() -> dict[str, Any]:
    """Top of an unattended cycle: directive interrupt OR Banyan self-direction.
    Returns the action for the agent to execute; this module never runs research or
    touches machinery itself."""
    sel = banyan_select()
    if sel.get("mode") == "explore" and sel.get("namespace"):
        st = generate_standing_tasks(sel["namespace"])  # ensure the queue isn't empty
        sel["next_task"] = st["queue"][0] if st["queue"] else None
    return sel


def banyan_stats() -> dict[str, Any]:
    state = _load_state()
    return {"state_dir": BANYAN_STATE_DIR, "skills_dir": SKILLS_DIR,
            "namespaces": {n: {"visits": v["visit_count"], "utility": v["utility"],
                               "saturated": v["saturated"]}
                           for n, v in state.get("namespaces", {}).items()},
            "self_improvement_enabled": SELF_IMPROVEMENT_ENABLED,
            "ucb_c": UCB_C, "content_roots": list(_CONTENT_ROOTS)}
```

#### `mcp-research/extract.py`
```python
"""Stage 4 — extraction ladder (graceful degradation by page type + failure).

A single page-fetch can fail many ways: static articles want a fast CPU extractor,
JS-rendered SPAs need a real browser, blocked/complex pages and PDFs need a hosted
reader. So fetching is a LADDER, picked by page type and fallen through on failure:

  1. Trafilatura  — free, CPU, ms-fast; great on static articles. (import-guarded;
                    absent in this venv => the rung is skipped, ladder starts at 2)
  2. Crawl4AI     — the existing JS-capable extractor, via mcp-docs.fetch_clean
                    (which itself does Crawl4AI -> trafilatura inside mcp-docs).
  3. Jina Reader  — r.jina.ai, rate-limited free (JINA_API_KEY lifts it); the
                    fallback for blocked/complex pages + PDFs.

PDFs and known-JS hosts reorder the ladder (trafilatura is poor on those). Every
rung is best-effort and returns None on failure/empty so the next rung runs; if all
rungs fail the caller still has the SearXNG snippet. Never raises.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc  # rc._fetch -> mcp-docs.fetch_clean (Crawl4AI rung)

JINA_API_KEY = os.environ.get("JINA_API_KEY", "").strip()
JINA_READER = "https://r.jina.ai/"
EXTRACT_TIMEOUT = float(os.environ.get("RESEARCH_EXTRACT_TIMEOUT", "20"))
_JS_HOSTS = ("twitter.com", "x.com", "medium.com", "notion.site", "reddit.com")


# ── rungs (each: url -> markdown|None; monkeypatchable in tests) ──────────────
def _rung_trafilatura(url: str) -> str | None:
    """Fast CPU extraction. Import-guarded: trafilatura may not be installed in
    this venv (it lives in mcp-docs) — then this rung is a clean no-op."""
    try:
        import trafilatura  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        with httpx.Client(timeout=EXTRACT_TIMEOUT, follow_redirects=True) as c:
            html = c.get(url).text
        md = trafilatura.extract(html, output_format="markdown", include_links=True)
        return md.strip() if md and md.strip() else None
    except Exception:  # noqa: BLE001
        return None


def _rung_crawl4ai(url: str) -> str | None:
    """JS-capable extraction via the existing mcp-docs.fetch_clean (Crawl4AI ->
    trafilatura inside mcp-docs). None on failure/empty."""
    try:
        fc = rc._fetch(url)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(fc, dict) and fc.get("ok"):
        md = fc.get("markdown") or ""
        return md.strip() if md.strip() else None
    return None


def _rung_jina(url: str) -> str | None:
    """Hosted reader fallback for blocked/complex pages + PDFs. Rate-limited free;
    JINA_API_KEY lifts the limit. None on failure."""
    try:
        headers = {"Authorization": f"Bearer {JINA_API_KEY}"} if JINA_API_KEY else {}
        with httpx.Client(timeout=EXTRACT_TIMEOUT, follow_redirects=True) as c:
            r = c.get(JINA_READER + quote(url, safe=":/?=&%"), headers=headers)
            r.raise_for_status()
            txt = r.text
        return txt.strip() if txt and txt.strip() else None
    except Exception:  # noqa: BLE001
        return None


_RUNGS = {"trafilatura": _rung_trafilatura, "crawl4ai": _rung_crawl4ai, "jina": _rung_jina}


def _ladder_for(url: str) -> list[str]:
    u = (url or "").lower()
    if u.endswith(".pdf") or "/pdf/" in u:
        return ["jina", "crawl4ai"]            # trafilatura is poor on PDFs
    if any(h in u for h in _JS_HOSTS):
        return ["crawl4ai", "jina", "trafilatura"]  # JS-heavy -> browser first
    return ["trafilatura", "crawl4ai", "jina"]      # static default: fast first


def extract_url(url: str, prefer: list[str] | None = None) -> dict[str, Any]:
    """Run the extraction ladder for a URL, falling through on failure/empty.
    Returns {ok, url, markdown, method, attempts} — attempts records which rungs
    were tried and whether each produced content (observability for the fall-through)."""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "empty url", "url": url, "markdown": "", "attempts": []}
    order = [r for r in (prefer or _ladder_for(url)) if r in _RUNGS]
    attempts: list[dict[str, Any]] = []
    for name in order:
        try:
            md = _RUNGS[name](url)
        except Exception as e:  # noqa: BLE001
            attempts.append({"rung": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            continue
        ok = bool(md)
        attempts.append({"rung": name, "ok": ok, "chars": len(md) if md else 0})
        if ok:
            otel_emit.record("extracted", {"url": url, "method": name, "chars": len(md),
                                           "rungs_tried": len(attempts)})
            return {"ok": True, "url": url, "markdown": md, "method": name, "attempts": attempts}
    otel_emit.record("extract_failed", {"url": url, "rungs_tried": len(attempts)})
    return {"ok": False, "url": url, "markdown": "", "method": None,
            "error": "all extraction rungs failed", "attempts": attempts}


def extract_stats() -> dict[str, Any]:
    try:
        import trafilatura  # noqa: F401
        traf = "available"
    except Exception:  # noqa: BLE001
        traf = "absent (ladder starts at crawl4ai)"
    return {"trafilatura": traf, "crawl4ai": "via mcp-docs.fetch_clean",
            "jina": "keyed" if JINA_API_KEY else "keyless (rate-limited)",
            "ladder_default": ["trafilatura", "crawl4ai", "jina"]}
```

### A.3  mcp-research — ranking, dedup, relevance

#### `mcp-research/rank.py`
```python
"""Stage 4 — semantic dedup + authority ranking + citation-graph edge extraction.

Three quality layers over fanned-out / extracted sources:

  * semantic_dedup — collapse NEAR-duplicates by embedding cosine (not just URL or
    n-gram), so paraphrased SEO mirror content doesn't dominate. Keeps the most
    AUTHORITATIVE instance of each cluster. Degrades to n-gram Jaccard (the existing
    research_core helpers) when the embedding endpoint is unavailable.
  * authority_rank — composite of domain authority (research_core.authority_score)
    + citation count (log-scaled, from Semantic Scholar) + recency. Surfaces an
    arXiv primary over a blog summary of it; anchors to seminal work while still
    rewarding recency for fast-moving fields.
  * citation_edges — turn a paper + its Semantic Scholar references/citations into
    normalized {src, rel, dst} edges (cites / cited_by) ready to become KG edges in
    Stage 5. Pure transform, no I/O.

Never raises; every path degrades.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

import httpx

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc  # authority_score, _shingles, _jaccard, _domain

EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "/model")
SEMANTIC_DUP_COSINE = float(os.environ.get("RESEARCH_SEMANTIC_DUP_COSINE", "0.92"))
_CURRENT_YEAR = int(os.environ.get("RESEARCH_CURRENT_YEAR", "2026"))


# ── embedding (shared endpoint with the RAG store; monkeypatchable) ───────────
def _embed(texts: list[str]) -> list[list[float]] | None:
    if not EMBED_BASE_URL or not texts:
        return None
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{EMBED_BASE_URL}/embeddings",
                       json={"model": EMBED_MODEL, "input": [t[:8000] for t in texts]})
            r.raise_for_status()
            data = r.json().get("data", [])
        vecs = [d["embedding"] for d in data]
        return vecs if len(vecs) == len(texts) else None
    except Exception:  # noqa: BLE001
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ── composite authority (domain + citations + recency) ────────────────────────
def composite_authority(item: dict[str, Any]) -> float:
    """0..~6 composite. Domain authority dominates (primary > farm); citation count
    is log-scaled (seminal anchor); recency adds a small fast-moving-field bump."""
    url = item.get("url", "")
    auth = rc.authority_score(url)  # 0..3
    cc = item.get("citation_count")
    cite = math.log1p(cc) / 3.0 if isinstance(cc, (int, float)) and cc else 0.0  # ~0..3
    recency = 0.0
    m = re.search(r"(19|20)\d{2}", str(item.get("date", "")))
    if m:
        yr = int(m.group(0))
        recency = max(0.0, 1.0 - (max(0, _CURRENT_YEAR - yr) / 10.0))  # 0..1, decays over a decade
    return round(auth + cite + 0.5 * recency, 4)


def authority_rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort items by composite authority desc, annotating each with the score.
    Surfaces primary/cited/recent sources over SEO summaries."""
    scored = [dict(it, _authority_composite=composite_authority(it)) for it in (items or [])]
    scored.sort(key=lambda it: it["_authority_composite"], reverse=True)
    otel_emit.record("authority_ranked", {"items": len(scored),
                                          "top": scored[0]["_authority_composite"] if scored else 0})
    return scored


# ── semantic dedup (embedding cosine; n-gram fallback) ────────────────────────
def _text_of(item: dict[str, Any]) -> str:
    return (item.get("markdown") or item.get("content") or item.get("title") or "")[:8000]


def semantic_dedup(items: list[dict[str, Any]],
                   threshold: float = SEMANTIC_DUP_COSINE) -> dict[str, Any]:
    """Collapse near-duplicate items, keeping the MOST AUTHORITATIVE of each cluster.
    Uses embedding cosine when the endpoint is up; otherwise n-gram Jaccard (the
    existing echo-chamber helper). Returns {kept, collapsed, method}."""
    items = [it for it in (items or []) if it]
    if len(items) <= 1:
        return {"ok": True, "kept": items, "collapsed": 0, "method": "noop"}
    # rank by authority first so the cluster representative is the best instance.
    ranked = authority_rank(items)
    texts = [_text_of(it) for it in ranked]
    vecs = _embed(texts)
    method = "embedding" if vecs else "ngram"
    shingles = [rc._shingles(t, n=4) for t in texts] if not vecs else None

    kept: list[dict[str, Any]] = []
    kept_idx: list[int] = []
    collapsed = 0
    for i, it in enumerate(ranked):
        dup = False
        for j in kept_idx:
            sim = (_cosine(vecs[i], vecs[j]) if vecs
                   else rc._jaccard(shingles[i], shingles[j]))
            if sim >= threshold:
                dup = True
                # record the merge on the kept (more-authoritative) instance
                kept[kept_idx.index(j)].setdefault("_dup_of", []).append(it.get("url", ""))
                break
        if dup:
            collapsed += 1
        else:
            kept.append(it)
            kept_idx.append(i)
    otel_emit.record("dedup_collapsed", {"in": len(items), "kept": len(kept),
                                         "collapsed": collapsed, "method": method})
    return {"ok": True, "kept": kept, "collapsed": collapsed, "method": method,
            "threshold": threshold}


# ── citation-graph edges (prep for Stage-5 KG) ────────────────────────────────
def citation_edges(paper: dict[str, Any], refs: list[dict[str, Any]] | None = None,
                   cites: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Turn a paper + its references (backward) / citations (forward) into normalized
    edges ready for the KG: paper --cites--> ref ; citing --cites--> paper. Each edge
    carries source IDs/URLs for provenance. Pure transform (no I/O)."""
    def _key(p: dict) -> str:
        ex = p.get("extra", {}) or {}
        return ex.get("paper_id") or p.get("url") or p.get("title", "")

    pkey = _key(paper)
    edges: list[dict[str, Any]] = []
    for r in (refs or []):
        edges.append({"src": pkey, "rel": "cites", "dst": _key(r),
                      "src_title": paper.get("title", ""), "dst_title": r.get("title", ""),
                      "dst_url": r.get("url", "")})
    for cdoc in (cites or []):
        edges.append({"src": _key(cdoc), "rel": "cites", "dst": pkey,
                      "src_title": cdoc.get("title", ""), "src_url": cdoc.get("url", ""),
                      "dst_title": paper.get("title", "")})
    otel_emit.record("citation_edges", {"paper": pkey, "refs": len(refs or []),
                                        "cites": len(cites or []), "edges": len(edges)})
    return {"ok": True, "paper": pkey, "edges": edges}


def rank_stats() -> dict[str, Any]:
    return {"embed_endpoint": EMBED_BASE_URL or "(unset -> n-gram dedup fallback)",
            "semantic_dup_cosine": SEMANTIC_DUP_COSINE, "current_year": _CURRENT_YEAR}
```

#### `mcp-research/relevance.py`
```python
"""RISK-B remedy (Stage-6): a relevance + authority FILTER on research findings
BEFORE they feed the synth brief.

The suspicion: noisy/irrelevant research poisons the synth brief → confident WRONG
directives (caught by verify, but at cost). Precision matters more than recall
here, so this gate drops a finding unless it clears BOTH a source-authority floor
and a query-relevance floor. Feature-flagged (RESEARCH_RELEVANCE_FILTER, default
on) and threshold-tunable; off → every finding passes through (recall-max).

Relevance is a cheap lexical overlap (shingle Jaccard) between the finding text and
the synth query — no embedding endpoint needed, so it runs anywhere. Authority
reuses research_core.authority_score (peer-review / standards / official-docs rank).
"""
from __future__ import annotations

import os
import re
from typing import Any

import research_core as rc

RELEVANCE_FILTER = os.environ.get("RESEARCH_RELEVANCE_FILTER", "true").strip().lower() in (
    "1", "true", "yes", "on")
MIN_AUTHORITY = int(os.environ.get("RESEARCH_MIN_AUTHORITY", "2"))
MIN_RELEVANCE = float(os.environ.get("RESEARCH_MIN_RELEVANCE", "0.25"))

_STOP = {"the", "a", "an", "to", "of", "in", "for", "and", "or", "is", "how", "use"}


def _relevance(query: str, text: str) -> float:
    """Query-token CONTAINMENT: fraction of the query's content words that appear in
    the finding. Forgiving on short snippets (unlike shingle-Jaccard) so a clearly
    on-topic source clears the floor while off-topic noise does not."""
    q = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if w not in _STOP}
    t = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    if not q:
        return 0.0
    return len(q & t) / len(q)


def filter_findings(findings: list[dict], query: str, *, enabled: bool | None = None,
                    min_authority: int | None = None,
                    min_relevance: float | None = None) -> dict[str, Any]:
    """Keep only findings clearing BOTH the authority and relevance floors. Each
    finding: {text|claim|snippet, url, authority?}. Returns kept + dropped (with
    reasons) so the synth brief can ingest `kept` and the eval can measure how much
    noise the filter removes. When disabled, everything is kept (annotated)."""
    enabled = RELEVANCE_FILTER if enabled is None else enabled
    ma = MIN_AUTHORITY if min_authority is None else min_authority
    mr = MIN_RELEVANCE if min_relevance is None else min_relevance
    kept: list[dict] = []
    dropped: list[dict] = []
    for f in findings:
        text = f.get("text") or f.get("claim") or f.get("snippet") or ""
        url = f.get("url", "")
        auth = f.get("authority")
        if auth is None:
            auth = rc.authority_score(url) if url else 0
        rel = _relevance(query, text)
        annotated = {**f, "authority": auth, "relevance": round(rel, 4)}
        if not enabled:
            kept.append(annotated)
            continue
        if auth >= ma and rel >= mr:
            kept.append(annotated)
        else:
            why = (f"authority {auth} < {ma}" if auth < ma else f"relevance {rel:.3f} < {mr}")
            dropped.append({**annotated, "drop_reason": why})
    return {"ok": True, "enabled": enabled, "min_authority": ma, "min_relevance": mr,
            "kept": kept, "dropped": dropped, "n_in": len(findings),
            "n_kept": len(kept), "n_dropped": len(dropped)}
```

### A.4  mcp-research — verification, corpus, KG, gates

#### `mcp-research/verify_gate.py`
```python
"""Stage 5b — decomposed verification gate (grounding, not generation).

The most important reliability layer. Verification is RETRIEVAL-decomposed, not a
generative "does this look right":

  * every synthesized claim must carry a SOURCE ID that resolves to a stored chunk
    (corpus.resolve_source) — claims with no resolvable backing are flagged, never
    asserted;
  * a cheap entailment pass checks each claim is actually ENTAILED by its cited
    chunk (local Qwen, or DeepSeek via the conductor for dense sources);
  * CONTRADICTIONS across sources are surfaced EXPLICITLY with both citations —
    never averaged away (critical when research drives an architecture decision).

Plus query-diversity decomposition (the echo-chamber fix): break a question into
complementary sub-questions, generate diverse paraphrase angles + per-source query
syntax, optional HyDE — the actual searches fuse via sources.rrf_fuse.

Never raises; degrades to deterministic behavior with no model.
"""
from __future__ import annotations

from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc
import corpus
import sources

DENSE_SOURCE_TYPES = corpus.DENSE_SOURCE_TYPES


# ── resolve a source ID -> the stored chunk text it backs ─────────────────────
def _resolve_chunk(src: dict[str, Any]) -> tuple[str, str, bool]:
    """Return (text, source_id, resolvable). Prefers a corpus relpath resolved to
    full on-disk content; falls back to an inline snippet; flags unresolvable."""
    sid = src.get("source_id") or src.get("source") or src.get("url") or ""
    if src.get("source_id") or (isinstance(sid, str) and sid.endswith(".md")):
        res = corpus.resolve_source(sid)
        if res.get("ok"):
            return res["content"], sid, True
    snippet = src.get("snippet") or src.get("markdown") or ""
    return snippet, sid, bool(snippet)


# ── entailment pass (decomposed; local default, dense -> optional cloud) ──────
_ENTAIL_SYS = (
    "You are a strict entailment checker. Does the SOURCE CHUNK entail the CLAIM? "
    "Answer STRICT JSON {\"label\": \"supports\"|\"contradicts\"|\"neutral\"}. "
    "'supports' ONLY if the chunk clearly backs the claim; 'contradicts' if it "
    "states the opposite; else 'neutral'. Judge only from the chunk, not prior "
    "knowledge."
)


def _entail(claim: str, chunk: str, source_type: str = "web") -> str:
    if not chunk.strip():
        return "unchecked"
    prompt = f"CLAIM: {claim}\n\nSOURCE CHUNK:\n{chunk[:4000]}"
    out = None
    if corpus.CLOUD_DISTILL and source_type in DENSE_SOURCE_TYPES:
        out = corpus._conductor_distill(f"{_ENTAIL_SYS}\n\n{prompt}", max_tokens=300)
    if out is None:
        out = rc._llm([{"role": "system", "content": _ENTAIL_SYS},
                       {"role": "user", "content": prompt}], max_tokens=2000, temperature=0)
    parsed = rc._json_from_llm(out)
    if isinstance(parsed, dict):
        lab = str(parsed.get("label", "")).lower().strip()
        if lab in ("supports", "contradicts", "neutral"):
            return lab
    return "unchecked"


def verify_claim(claim: str, sources: list[dict[str, Any]],
                 min_sources: int = 2) -> dict[str, Any]:
    """Verify ONE claim by decomposed retrieval: resolve each source to its stored
    chunk, entail the claim against it, count INDEPENDENT (distinct-domain) support.
    Returns status + per-source verdicts + resolvable source IDs. Contradictions are
    preserved (status='conflicting'), never averaged."""
    claim = (claim or "").strip()
    if not claim:
        return {"ok": False, "error": "empty claim"}
    by_domain: dict[str, dict] = {}
    unresolved = 0
    for src in (sources or []):
        chunk, sid, resolvable = _resolve_chunk(src)
        if not resolvable:
            unresolved += 1
        dom = rc._domain(src.get("url", "") or sid)
        if not dom or dom in by_domain:
            continue  # one vote per domain -> independence
        label = _entail(claim, chunk, src.get("source_type", "web")) if (rc.VLLM_BASE_URL or corpus.CLOUD_DISTILL) else "unchecked"
        by_domain[dom] = {"source_id": sid, "url": src.get("url", ""), "label": label,
                          "resolvable": resolvable}
    supports = [d for d in by_domain.values() if d["label"] == "supports"]
    contradicts = [d for d in by_domain.values() if d["label"] == "contradicts"]
    # 'unchecked' (no model) counts as candidate support for the deterministic path,
    # but the wording stays honest.
    candidate = supports + [d for d in by_domain.values() if d["label"] == "unchecked"]
    if contradicts and (supports or candidate):
        status = "conflicting"
    elif len(supports) >= min_sources:
        status = "well-supported"
    elif len(candidate) >= min_sources:
        status = "candidate-unverified"  # ≥2 domains but no model entailment
    elif by_domain:
        status = "single-sourced"
    else:
        status = "unsupported"
    otel_emit.record("claim_verified" if status in ("well-supported", "candidate-unverified")
                     else "claim_unsupported",
                     {"status": status, "independent": len(by_domain), "unresolved": unresolved})
    return {"ok": True, "claim": claim, "status": status,
            "independent_sources": len(by_domain),
            "supports": [d["source_id"] for d in supports],
            "contradicts": [d["source_id"] for d in contradicts],
            "unresolved_sources": unresolved,
            "source_ids": [d["source_id"] for d in by_domain.values()],
            "verdicts": list(by_domain.values())}


def verify_findings(findings: list[dict[str, Any]], min_sources: int = 2) -> dict[str, Any]:
    """Verify a batch of claims and surface contradictions explicitly. Each finding:
    {"claim": str, "sources": [{source_id|url|snippet, source_type?}]}."""
    verified = [verify_claim(f.get("claim", ""), f.get("sources", []), min_sources)
                for f in (findings or [])]
    verified = [v for v in verified if v.get("ok")]
    contradictions = surface_contradictions(verified)
    summary = {s: sum(1 for v in verified if v["status"] == s)
               for s in ("well-supported", "candidate-unverified", "single-sourced",
                         "conflicting", "unsupported")}
    return {"ok": True, "verified": verified, "contradictions": contradictions,
            "summary": summary}


def surface_contradictions(verified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull out claims where sources disagree, presenting BOTH sides' citations.
    Never averaged — the operator/agent sees the conflict and both sources."""
    out = []
    for v in verified:
        if v.get("status") == "conflicting":
            out.append({"claim": v["claim"],
                        "supported_by": v["supports"],
                        "contradicted_by": v["contradicts"],
                        "note": "sources disagree — both citations surfaced, not averaged"})
    if out:
        otel_emit.record("contradiction_surfaced", {"count": len(out)})
    return out


# ── query-diversity decomposition (echo-chamber fix) ──────────────────────────
_DECOMP_SYS = (
    "Decompose the research question into 2-4 COMPLEMENTARY sub-questions (not "
    "overlapping). For EACH sub-question give 3 diverse search paraphrases that vary "
    "abstraction and phrasing to retrieve DIFFERENT sources. Return STRICT JSON: "
    '[{"sub_question": "...", "paraphrases": ["...", "...", "..."]}]. No prose.'
)


def _per_source_syntax(query: str) -> dict[str, str]:
    """Translate one query into per-source syntax (arXiv field prefixes != GitHub
    qualifiers != web). Lightweight, deterministic — the diverse-retrieval step."""
    q = query.strip()
    return {"web": q, "arxiv": f"all:{q}", "github": q, "semantic_scholar": q,
            "hn": q}


def decompose_question(question: str, hyde: bool = False) -> dict[str, Any]:
    """Sub-question decomposition + diverse paraphrase angles + per-source syntax,
    so retrieval doesn't echo one phrasing. Uses the local model (or conductor for
    a stronger decomposition); degrades to deterministic variants with no model.
    Optional HyDE: a hypothetical answer doc to embed for dense retrieval."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}
    parsed = rc._json_from_llm(rc._llm(
        [{"role": "system", "content": _DECOMP_SYS},
         {"role": "user", "content": question}], temperature=0.4))
    subs: list[dict[str, Any]] = []
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and item.get("sub_question"):
                paras = [str(p).strip() for p in (item.get("paraphrases") or []) if str(p).strip()]
                subs.append({"sub_question": str(item["sub_question"]).strip(),
                             "paraphrases": sources_dedup(paras or [item["sub_question"]]),
                             "per_source": {p: _per_source_syntax(p) for p in (paras[:3] or [question])}})
    if not subs:  # deterministic fallback
        variants = [question, f"{question} overview", f"{question} latest research"]
        subs = [{"sub_question": question, "paraphrases": variants,
                 "per_source": {v: _per_source_syntax(v) for v in variants}}]
    hyde_doc = None
    if hyde:
        hyde_doc = rc._llm([{"role": "system", "content":
                             "Write a short hypothetical expert answer to embed for retrieval (HyDE)."},
                            {"role": "user", "content": question}], max_tokens=400, temperature=0.3)
    otel_emit.record("query_decomposed", {"sub_questions": len(subs), "hyde": bool(hyde_doc)})
    return {"ok": True, "question": question, "sub_questions": subs, "hyde_doc": hyde_doc}


def sources_dedup(qs: list[str]) -> list[str]:
    """Thin reuse of the existing n-gram query dedup so paraphrases stay diverse."""
    return rc._dedup_queries(qs)


def verify_gate_stats() -> dict[str, Any]:
    return {"entailment": "local" + ("+cloud(dense)" if corpus.CLOUD_DISTILL else ""),
            "corpus_resolvable": True}
```

#### `mcp-research/corpus.py`
```python
"""Stage 3 — on-disk human-readable corpus + provenance + lazy distillation.

Two problems this fixes, per the research-engine spec:

  1. The corpus was distilled-on-ingest (compressing away the technical nuance that
     frontier work needs) and only lived inside the vector store. Now: the FULL,
     untruncated extracted content is written to a greppable, git-versionable,
     human-readable markdown tree on disk — `corpus/{namespace}/{source_type}/
     {slug}.md` with YAML front-matter provenance — INDEPENDENT of the vector DB
     and fully sovereign. The full text is ALSO indexed into the existing hybrid
     RAG store (mcp-codebase-rag.index_document already keeps full chunks; the 10K
     truncation lived in the old distill step, which we stop calling on ingest).

  2. Distillation moves from ingest-time to QUERY-time, run only over the chunks
     a query actually retrieves — local Qwen by default; optional cheap-cloud
     (DeepSeek via the conductor's steer role) for DENSE technical sources, behind
     the RESEARCH_CLOUD_DISTILL flag. Off => fully local / offline.

Resolvability (the verify gate, Stage 5, depends on this): every RAG chunk is
indexed with `source` = the corpus file's relative path, so a retrieved chunk
resolves straight back to its backing on-disk document + front-matter provenance.

Discipline (unchanged): never raises (string errors), every backend degrades, no
new required keys. The vector store (machinery) is USED, not modified.

NOTE (honest): the spec says "Qdrant"; the actual RAG is mcp-codebase-rag's
SQLite + FTS5 + sqlite-vec hybrid store. Same contract (full chunks + embeddings),
different engine — documented rather than papered over.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc  # reuse _mcp_call / _llm / _slug — no duplication

# ── config ────────────────────────────────────────────────────────────────────
CORPUS_DIR = os.path.expanduser(
    os.environ.get("RESEARCH_CORPUS_DIR", "~/.hermes-max/corpus"))
ESCALATION_MCP_URL = os.environ.get("ESCALATION_MCP_URL", "http://127.0.0.1:9107/mcp")
RAG_MCP_URL = rc.RAG_MCP_URL
# Flag: off (default) => distillation is fully local/sovereign. On => DENSE
# technical sources may be distilled by the conductor's cheap-cloud steer role.
#
# WHY LOCAL IS THE DEFAULT (Stage 7b): per-source distillation is the highest-VOLUME
# step in the research cascade. Gating it on a rate-limited cloud tier (e.g. Groq's
# 6-8K TPM) would force serialization + 429 backoffs — a real ARTIFICIAL bottleneck
# on exactly the bulkiest step. The local model is already running, has no rate
# limit, and handles bulk summarization fine. Cloud distillation is therefore an
# explicit opt-in ONLY, and it is rate-limit-bound (warned below). Keep the fast
# cloud tiers for slop-drafting small verifiable tasks, not the bulk cascade.
CLOUD_DISTILL = os.environ.get("RESEARCH_CLOUD_DISTILL", "false").strip().lower() in ("1", "true", "yes")
# Source types whose content is dense enough to warrant cloud distillation.
DENSE_SOURCE_TYPES = {"arxiv", "semantic_scholar", "eip_erc", "ietf_rfc", "audit"}

if CLOUD_DISTILL:
    # One-time warning at import: the operator opted into the rate-limit-bound path.
    otel_emit.record("research_cloud_distill_enabled", {
        "warning": "RESEARCH_CLOUD_DISTILL=on — dense-source distillation routes to a "
                   "RATE-LIMITED cloud tier; high volume may serialize on 429 backoffs "
                   "(an artificial bottleneck). Local distillation is the default for a "
                   "reason.", "dense_source_types": sorted(DENSE_SOURCE_TYPES)},
        status="error")


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ── front-matter + path ───────────────────────────────────────────────────────
def _yaml_value(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(str(x)) for x in v) + "]"
    if v is None:
        return '""'
    if isinstance(v, (int, float, bool)):
        return str(v).lower() if isinstance(v, bool) else str(v)
    s = str(v)
    # quote anything with YAML-significant chars or leading/trailing space
    if s == "" or re.search(r"[:#\[\]{}\n\"']", s) or s != s.strip():
        return json.dumps(s)
    return s


def _front_matter(meta: dict[str, Any]) -> str:
    order = ["source_url", "title", "authors", "date", "retrieval_query",
             "source_type", "citation_count", "authority_score", "ingested_at",
             "session_id"]
    keys = order + [k for k in meta if k not in order]
    lines = ["---"]
    for k in keys:
        if k in meta:
            lines.append(f"{k}: {_yaml_value(meta[k])}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def corpus_path(namespace: str, source_type: str, slug: str) -> str:
    ns = re.sub(r"[^a-z0-9._/-]+", "-", (namespace or "research").lower()).strip("-/") or "research"
    st = re.sub(r"[^a-z0-9._-]+", "-", (source_type or "web").lower()).strip("-") or "web"
    sl = rc._slug(slug or "doc")
    return os.path.join(CORPUS_DIR, ns, st, f"{sl}.md")


def corpus_relpath(path: str) -> str:
    try:
        return os.path.relpath(path, CORPUS_DIR)
    except Exception:  # noqa: BLE001
        return path


# ── injectable backends (smoke tests stub these) ──────────────────────────────
def _rag_index(text: str, namespace: str, source: str, title: str) -> dict[str, Any]:
    return rc._mcp_call(RAG_MCP_URL, "index_document",
                        {"text": text, "namespace": namespace, "source": source, "title": title})


def _conductor_distill(prompt: str, max_tokens: int = 1500) -> str | None:
    """Cheap-cloud distill via the conductor's steer role (DeepSeek-first). Returns
    None if steer is OFF/capped/unreachable (proceed_local) -> caller falls to local."""
    r = rc._mcp_call(ESCALATION_MCP_URL, "conductor_steer",
                     {"prompt": prompt, "max_tokens": max_tokens})
    if not r.get("ok"):
        return None
    res = r.get("result") or {}
    if isinstance(res, dict) and res.get("proceed_local"):
        return None
    content = res.get("content") if isinstance(res, dict) else None
    return content.strip() if content else None


# ── write the on-disk corpus document (full, untruncated, with provenance) ─────
def write_corpus_doc(namespace: str, source_type: str, content: str,
                     meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write FULL untruncated content + YAML front-matter to
    corpus/{namespace}/{source_type}/{slug}.md. Idempotent (re-ingest overwrites
    the same slug). The sovereign, greppable, git-versionable record — independent
    of the vector store. Best-effort: a write failure returns a string error."""
    content = content or ""
    meta = dict(meta or {})
    meta.setdefault("source_type", source_type)
    meta.setdefault("ingested_at", _now_iso())
    slug_src = meta.get("title") or meta.get("source_url") or "doc"
    path = corpus_path(namespace, source_type, slug_src)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(_front_matter(meta))
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "path": path}
    otel_emit.record("corpus_written", {"namespace": namespace, "source_type": source_type,
                                        "path": corpus_relpath(path), "chars": len(content)})
    return {"ok": True, "path": path, "relpath": corpus_relpath(path),
            "chars": len(content), "front_matter": meta}


# ── ingest: write disk (full) + index full chunks to RAG (resolvable) ──────────
def ingest_research(namespace: str, source_type: str, content: str,
                    meta: dict[str, Any] | None = None, index: bool = True) -> dict[str, Any]:
    """Write the full document to the on-disk corpus AND (best-effort) index the
    FULL content into the hybrid RAG store. Each RAG chunk's `source` is set to the
    corpus relative path so a retrieved chunk resolves back to its backing document
    + provenance (used by the Stage-5 verify gate). NO distill-on-ingest — the full
    technical text is preserved; distillation happens lazily at query time."""
    meta = dict(meta or {})
    written = write_corpus_doc(namespace, source_type, content, meta)
    indexed = {"rag_stored": False}
    if index and content.strip():
        title = (meta.get("title") or source_type)[:120]
        # `source` carries the corpus relpath -> resolvable; fall back to URL.
        source = written.get("relpath") or meta.get("source_url") or title
        r = _rag_index(content, namespace, source, title)
        if r.get("ok"):
            res = r.get("result") or {}
            indexed = {"rag_stored": bool(res.get("ok", True)),
                       "chunks_indexed": res.get("chunks_indexed"),
                       "dense_embedded": res.get("dense_embedded"),
                       "rag_source": source}
        else:
            indexed = {"rag_stored": False, "error": r.get("error")}
    otel_emit.record("research_ingested", {"namespace": namespace, "source_type": source_type,
                                          "corpus_ok": written.get("ok"),
                                          "rag_stored": indexed.get("rag_stored")})
    return {"ok": True, "corpus": written, "rag": indexed,
            "resolvable_via": written.get("relpath")}


# ── lazy, query-time distillation (local default; optional dense cloud) ────────
_DISTILL_SYS = (
    "You are a technical distiller. Given a query and retrieved source chunks, "
    "produce a focused, FAITHFUL distillation that answers the query. PRESERVE exact "
    "technical detail VERBATIM — code, equations, parameter names, numbers, version "
    "constraints. Do NOT compress away nuance or generalize. If the chunks do not "
    "answer the query, say so. No invented facts."
)


def distill_for_query(query: str, chunks: list[str], source_type: str = "web",
                      max_tokens: int = 1500) -> dict[str, Any]:
    """Distill ONLY the retrieved chunks, at query time (not ingest time). Routes by
    density: dense technical source_types -> cheap-cloud (conductor steer / DeepSeek)
    when RESEARCH_CLOUD_DISTILL is on; everything else -> local Qwen. Degrades to a
    raw chunk concatenation if no model is available — still honest, never raises."""
    chunks = [c for c in (chunks or []) if c and c.strip()]
    if not chunks:
        return {"ok": True, "distilled": "", "method": "empty", "query": query}
    blob = "\n\n---\n\n".join(c[:6000] for c in chunks)[:24000]
    prompt = f"Query: {query}\n\nRetrieved chunks:\n{blob}"
    method = None
    out: str | None = None

    # Label heartbeats for the local rc._llm path (it is wrapped at the source);
    # the cloud path below is wrapped explicitly since it bypasses rc._llm.
    rc._HB_PHASE = "distill"
    dense = source_type in DENSE_SOURCE_TYPES
    if CLOUD_DISTILL and dense:
        rc.heartbeat.beat("deep_research", progress="distill: cloud inference start")
        try:
            out = _conductor_distill(f"{_DISTILL_SYS}\n\n{prompt}", max_tokens=max_tokens)
        finally:
            rc.heartbeat.beat("deep_research", progress="distill: cloud inference done")
        if out:
            method = "cloud"
    if out is None:  # local default (and fallback when cloud is off/unavailable)
        out = rc._llm([{"role": "system", "content": _DISTILL_SYS},
                       {"role": "user", "content": prompt}],
                      max_tokens=max_tokens, temperature=0.1)
        if out:
            method = "local"
    if out is None:  # fully sovereign fallback — no model anywhere
        out = blob
        method = "raw"
    otel_emit.record(f"distill_{method}", {"query": query, "chunks": len(chunks),
                                           "source_type": source_type, "dense": dense})
    return {"ok": True, "distilled": out, "method": method, "query": query,
            "chunks_used": len(chunks)}


# ── resolve a chunk's source back to its on-disk document + provenance ─────────
def resolve_source(relpath_or_path: str) -> dict[str, Any]:
    """Given a RAG chunk's `source` (a corpus relpath) or an absolute path, read the
    backing on-disk document: full content + parsed front-matter provenance. This is
    how a synthesized claim resolves to the exact stored chunk it came from."""
    p = relpath_or_path or ""
    path = p if os.path.isabs(p) else os.path.join(CORPUS_DIR, p)
    if not os.path.exists(path):
        return {"ok": False, "error": "not found", "path": path}
    try:
        with open(path) as f:
            raw = f.read()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "path": path}
    fm: dict[str, Any] = {}
    body = raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                v = v.strip()
                if v and v[0] in '"[{':  # our writer JSON-encodes quoted/list values
                    try:
                        v = json.loads(v)
                    except Exception:  # noqa: BLE001
                        pass
                fm[k.strip()] = v
        body = m.group(2)
    return {"ok": True, "path": path, "relpath": corpus_relpath(path),
            "front_matter": fm, "content": body, "chars": len(body)}


def corpus_stats() -> dict[str, Any]:
    n_docs = 0
    namespaces: set[str] = set()
    if os.path.isdir(CORPUS_DIR):
        for root, _dirs, files in os.walk(CORPUS_DIR):
            for fn in files:
                if fn.endswith(".md"):
                    n_docs += 1
                    rel = os.path.relpath(root, CORPUS_DIR)
                    namespaces.add(rel.split(os.sep)[0] if rel != "." else "")
    return {"corpus_dir": CORPUS_DIR, "docs": n_docs, "namespaces": sorted(namespaces),
            "cloud_distill": CLOUD_DISTILL, "dense_source_types": sorted(DENSE_SOURCE_TYPES)}
```

#### `mcp-research/kg_provenance.py`
```python
"""Stage 5a — KG ingestion with provenance + temporal validity.

Research outputs become graph episodes/entities/edges, each FACT EDGE carrying its
source ID so any claim is traceable to the chunk it came from. Edge vocabulary maps
the research domain: cites / supersedes / implements / audits / contradicts /
authored_by (the citation graph from Stage 4 lands directly as `cites` edges).

Temporal validity matters for fast-moving fields: a 2024 claim may be superseded by
a 2026 one. Rather than silently keeping both, mark_superseded records the
`supersedes` edge AND stamps the old fact's `valid_until` — so the graph says which
is current.

NOTE (honest): the spec says Graphiti/Neo4j; the actual KG (mcp-knowledge-graph) is
a single-file SQLite store whose own header reads "Deliberately NOT built: Neo4j +
Graphiti + Cognee". Same contract — entities, directed relations, and a `props` bag
that carries source IDs + valid_from/valid_until — modeled on that store, USED not
modified. Never raises; degrades to a reported no-op if the KG is down.
"""
from __future__ import annotations

import datetime
from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc

KG_MCP_URL = rc.KG_MCP_URL
ALLOWED_RELS = {"cites", "supersedes", "implements", "audits", "contradicts", "authored_by"}


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ── injectable KG call (smoke tests stub this) ────────────────────────────────
def _kg_call(tool: str, args: dict) -> dict[str, Any]:
    return rc._mcp_call(KG_MCP_URL, tool, args)


def add_entity(entity_type: str, name: str, props: dict | None = None,
               source_id: str | None = None) -> dict[str, Any]:
    """Upsert a research entity (paper/repo/protocol/eip/person/technique). The
    backing source_id is stored in props for provenance."""
    props = dict(props or {})
    if source_id:
        props.setdefault("source_id", source_id)
    return _kg_call("record_entity", {"type": entity_type, "name": name, "props": props})


def add_fact_edge(a: str, rel: str, b: str, source_id: str,
                  valid_from: str | None = None, valid_until: str | None = None,
                  props: dict | None = None) -> dict[str, Any]:
    """Record a fact edge (a)-[rel]->(b) carrying its SOURCE ID + temporal validity.
    rel must be in ALLOWED_RELS (a wrong/invented relation is rejected, not stored)."""
    if rel not in ALLOWED_RELS:
        return {"ok": False, "error": f"relation '{rel}' not in {sorted(ALLOWED_RELS)}"}
    p = dict(props or {})
    p.update(source_id=source_id, valid_from=valid_from or _now_iso(), valid_until=valid_until)
    r = _kg_call("record_relation", {"a": a, "rel": rel, "b": b, "props": p})
    if r.get("ok"):
        otel_emit.record("kg_episode_added", {"rel": rel, "source_id": source_id,
                                              "a": a, "b": b})
    return r


def ingest_citation_edges(edges: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    """Bulk-record Stage-4 citation_edges() output as `cites` fact edges, each
    carrying source_id. Entities are auto-created by the KG; we also tag titles."""
    written = 0
    errors: list[str] = []
    for e in (edges or []):
        a, b = e.get("src"), e.get("dst")
        if not a or not b:
            continue
        # tag endpoint titles/urls when present (provenance)
        if e.get("src_title"):
            add_entity("paper", a, {"title": e["src_title"], "url": e.get("src_url", "")})
        if e.get("dst_title"):
            add_entity("paper", b, {"title": e["dst_title"], "url": e.get("dst_url", "")})
        r = add_fact_edge(a, e.get("rel", "cites"), b, source_id=source_id)
        if r.get("ok"):
            written += 1
        elif r.get("error"):
            errors.append(r["error"])
    return {"ok": True, "edges_written": written, "errors": errors}


def mark_superseded(old: str, new: str, source_id: str,
                    as_of: str | None = None) -> dict[str, Any]:
    """Mark `old` superseded by `new` (fast-moving fields): record new-[supersedes]->
    old, AND stamp the old entity's valid_until=as_of so the graph says which is
    current — rather than silently keeping both."""
    as_of = as_of or _now_iso()
    edge = add_fact_edge(new, "supersedes", old, source_id=source_id, valid_from=as_of)
    add_entity("entity", old, {"valid_until": as_of, "superseded_by": new})
    otel_emit.record("kg_superseded", {"old": old, "new": new, "as_of": as_of})
    return {"ok": edge.get("ok", False), "old": old, "new": new, "as_of": as_of}


def add_episode(namespace: str, summary: str, source_id: str,
                entities: list[dict] | None = None,
                edges: list[dict] | None = None) -> dict[str, Any]:
    """Record a research EPISODE (modeled as an entity of type 'episode') + its
    entities + fact edges, all carrying source_id + ingested_at. The single call
    that lands a finished research finding into the graph with full provenance."""
    ts = _now_iso()
    ep_name = f"episode:{namespace}:{source_id}"
    ep = add_entity("episode", ep_name,
                    {"namespace": namespace, "summary": summary[:1000], "ingested_at": ts},
                    source_id=source_id)
    ent_written = 0
    for e in (entities or []):
        if e.get("name"):
            add_entity(e.get("type", "entity"), e["name"], e.get("props", {}), source_id)
            ent_written += 1
    edge_written = 0
    for ed in (edges or []):
        if ed.get("a") and ed.get("b") and ed.get("rel"):
            if add_fact_edge(ed["a"], ed["rel"], ed["b"], source_id=source_id,
                             valid_from=ed.get("valid_from"),
                             valid_until=ed.get("valid_until")).get("ok"):
                edge_written += 1
    otel_emit.record("kg_episode_added", {"namespace": namespace, "source_id": source_id,
                                          "entities": ent_written, "edges": edge_written})
    return {"ok": ep.get("ok", False), "episode": ep_name, "entities": ent_written,
            "edges": edge_written, "ingested_at": ts}


def kg_provenance_stats() -> dict[str, Any]:
    return {"kg_mcp": KG_MCP_URL, "allowed_relations": sorted(ALLOWED_RELS)}
```

#### `mcp-research/session_state.py`
```python
"""Per-session research rationing state — the mechanical budget the skill can't
enforce on its own (SWE-agent-style per-task budget + cooldown). File-based, keyed
by the session/task id, no LLM. Tracks:

  • deep_research cumulative wall-time + last-call timestamp + call count
    (R-Stage 2: budget RESEARCH_BUDGET_S + cooldown RESEARCH_COOLDOWN_S);
  • lighter-tool attempts with their query text (R-Stage 3: the exhaustion-first
    precondition + semantic-relatedness check);
  • whether the corpus was checked for a question (R-Stage 1/3 precondition).

Gates on EXTERNAL signals (elapsed budget, time since last call, cheaper-tool
attempts), never the model's self-confidence — per the adaptive-retrieval research.
Never raises: a broken state file degrades to "allow" so rationing never wedges a
task, but every decision is logged so the operator sees what happened.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.path.expanduser(os.environ.get("RESEARCH_STATE_DIR", "~/.hermes-max/research")))
SESSION_DIR = STATE_DIR / "sessions"

RESEARCH_BUDGET_S = float(os.environ.get("RESEARCH_BUDGET_S", "900"))
RESEARCH_COOLDOWN_S = float(os.environ.get("RESEARCH_COOLDOWN_S", "1800"))
# Relatedness threshold for "a lighter tool was attempted on a RELATED query".
# Embeddings (cosine > 0.6) are the ideal signal per the research, but this
# deployment has no embed endpoint (EMBED_BASE_URL blank), so we fall back to
# lexical token-set Jaccard with its own (lower) threshold. Both env-overridable.
RESEARCH_LIGHTER_SIM = float(os.environ.get("RESEARCH_LIGHTER_SIM", "0.6"))            # embedding cosine
RESEARCH_LIGHTER_LEXICAL_SIM = float(os.environ.get("RESEARCH_LIGHTER_LEXICAL_SIM", "0.2"))  # Jaccard fallback
RESEARCH_LIGHTER_MAX_AGE_S = float(os.environ.get("RESEARCH_LIGHTER_MAX_AGE_S", "3600"))


def session_id() -> str:
    return (os.environ.get("WATCHDOG_TASK_ID")
            or os.environ.get("HERMES_TASK_ID")
            or "default")


def _path(sid: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (sid or "default"))
    return SESSION_DIR / f"{safe}.json"


def load(sid: str | None = None) -> dict[str, Any]:
    sid = sid or session_id()
    try:
        with open(_path(sid)) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        return {}


def save(sid: str, st: dict[str, Any]) -> None:
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        p = _path(sid)
        tmp = str(p) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 - best-effort
        pass


def _dr(st: dict[str, Any]) -> dict[str, Any]:
    dr = st.get("deep_research")
    if not isinstance(dr, dict):
        dr = {"last_ts": 0.0, "cumulative_s": 0.0, "calls": 0}
    return dr


def record_research(elapsed_s: float, sid: str | None = None) -> None:
    """Record a completed deep_research call: bump last_ts, cumulative time, count."""
    sid = sid or session_id()
    st = load(sid)
    dr = _dr(st)
    dr["last_ts"] = time.time()
    dr["cumulative_s"] = float(dr.get("cumulative_s", 0.0)) + max(0.0, float(elapsed_s))
    dr["calls"] = int(dr.get("calls", 0)) + 1
    st["deep_research"] = dr
    save(sid, st)


def mark_corpus_checked(sid: str | None = None) -> None:
    sid = sid or session_id()
    st = load(sid)
    st["corpus_checked_ts"] = time.time()
    save(sid, st)


import re as _re


def _tokens(text: str) -> set[str]:
    return {t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _lexical_sim(a: str, b: str) -> float:
    """Token-set Jaccard — the embedding-free relatedness fallback."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def record_lighter_tool(tool: str, query: str, sid: str | None = None) -> None:
    """Record an AGENT-initiated lighter-tool call (search_code / fetch_clean /
    research_topic) with its query. Called from each of those tools' servers so the
    exhaustion-first gate can see that cheaper tools were tried first (R-Stage 3).
    Keeps the most recent 40 entries."""
    sid = sid or session_id()
    st = load(sid)
    lt = st.get("lighter_tools")
    if not isinstance(lt, list):
        lt = []
    lt.append({"tool": tool, "query": (query or "")[:300], "ts": time.time()})
    st["lighter_tools"] = lt[-40:]
    save(sid, st)


def lighter_tools_attempted(question: str, sid: str | None = None) -> dict[str, Any]:
    """Has a lighter tool been attempted on a RELATED query this session? The
    exhaustion-first precondition for escalating to deep_research (R-Stage 3).
    Relatedness: embedding cosine > RESEARCH_LIGHTER_SIM if an embedder is wired in
    (none here), else lexical Jaccard > RESEARCH_LIGHTER_LEXICAL_SIM. Returns
    {attempted, best_sim, best_tool, best_query, method, considered}."""
    sid = sid or session_id()
    lt = load(sid).get("lighter_tools") or []
    now = time.time()
    best = {"sim": 0.0, "tool": None, "query": None}
    considered = 0
    for e in lt:
        if not isinstance(e, dict):
            continue
        if now - float(e.get("ts", 0)) > RESEARCH_LIGHTER_MAX_AGE_S:
            continue
        considered += 1
        s = _lexical_sim(question, e.get("query", ""))
        if s > best["sim"]:
            best = {"sim": s, "tool": e.get("tool"), "query": e.get("query")}
    attempted = best["sim"] >= RESEARCH_LIGHTER_LEXICAL_SIM
    return {"attempted": attempted, "best_sim": round(best["sim"], 3),
            "best_tool": best["tool"], "best_query": best["query"],
            "method": "lexical-jaccard", "considered": considered,
            "threshold": RESEARCH_LIGHTER_LEXICAL_SIM}


def note_lighter_tools_attempted(query: str, sid: str | None = None) -> None:
    """Explicit agent assertion that it tried lighter tools and found them
    insufficient for `query` — recorded as a synthetic lighter-tool attempt so the
    exhaustion gate is satisfied (the directive's explicit-precondition path)."""
    record_lighter_tool("explicit", query, sid)


def research_gate(est_s: float = 0.0, sid: str | None = None) -> dict[str, Any]:
    """Budget + cooldown gate for deep_research (R-Stage 2). Returns
    {allowed, reason, cooldown_remaining_s, cumulative_s, budget_s, calls}.
    allowed=False when a call fired < RESEARCH_COOLDOWN_S ago, or the cumulative
    research time this session would exceed RESEARCH_BUDGET_S."""
    sid = sid or session_id()
    dr = _dr(load(sid))
    now = time.time()
    last = float(dr.get("last_ts", 0.0))
    cum = float(dr.get("cumulative_s", 0.0))
    calls = int(dr.get("calls", 0))
    since = now - last if last else None
    cooldown_remaining = max(0.0, RESEARCH_COOLDOWN_S - since) if since is not None else 0.0

    if since is not None and since < RESEARCH_COOLDOWN_S:
        return {"allowed": False, "reason": "cooldown",
                "cooldown_remaining_s": round(cooldown_remaining, 1),
                "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
    if cum + max(0.0, est_s) > RESEARCH_BUDGET_S:
        return {"allowed": False, "reason": "budget_exhausted",
                "cooldown_remaining_s": 0.0,
                "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
    return {"allowed": True, "reason": "ok", "cooldown_remaining_s": 0.0,
            "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
```

### A.5  mcp-search — verifier-guided solution search

#### `mcp-search/server.py`
```python
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
```

#### `mcp-search/search_core.py`
```python
"""Verifier-guided test-time search — bounded best-of-N selection (Stage 1.2).

SWE-PRM-class selection (+10.7 pts) made losslessly-by-construction: candidates
are chosen by EXECUTION (run each through mcp-verify), never by a model judging
itself. The selected patch is one that actually goes green.

your inference host discipline (a single bandwidth-bound GPU stream — best-of-N competes with
itself for the one model):
  * default N is small (SEARCH_DEFAULT_N=3) and hard-capped (SEARCH_MAX_N=6);
  * the model-generation path requires $VLLM_BASE_URL and is meant for HARD
    subtasks only (the difficulty signal gates it via the skill);
  * the deterministic SELECTOR (candidates supplied) is cheap and always
    available — it only runs the verifier, no extra model calls.

Selection rule: keep only candidates that verify GREEN; among those prefer the
one passing the MOST tests, tie-broken by the SMALLEST diff (least code). If
none is green, say so honestly (caller escalates) — never return a red patch.

If $VLLM_BASE_URL is unreachable the generation path degrades to a clear error
and the agent falls back to writing the patch itself — never a crash.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

import otel_emit

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
SEARCH_MODEL = os.environ.get("SEARCH_MODEL", os.environ.get("EMBED_MODEL", "/model"))
DEFAULT_N = int(os.environ.get("SEARCH_DEFAULT_N", "3"))
MAX_N = int(os.environ.get("SEARCH_MAX_N", "6"))
GEN_TIMEOUT = float(os.environ.get("SEARCH_GEN_TIMEOUT", "120"))

VERIFY_PORT = int(os.environ.get("MCP_VERIFY_PORT", "9101"))
VERIFY_HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
VERIFY_CALL_TIMEOUT = float(os.environ.get("SEARCH_VERIFY_TIMEOUT", "600"))

# Stage 4: the conductor's parallel_draft POOL lives on the escalation server.
ESCALATION_PORT = int(os.environ.get("MCP_ESCALATION_PORT", "9105"))
POOL_CALL_TIMEOUT = float(os.environ.get("SEARCH_POOL_TIMEOUT", "120"))

# M-Stage 6: optional reranker scoring for quality_threshold early-exit when no
# test oracle is available (auto-detect the local rerank serve on :8003).
RERANK_BASE_URL = os.environ.get("RERANK_BASE_URL", "http://127.0.0.1:8003").rstrip("/")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "/model")


def _rerank_score(query: str, doc: str) -> float | None:
    """Cross-encoder relevance of `doc` to `query` (~[0,1]); None if unreachable.
    Used only for the quality_threshold path (execution-based selection is default)."""
    base = RERANK_BASE_URL
    if not base or not doc:
        return None
    for path in ("/rerank", "/v1/rerank"):
        try:
            with httpx.Client(timeout=8) as c:
                r = c.post(f"{base}{path}", json={"model": RERANK_MODEL, "query": query[:2000],
                                                  "documents": [doc[:2000]]})
                r.raise_for_status()
                payload = r.json()
            results = payload.get("results", payload) if isinstance(payload, dict) else payload
            if isinstance(results, list) and results:
                return float(results[0].get("relevance_score", results[0].get("score", 0.0)))
        except Exception:  # noqa: BLE001
            continue
    return None


# ── verify boundary (graceful-degrade if mcp-verify is unreachable) ──────────
async def _call_verify(path: str, language: str) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://{VERIFY_HOST}:{VERIFY_PORT}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("verify", {"path": path, "language": language})
            text = getattr(res.content[0], "text", "") if res.content else ""
            data = res.structuredContent or (json.loads(text) if text else {})
            if isinstance(data, dict) and "result" in data and "passed" not in data:
                data = data["result"]
            return data if isinstance(data, dict) else {}


def _verify(path: str, language: str) -> dict[str, Any]:
    def _runner() -> dict[str, Any]:
        return asyncio.run(asyncio.wait_for(_call_verify(path, language), timeout=VERIFY_CALL_TIMEOUT))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            data = ex.submit(_runner).result(timeout=VERIFY_CALL_TIMEOUT + 30)
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "passed": False, "result": None, "error": f"{type(e).__name__}: {e}"}
    return {"reachable": True, "passed": bool(data.get("passed")), "result": data, "error": None}


_TESTS_PASSED_RE = re.compile(r"(\d+)\s+passed")


def _tests_passed(verify_result: dict[str, Any] | None) -> int:
    """Best-effort count of passing tests from the verify summary text."""
    if not verify_result:
        return 0
    blob = json.dumps(verify_result)
    m = _TESTS_PASSED_RE.search(blob)
    return int(m.group(1)) if m else 0


# ── deterministic selector (the lossless core — no model calls) ──────────────
def select_from_candidates(candidates: list[dict], tests: dict | None = None,
                           language: str = "python", base_files: dict | None = None,
                           early_exit: bool = False) -> dict[str, Any]:
    """Run each candidate through mcp-verify in isolation; select the green one.

    candidates: [{"id": str, "files": {relpath: content}}, ...]
    tests:      {relpath: content} written into EVERY candidate dir (shared).
    base_files: {relpath: content} common scaffolding (e.g. pyproject) for all.
    Returns the selected candidate id + per-candidate verdicts. Never returns a
    red selection: if none is green, selected is None and reason says so.

    early_exit (M-Stage 6): return as soon as a candidate verifies GREEN, WITHOUT
    verifying the rest — execution-based early-exit (RASC: large sample savings at
    comparable accuracy; naive best-of-N gets less reliable as N grows). Trades
    'best among green' for 'first green', the right call for code where green==done.
    """
    if not candidates:
        return {"ok": False, "error": "no candidates supplied"}

    verdicts: list[dict[str, Any]] = []
    for cand in candidates:
        cid = str(cand.get("id", f"cand{len(verdicts)}"))
        files = cand.get("files", {}) or {}
        size = sum(len(v) for v in files.values())
        tmp = tempfile.mkdtemp(prefix=f"search-{cid}-")
        try:
            for rel, content in {**(base_files or {}), **files, **(tests or {})}.items():
                fp = Path(tmp) / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
            v = _verify(tmp, language)
            verdicts.append({
                "id": cid,
                "reachable": v["reachable"],
                "green": bool(v["passed"]),
                "tests_passed": _tests_passed(v["result"]),
                "size": size,
                "summary": (str(v["result"].get("summary")) if v.get("result") else v.get("error", ""))[:200],
            })
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # EARLY EXIT: first green wins — skip verifying the remaining candidates.
        if early_exit and verdicts[-1]["reachable"] and verdicts[-1]["green"]:
            otel_emit.record("best_of_n_result", {
                "candidates_generated": len(candidates), "candidates_verified": len(verdicts),
                "early_exit_fired": True, "selected": cid,
                "tests_passed": verdicts[-1]["tests_passed"]}, status="ok")
            return {"ok": True, "selected": cid, "early_exit": True,
                    "selected_files": files, "green_count": 1,
                    "candidates_verified": len(verdicts), "n": len(candidates),
                    "verdicts": verdicts,
                    "reason": f"early-exit: '{cid}' verified green after "
                              f"{len(verdicts)}/{len(candidates)} candidates "
                              f"({verdicts[-1]['tests_passed']} tests passed)"}

    if any(not vd["reachable"] for vd in verdicts):
        return {"ok": False, "verify_unreachable": True, "verdicts": verdicts,
                "reason": "mcp-verify unreachable — cannot select by execution; write the patch yourself"}

    green = [vd for vd in verdicts if vd["green"]]
    if not green:
        otel_emit.record("search_selected", {"selected": "none", "n": len(verdicts),
                                            "green": 0}, status="error")
        return {"ok": True, "selected": None, "verdicts": verdicts,
                "reason": "no candidate verified green — escalate or rethink the approach"}

    # prefer most tests passed, then smallest diff (least code)
    best = sorted(green, key=lambda vd: (-vd["tests_passed"], vd["size"]))[0]
    otel_emit.record("search_selected", {"selected": best["id"], "n": len(verdicts),
                                        "green": len(green), "tests_passed": best["tests_passed"],
                                        "size": best["size"]}, status="ok")
    otel_emit.record("best_of_n_result", {
        "candidates_generated": len(candidates), "candidates_verified": len(verdicts),
        "early_exit_fired": False, "selected": best["id"], "green_count": len(green)}, status="ok")
    return {
        "ok": True,
        "selected": best["id"],
        "selected_files": next((c.get("files") for c in candidates
                                if str(c.get("id")) == best["id"]), {}),
        "green_count": len(green),
        "n": len(verdicts),
        "verdicts": verdicts,
        "reason": f"selected '{best['id']}' (green, {best['tests_passed']} tests passed, "
                  f"smallest diff among {len(green)} green of {len(verdicts)})",
    }


# ── model generation (bounded; requires $VLLM_BASE_URL) ──────────────────────
def _extract_code(text: str) -> str:
    m = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip() + "\n"


def _generate_one(task_spec: str, language: str, temperature: float) -> str | None:
    if not VLLM_BASE_URL:
        return None
    payload = {
        "model": SEARCH_MODEL,
        "messages": [
            {"role": "system", "content": f"You are a precise {language} engineer. Output ONLY the "
             "complete file content in a single fenced code block, no prose."},
            {"role": "user", "content": task_spec},
        ],
        "temperature": temperature,
        "max_tokens": 1024,
    }
    try:
        with httpx.Client(timeout=GEN_TIMEOUT) as client:
            r = client.post(f"{VLLM_BASE_URL}/chat/completions", json=payload)
            r.raise_for_status()
            return _extract_code(r.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001
        return None


def generate_and_select(task_spec: str, n: int = 0, language: str = "python",
                        target_path: str = "solution.py", tests: dict | None = None,
                        base_files: dict | None = None,
                        candidates: list[dict] | None = None,
                        early_exit: bool = True, quality_threshold: float = 0.0) -> dict[str, Any]:
    """Bounded verifier-guided search. If `candidates` are supplied, skip
    generation and select among them (the cheap, always-available path). Else
    generate N candidates from $VLLM_BASE_URL (HARD subtasks only) and select.

    early_exit (M-Stage 6, default True for code): generate-and-verify ONE AT A TIME
    and return the moment a candidate verifies green — saving the cost of generating
    (and verifying) the remaining candidates. The largest saving is in generation
    (RASC reports up to ~85% fewer samples at comparable accuracy). With supplied
    candidates it short-circuits verification instead.
    quality_threshold (0-1, 0=off): when no test oracle is available, score each
    candidate against the task with the reranker and return the first above threshold.
    """
    n = DEFAULT_N if not n else n
    n = max(1, min(int(n), MAX_N))

    if candidates is None:
        if not VLLM_BASE_URL:
            return {"ok": False, "disabled": True,
                    "reason": "generation path needs $VLLM_BASE_URL; supply `candidates` to use the "
                              "selector directly, or write the patch yourself"}
        if not tests and quality_threshold <= 0:
            return {"ok": False, "error": "generation requires `tests` to select against (lossless "
                    "selection is execution-based), or quality_threshold>0 for reranker selection"}
        gen: list[dict] = []
        for i in range(n):
            # vary temperature across samples for diversity (no RNG needed)
            temp = round(0.2 + 0.6 * (i / max(1, n - 1)), 3) if n > 1 else 0.2
            code = _generate_one(task_spec, language, temp)
            if not code:
                continue
            cand = {"id": f"gen{i}", "files": {target_path: code}}
            gen.append(cand)
            # INTERLEAVED early-exit: verify (or score) THIS candidate now; stop
            # generating the rest the moment one is good enough.
            if early_exit and tests:
                sel1 = select_from_candidates([cand], tests, language, base_files, early_exit=True)
                if sel1.get("ok") and sel1.get("selected"):
                    sel1["candidates_generated"] = len(gen)
                    return sel1
            elif quality_threshold > 0:
                score = _rerank_score(task_spec, code)
                if score is not None and score >= quality_threshold:
                    otel_emit.record("best_of_n_result", {
                        "candidates_generated": len(gen), "candidates_verified": 0,
                        "early_exit_fired": True, "best_score": round(score, 4),
                        "threshold": quality_threshold, "selected": cand["id"]}, status="ok")
                    return {"ok": True, "selected": cand["id"], "selected_files": cand["files"],
                            "early_exit": True, "best_score": round(score, 4),
                            "candidates_generated": len(gen),
                            "reason": f"early-exit: candidate scored {score:.3f} >= {quality_threshold} "
                                      "(reranker; no test oracle)"}
        if not gen:
            return {"ok": False, "error": "no candidates generated (model unreachable?) — fall back"}
        candidates = gen

    return select_from_candidates(candidates, tests, language, base_files, early_exit=early_exit)


# ── Stage 4: verifier-selected parallel_draft across the conductor pool ───────
async def _call_pool_async(prompt: str, n: int) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://{VERIFY_HOST}:{ESCALATION_PORT}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("parallel_draft_pool", {"prompt": prompt, "n": n})
            text = getattr(res.content[0], "text", "") if res.content else ""
            data = res.structuredContent or (json.loads(text) if text else {})
            if isinstance(data, dict) and "result" in data and "candidates" not in data:
                data = data["result"]
            return data if isinstance(data, dict) else {}


def _call_pool(prompt: str, n: int) -> dict[str, Any]:
    """Get cross-family draft candidates from the conductor pool (escalation
    server). Degrades to an empty result if the server/role is unavailable."""
    def _runner() -> dict[str, Any]:
        return asyncio.run(asyncio.wait_for(_call_pool_async(prompt, n), timeout=POOL_CALL_TIMEOUT))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_runner).result(timeout=POOL_CALL_TIMEOUT + 30)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "candidates": [], "error": f"{type(e).__name__}: {e}"}


def parallel_draft(task_spec: str, language: str = "python",
                   target_path: str = "solution.py", tests: dict | None = None,
                   base_files: dict | None = None, n: int = 0,
                   draft_brief: str | None = None) -> dict[str, Any]:
    """Verifier-selected best-of-N across the FREE/cheap conductor pool — the
    optimal use of 'slop' models. ONLY for VERIFIABLE subtasks: `tests` (the
    objective oracle) is REQUIRED; without it this is an ambiguous task that must
    route to the synthesize role instead (returned as route_to='synthesize').

    Flow: fan out one draft per present pool family (cross-family DIVERSITY, not
    temperature sampling), extract each candidate's code, run EVERY candidate
    through mcp-verify, and select the one that goes green (most tests, smallest
    diff). If NONE pass -> route_to='synthesize'. If the pool is empty/unreachable
    -> degrade to local generation ($VLLM_BASE_URL) or route_to='local'. The local
    model integrates + checkpoints the winning diff (slop models never touch the
    repo). Never raises."""
    # ── GATE: verifiable subtasks only (objective oracle present) ─────────────
    if not tests:
        return {"ok": False, "verifiable": False, "route_to": "synthesize",
                "reason": "no test oracle supplied — this subtask is AMBIGUOUS; route to the "
                          "synthesize role (no oracle => the verifier can't select). parallel_draft "
                          "is only for verifiable subtasks."}
    n = DEFAULT_N if not n else n
    n = max(1, min(int(n), MAX_N))

    spec = draft_brief or task_spec
    prompt = (f"{spec}\n\nOutput ONLY the complete content of `{target_path}` in a single fenced "
              f"{language} code block, no prose.")

    pool = _call_pool(prompt, n)
    candidates: list[dict] = []
    sources: list[str] = []
    for c in pool.get("candidates", []) if isinstance(pool, dict) else []:
        if c.get("ok") and c.get("content"):
            cid = f"{c.get('provider')}:{str(c.get('model', '')).split('/')[-1]}"
            candidates.append({"id": cid, "files": {target_path: _extract_code(c["content"])}})
            sources.append(cid)

    if not candidates:
        # degrade: local best-of-N if the model endpoint is up, else route local
        if VLLM_BASE_URL:
            gen = generate_and_select(task_spec, n, language, target_path, tests, base_files)
            gen["draft_source"] = "local_fallback"
            gen["reason"] = (gen.get("reason", "") +
                             " | pool empty/unreachable -> local generation fallback").strip(" |")
            return gen
        otel_emit.record("draft_fanout", {"n_sources": 0, "degraded": "local"}, status="error")
        return {"ok": False, "route_to": "local", "candidates_from": [],
                "pool_error": pool.get("error") if isinstance(pool, dict) else None,
                "reason": "draft pool empty/unreachable and no $VLLM_BASE_URL — write the patch yourself"}

    otel_emit.record("draft_fanout", {"n_sources": len(candidates),
                                     "families": ",".join(sources)}, status="ok")
    # early_exit=True (M-Stage 6): the first pool draft that verifies green wins —
    # no need to verify the rest (execution-based, code where green==done).
    sel = select_from_candidates(candidates, tests, language, base_files, early_exit=True)
    sel["draft_source"] = "pool"
    sel["candidates_from"] = sources
    # none-pass fallback: the subtask was harder than 'verifiable-slop' assumed
    if sel.get("ok") and sel.get("selected") is None:
        sel["route_to"] = "synthesize"
        sel["reason"] = (sel.get("reason", "") +
                         " | none of the pool drafts passed -> route to the synthesize role").strip()
    return sel


def status() -> dict[str, Any]:
    return {
        "generation_available": bool(VLLM_BASE_URL),
        "model": SEARCH_MODEL if VLLM_BASE_URL else None,
        "default_n": DEFAULT_N,
        "max_n": MAX_N,
        "verify_endpoint": f"http://{VERIFY_HOST}:{VERIFY_PORT}/mcp",
        "pool_endpoint": f"http://{VERIFY_HOST}:{ESCALATION_PORT}/mcp",
        "note": "selector (candidates supplied) is always available; generation needs $VLLM_BASE_URL. "
                "parallel_draft fans the conductor pool over verifiable subtasks only. "
                "Use on HARD subtasks only — best-of-N competes for the one your inference host GPU.",
    }
```
