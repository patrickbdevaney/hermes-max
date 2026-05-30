# mcp-research

**SOTA local deep-research** — the canonical four-stage architecture
(`plan → develop → explore → verify → synthesize`) built as bounded, deterministic
MCP tools on top of the **existing sovereign loop** (SearXNG + Crawl4AI/trafilatura
via `mcp-docs` + the local chat model + RAG/KG). **No external API**, on either
deploy profile.

It is *not* a framework import (no `local-deep-research` / LangChain): the research
is explicit that quality comes from a small set of patterns — a well-configured
agent + SearXNG has beaten dedicated frameworks — so the value-bearing patterns are
built here as native tools.

```
plan_research → develop_queries → explore → verify_claims → synthesize
  (PLAN.md)      (diverse,         (dedup +   (≥2 indep.     (citation-
                  deduped)          authority   sources)       backed)
```

## Engineered against the four named failure modes

| Failure mode | Countermeasure (tested invariant) |
|---|---|
| **Echo-chamber retrieval** | query diversity + **URL & n-gram content dedup** across loops |
| **Source-quality / SEO bias** | **authority-aware re-ranking** (primary/official/papers > content farms; optional cross-encoder on top) |
| **Planning hallucination** | external checkable **PLAN.md** + **intermediate `verify_claims`** (a weak claim is flagged → lands in *gaps*, never asserted) |
| **Sub-agent overspawning** | hard **per-query / per-loop / total-source** caps; single-threaded by default |

All four are asserted deterministically in `smoke_test.py` (Part C) with no live
services.

## Tools

- `plan_research(question)` — decompose into 2-5 complementary sub-goals + roadmap,
  written to external `PLAN.md` state (the plan is itself checkable).
- `develop_queries(subgoal, n=4)` — diverse, complementary queries, deduped by
  n-gram similarity (the direct echo-chamber counter).
- `explore(queries, seen_urls?, max_sources_per_query=3, max_total=8, category?)` —
  iterative web exploration with URL + n-gram content dedup, authority-aware
  ranking, and hard breadth caps. Returns fetched sources + provenance + filter
  counts. Pass prior `seen_urls` across loops to keep breaking echo chambers.
- `verify_claims(claims, min_sources=2)` — cross-check each claim against ≥2
  **independent** (distinct-domain) sources; flags single-sourced/conflicting.
- `synthesize(question, verified_findings, plan?)` — structured, **citation-backed**
  report distinguishing well-supported / single-sourced / conflicting, quotes/code
  preserved verbatim, with a confidence + gaps section.
- `deep_research(question, max_loops=3, max_total_sources=8, category?, compound=True)`
  — the end-to-end orchestrator. Bounded loops + wall-clock budget. **Compounds**
  the final brief + key entities into RAG/KG so a later related run starts ahead.

## Structured source fan-out (research_engine Stage 1)

Alongside the SearXNG web layer, `mcp-research` reaches free structured sources
directly — the biggest quality jump per unit effort. A keyword **classifier-router**
maps a query to a source set + bounded per-source budget; each adapter's ranked list
is merged with **Reciprocal Rank Fusion** (RRF, `Σ 1/(k+rank)`, k≈60 — pure
arithmetic, no model), rewarding docs that rank consistently across sources.

| Tool | Source | Key? | Notes |
|---|---|---|---|
| `arxiv_search(query, days_back?, categories?, limit)` | arXiv Atom API | **none** | `days_back` OPTIONAL (omit ⇒ seminal work reachable, no 90-day window); `categories` targets `cs.CR`/`cs.LG`/`cs.DC`/`cs.AI` |
| `semantic_scholar_search(query, limit)` | Semantic Scholar | **none** (5k/5min pool; key ⇒ 1 RPS) | abstracts, authors, **citation counts**; attribution required when displayed |
| `semantic_scholar_citations(paper_id, direction, limit)` | Semantic Scholar | **none** | **citation-graph traversal** — `references` = backward (seminal), `citations` = forward (frontier). Turns search into "find the canon + latest" |
| `github_search(query, search_type, limit)` | GitHub REST | **`GITHUB_TOKEN`** (free PAT) | repos/code/issues; **absent ⇒ no-op** (web layer covers it), present ⇒ 30 req/min |
| `hn_search(query, limit, tags)` | HN Algolia | **none** | practitioner signal (points, comments) |
| `stackexchange_search(query, site, limit)` | Stack Exchange | **none** (300/day; key ⇒ 10k) | vote/tag-ranked Q&A; routed for library/how-to |
| `ethresearch_search(query, limit)` / `ethresearch_topic(id, slug)` | ethresear.ch (Discourse) | **none** | Ethereum-research **frontier** consumer tools miss — public `.json` read, full post text |
| `eip_erc(query, limit)` | ethereum/EIPs + ERCs | **none** (raw md; token ⇒ code-search) | naming a number (EIP-4844, ERC-20) fetches the **full canonical spec** + parsed front-matter |
| `ietf_rfc(query, limit)` | RFC-Editor | **none** | full RFC text by number; routed only on rfc/ietf mention (optional per spec) |
| `multi_source_search(query)` | (orchestrator) | — | classify → route → RRF-fuse; returns fused candidates + per-source status |
| `classify_query(query)` | (router) | — | category + source set + budgets; **always includes `searxng`** |

**No source is load-bearing.** Every adapter returns `{ok, results, error}` (string
errors, never exceptions), is presence-gated (skips cleanly without its token), and
the router always routes `searxng` so the existing web layer answers even if the
entire structured layer is down. Asserted in `smoke_sources.py` (no live services).

**Source-limit volatility (why degrade-to-web is the standing hedge):** Bing Search
API retired Aug-2025, Brave free tier removed Feb-2026, OpenAlex key-gated Feb-2026,
and Semantic Scholar / arXiv limits change without notice (both return `429` under
load). The keyless sources are the durable core; optional keys (`GITHUB_TOKEN`,
`SEMANTIC_SCHOLAR_API_KEY`, `STACKEXCHANGE_KEY`) only lift limits — **no new required
keys**. See `.env.example` → *DEEP-RESEARCH SOURCE FAN-OUT*.

## On-disk corpus + provenance + lazy distillation (research_engine Stage 3)

Research content is no longer distilled-away on ingest. Instead:

- **Sovereign markdown corpus on disk** — `ingest_research(...)` writes the **full,
  untruncated** extracted content to `corpus/{namespace}/{source_type}/{slug}.md`
  with **YAML front-matter provenance** (`source_url`, `title`, `authors`, `date`,
  `retrieval_query`, `source_type`, `citation_count`, `authority_score`,
  `ingested_at`, `session_id`). Greppable, git-versionable, human-readable, and
  **independent of the vector store**. Idempotent (re-ingest overwrites the slug).
  Location: `RESEARCH_CORPUS_DIR` (default `~/.hermes-max/corpus`; point at a repo
  path to version it).
- **Full chunks in RAG, resolvable** — the same full text is indexed into the hybrid
  store; each chunk's `source` is the corpus relpath, so a retrieved chunk resolves
  straight back to its on-disk document + provenance via `resolve_source(source)`
  (the seam the Stage-5 verify gate uses).
- **Lazy, query-time distillation** — `distill_for_query(query, chunks, source_type)`
  distills **only the retrieved chunks**, at query time, preserving technical detail
  verbatim. Density-routed: dense sources (arxiv/semantic_scholar/eip_erc/ietf_rfc/
  audit) → cheap-cloud (**DeepSeek via the conductor's steer role**) when
  `RESEARCH_CLOUD_DISTILL=true`; everything else → local Qwen; **no model anywhere →
  raw chunk concatenation** (fully sovereign, still honest).

> The spec says "Qdrant"; the actual RAG is `mcp-codebase-rag`'s SQLite + FTS5 +
> sqlite-vec hybrid store (same full-chunks-plus-embeddings contract). The vector
> store is *used*, not modified — full provenance lives in the on-disk corpus.

Asserted in `smoke_corpus.py` (temp dir, monkeypatched RAG/LLM/conductor): full
untruncated write, idempotency, full-content RAG index with resolvable source,
density routing (all four paths), provenance round-trip, 45K-char paper untruncated.

## Extraction ladder + dedup / authority / citation-graph (research_engine Stage 4)

- **Extraction ladder** — `extract_url(url)` falls through **Trafilatura** (fast,
  CPU, static articles) → **Crawl4AI** (JS-rendered, via `mcp-docs.fetch_clean`) →
  **Jina Reader** (`r.jina.ai`, blocked/complex pages + PDFs; `JINA_API_KEY` lifts
  the limit). The order is chosen by page type (PDFs and JS hosts reorder), each
  rung is best-effort, and a rung that fails *or raises* falls through to the next.
  *(Trafilatura isn't installed in this venv → that rung no-ops and the ladder
  starts at Crawl4AI; add `trafilatura` to enable the fast first rung.)*
- **Semantic dedup** — `semantic_dedup(items)` collapses near-duplicates by
  **embedding cosine** (not just URL/n-gram), keeping the most authoritative
  instance of each cluster so paraphrased SEO mirrors don't dominate. Degrades to
  n-gram Jaccard when the embedding endpoint is down.
- **Authority ranking** — `authority_rank(items)` scores `domain authority +
  log(citation_count) + recency`, surfacing an arXiv primary over a blog summary
  while anchoring to seminal work.
- **Citation-graph edges** — `citation_edges(paper, refs, cites)` turns Semantic
  Scholar references/citations into normalized `{src, rel:'cites', dst}` edges with
  provenance, ready to become KG edges in Stage 5.

Asserted in `smoke_extract.py`: ladder fall-through (incl. a raising rung) +
PDF reorder + all-fail, primary-over-blog ranking, embedding + n-gram dedup, edge
directions.

## KG provenance + decomposed verification gate (research_engine Stage 5)

The grounding layer. Research findings land in the knowledge graph **with
provenance**, and every synthesized claim is verified by **retrieval, not
generation**.

- **KG provenance + temporal validity** — `kg_add_episode` / `kg_add_fact_edge` /
  `kg_ingest_citation_edges` / `kg_mark_superseded`. Entities are papers/repos/
  protocols/EIPs/people/techniques; edges use a fixed vocabulary (`cites` /
  `supersedes` / `implements` / `audits` / `contradicts` / `authored_by` — an
  invented relation is rejected). **Every fact edge carries its `source_id`** plus
  `valid_from`/`valid_until`, so a 2024 claim superseded in 2026 is *marked*, not
  silently kept alongside the new one.
- **Decomposed verification gate** — `verify_claim` / `verify_findings` resolve each
  claim's `source_id` to its **stored chunk** (`corpus.resolve_source`), then run a
  cheap **entailment** pass (local Qwen; DeepSeek via conductor for dense sources).
  ≥2 independent supporting domains ⇒ *well-supported*; no resolvable/entailing
  backing ⇒ flagged (*unsupported* / *single-sourced*), **never asserted**.
  **Contradictions are surfaced with BOTH citations** (`surface_contradictions`) —
  never averaged, which matters when research drives an architecture decision.
- **Query-diversity decomposition** — `decompose_question` breaks a question into
  complementary sub-questions, each with diverse paraphrase angles + per-source
  query syntax (arXiv field prefixes ≠ GitHub qualifiers ≠ web) + optional HyDE; the
  searches fuse via RRF (Stage 1). Degrades to deterministic variants with no model.

> The spec says Graphiti/Neo4j; the actual KG (`mcp-knowledge-graph`) is a
> single-file SQLite store (its own header: *"Deliberately NOT built: Neo4j +
> Graphiti + Cognee"*). Same contract — entities, directed relations, and a `props`
> bag for source IDs + temporal validity — used, not modified.

Asserted in `smoke_verify.py`: resolvable-source well-supported, entailment flags
an unsupported claim, contradiction surfaced with both citations, KG edges carry
source_id + temporal validity, invented relation rejected, supersede stamps
`valid_until`, decomposition + per-source syntax + deterministic degrade.

## Banyan content-evolution — long-horizon autonomy (research_engine Stage 6)

> **THE HARD LINE (enforced in code):** the unattended loop may evolve **CONTENT** —
> which research directions to explore, the RAG corpus, the KG, and the skill
> library — but **NEVER MACHINERY** (no `mcp-*` server code, Hermes core, router, or
> tool `.py`/config). `banyan.is_machinery_path` + `_guard_content_write` refuse any
> write outside the content whitelist, and `smoke_banyan.py` asserts a full cycle
> leaves every `.py` byte-identical. Machinery changes require a human Claude Code
> session — never the loop.

- **`banyan_select`** — UCB1 explore-exploit over research namespaces
  (`utility*priority + c*sqrt(ln(N)/n_i)`); unvisited namespaces get an infinite
  exploration bonus (visited despite lower utility). A pending operator **directive
  preempts** selection (`banyan_set_directive`) — supervised-steer OR unattended-
  explore, same machinery.
- **`banyan_update`** — `visit_count++`, running utility (0.8 history / 0.2 new),
  marginal-gain history (last 20).
- **`banyan_detect_saturation`** — two signals: embedding-drift (new research too
  similar to the namespace corpus centroid ⇒ retreading) and marginal-gain decline.
  On saturation: flag, **stop investing, and surface to the operator** (sovereign
  `surfaced.jsonl` log; Telegram optional) — never silently churn.
- **`banyan_generate_standing_tasks`** — empty queue ⇒ standing research tasks
  ("what's new in {ns} since {last_ingest}") so cycles never idle.
- **`banyan_write_skill`** — refine markdown SKILLS at runtime (content), gated by
  the maturity check (`SELF_IMPROVEMENT_ENABLED` + 200 tasks / 30 days / 50 skills)
  AND the machinery guard (a non-`.md` / machinery path is refused).
- **`banyan_next_action`** — one unattended cycle: directive interrupt OR Banyan
  self-direction + the chosen namespace's next standing task. Selection only — the
  agent runs the research; this module never touches machinery.

Asserted in `smoke_banyan.py`: UCB1 explores the underexplored then swings to
exploitation, utility/gain math, both saturation signals + operator surfacing,
directive preemption, standing-task generation, gated skill writes, and the
**no-machinery-write** invariant (guard + full-cycle hash check).

## Backends (all local; each degrades gracefully)

| Env var | Default | Down ⇒ |
|---|---|---|
| `VLLM_BASE_URL` | (chat model) | deterministic plan/queries/verify/synthesis (no LLM) |
| `DOCS_MCP_URL` | `http://127.0.0.1:9109/mcp` | SearXNG down ⇒ empty explore; Crawl4AI down ⇒ trafilatura (handled in mcp-docs) |
| `RAG_MCP_URL` / `KG_MCP_URL` | `9102` / `9103` | brief not compounded (reported) |
| `RERANK_BASE_URL` | (unset) | authority-heuristic ranking only |

Bounds (overspawning guard): `MAX_RESEARCH_LOOPS`, `RESEARCH_MAX_TOTAL_SOURCES`,
`RESEARCH_MAX_SOURCES_PER_QUERY`, `RESEARCH_WALL_BUDGET_S`, `RESEARCH_MIN_SOURCES`.

## Deploy profiles

Runs on **both** `gpu_local` and `lean_cloud` — it is orchestration and uses
whatever chat endpoint `$VLLM_BASE_URL` points at (local or cloud). On `lean_cloud`
the reranker is typically absent → authority-heuristic ranking; extraction uses
trafilatura when Crawl4AI/Docker is absent. Quality is best with a strong chat
model + reranker, but the loop is fully functional without them.

## Run / health / test

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MCP_RESEARCH_PORT=9110 .venv/bin/python server.py
.venv/bin/python smoke_test.py     # A pure, B degraded, C 4 invariants, D e2e, E boot
.venv/bin/python smoke_sources.py  # Stage 1+2: adapter parsing, gating, RRF, routing, degrade
.venv/bin/python smoke_corpus.py   # Stage 3: on-disk corpus, provenance, lazy distill, resolve
.venv/bin/python smoke_extract.py  # Stage 4: extraction ladder, dedup, authority, citation edges
.venv/bin/python smoke_verify.py   # Stage 5: KG provenance + decomposed verification gate
.venv/bin/python smoke_banyan.py   # Stage 6: UCB1, saturation, directive, skills, NO-machinery
bash ../scripts/eval-research.sh    # honest quality number on a small fixed set
```

## Isolation

Independent process. If killed, Hermes reports the tools unavailable and the agent
degrades to single-shot search; it never hard-fails the agent. Every backend has a
local default or graceful fallback.
