---
name: workflow-deep-research
description: >-
  Drive mcp-research's deep_research loop when a task needs CURRENT or EXTERNAL
  knowledge beyond pretraining + RAG — a novel/recent framework or release, "what
  is the current best X", or cross-referencing multiple sources. Fully local and
  sovereign (SearXNG + Crawl4AI/trafilatura + the chat model). Gate DEPTH on the
  shared difficulty/scope signal: a quick lookup is NOT a multi-source synthesis.
---

# workflow-deep-research

> **NEVER call `deep_research` more than once per session, under any circumstances.**
> If the result has `confidence: low` (or `adequate`) that is **NORMAL** — by design
> claims are effectively single-sourced after the echo-chamber guard dedups
> overlapping sources, so the score is not a measure of whether you have enough.
> **Proceed immediately to implementation with whatever synthesis was returned**;
> the verify gate on your code is the real quality check. **A second `deep_research`
> call is always wrong** — it just burns wall-clock without changing the result. For
> any follow-up, use `search_code` / `mcp-docs` (`search_docs` / `research_topic`),
> never another `deep_research`.

Use this when the answer is **not reliably in pretraining or the local RAG/KG**:
a new framework, a recent release, "the current best/most-recommended X", or a
question that needs several independent sources reconciled. For a fact you already
know or that one `search_code`/`search_docs` call answers, do **not** spin up the
full loop.

## Gate the depth (don't overspawn)

First classify scope (reuse `classify_difficulty` / the shared signal):

- **Quick lookup** → `mcp-docs.search_docs` / `research_topic`, or one
  `explore([...])` call. Stop.
- **Real synthesis** (multi-source, contested, or you must be confident) →
  `mcp-research.deep_research(question, max_loops, max_total_sources)`.

Match `max_loops` / `max_total_sources` to scope. Bigger is not better — the caps
exist to prevent the overspawning failure mode.

## How to run it

1. **Make the question specific.** If it's underspecified (budget, version,
   platform, region), narrow it first — a vague question retrieves an echo chamber.
2. `deep_research(question)` runs `plan → develop → explore → verify → synthesize`
   for you, bounded. Or drive the stages yourself for control:
   - `plan_research(question)` → inspect the sub-goals (the PLAN is checkable;
     a wrong plan step is the most damaging — sanity-check it).
   - per sub-goal: `develop_queries(subgoal)` → `explore(queries, seen_urls=…)`,
     **threading `seen_urls` across calls** so you keep breaking echo chambers.
   - `verify_claims(claims)` **before** asserting anything — cross-check each
     material claim against ≥2 **independent** (distinct-domain) sources.
   - `synthesize(question, verified_findings)` → a citation-backed report.

## Discipline (what makes it approach proprietary quality)

- **Verify before you assert.** Single-sourced or conflicting claims are reported
  as such — surface them, don't launder them into confident prose.
- **Cite every claim.** Each statement maps to a source URL.
- **Prefer primary sources.** Official docs, papers, standards, and project repos
  over SEO content farms (the ranker already does this — don't fight it).
- **Stop at the cap honestly.** When loops/budget are exhausted, end with a
  **confidence + gaps** note rather than padding with low-confidence filler.
- **Confidence is advisory — act on the synthesis regardless of its level.** A
  `confidence: low` result is the *expected* outcome when the echo-chamber guard
  deduped overlapping sources (the same fact corroborated once, not N times) — it
  does **not** mean the findings are wrong or the report is incomplete. The result
  is `actionable: true` either way. **Proceed to implementation with it; the verify
  gate on your code is the real quality check.** Do **NOT** re-run `deep_research`
  to chase a higher confidence score — that is the multi-call trap and it just
  burns wall-clock without changing the deduped-corroboration math.
- **It compounds.** `deep_research` writes the brief + entities into RAG/KG, so a
  later related run starts ahead — prefer it over re-researching from scratch.

## Degradation

Fully sovereign on both deploy profiles. SearXNG down → explore is empty (say so);
Crawl4AI down → trafilatura; reranker absent → authority-heuristic ranking; chat
model unset → deterministic plan/queries/synthesis (a cited bullet brief). None of
these crash — they lower confidence, which you report.
