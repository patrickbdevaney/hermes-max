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
bash ../scripts/eval-research.sh    # honest quality number on a small fixed set
```

## Isolation

Independent process. If killed, Hermes reports the tools unavailable and the agent
degrades to single-shot search; it never hard-fails the agent. Every backend has a
local default or graceful fallback.
