---
name: workflow-tool-selection
description: A strict tiered ladder consulted BEFORE reaching for any expensive tool — answer from context first, climb only when each rung fails. Caps cost and latency by never firing deep_research/escalation when a cheaper rung would answer.
trigger: before invoking ANY retrieval or model/research tool — pick the lowest rung that can answer
---
# Climb the ladder from the cheapest rung. Never skip down to an expensive tool.

Every tool below the context window costs tokens, latency, or money, and the costs grow fast as you
descend. Before you reach for one, ask: *what is the CHEAPEST rung that can answer this?* Start there.
A lower rung is tried **only after** the rungs above it have failed to answer — never in parallel,
never as a first guess. This is the single most important habit for staying fast and cheap; pair it
with [[workflow-effort-routing]] (how hard to think) and [[workflow-deadline-discipline]] (stop
deliberating, act).

## The ladder (try top-down; descend only on failure)

| # | Rung | Tool | Use when | Cost |
|---|------|------|----------|------|
| 1 | **Context window** | *no tool* | the answer is already in this conversation / the open files / what you just read | free |
| 2 | **Known codebase pattern** | `search_code` (also `get_symbol_context`, `retrieve_related`) | "how does THIS repo already do X" — an existing symbol, caller, or convention | ~free, sub-second |
| 3 | **Framework / API question** | `search_docs` (mcp-docs corpus) | a library/framework/API fact that may already be in the docs corpus | cheap, local |
| 4 | **Prior decision / knowledge** | `recall_about` (KG; also `query_graph`) | "did we already decide / learn / record this" — an earlier choice, entity, or relation | cheap, local |
| 5 | **Hard novel problem** | `conductor_steer` → `conductor_synthesize` | none of 1–4 answer and it needs real reasoning/generation, not retrieval | local model time |
| 6 | **Needs recent knowledge** | `deep_research` | the answer requires CURRENT external knowledge **not in the corpus** (rung 3 already checked) | minutes + sources |
| 7 | **Frontier-novel, synth failed** | `escalate` (Opus tier) | `conductor_synthesize` failed **twice** on a genuinely frontier-novel problem | $$ / may be OFF |

Each rung answers a *different kind* of question — retrieval (2–4), reasoning (5, 7), or fresh
external knowledge (6). Climbing in order means you never pay for fresh-knowledge research or frontier
escalation to answer something the repo, the docs, or the KG already knew.

## Task-class entry points (where to START on the ladder)

Most tasks should NOT start at rung 1 and grind upward — classify the task (use the difficulty signal
from [[workflow-conductor]] / `classify_difficulty`) and enter at the right rung:

- **MECHANICAL** (apply a planned diff, rename, format, obvious fix, run the verifier): **start at
  rung 1 and NEVER go deeper.** If the context window doesn't already hold what you need, at most one
  `search_code` (rung 2) — a mechanical task that "needs research" is mis-classified; re-plan instead
  of descending. Mechanical work never touches rungs 5–7.
- **HARD** (non-obvious bug, design with downstream consequences, flagged HARD): **start at rung 4** —
  check prior decisions in the KG first (don't re-derive what was decided), then climb 5 → (6/7 only
  if truly required). Rungs 2–3 are still worth a quick look if a codebase/API fact is implicated.
- **NOVEL** (frontier problem, genuinely new territory, needs current external knowledge): **start at
  rung 6** — but **only after the corpus pre-check below**. If `deep_research` synthesis still can't
  close it and the problem is frontier-novel, rung 7.

## The deep_research corpus pre-check (MANDATORY before rung 6)

`deep_research` is the most expensive rung short of Opus — minutes of wall-clock and external sources.
**Before firing it, ALWAYS check whether the RAG/docs corpus already has relevant material:**

1. `search_docs("<the question>")` — the on-disk research corpus (prior `deep_research` runs compound
   their briefs here).
2. `search_code` over ingested docs / `recall_about` for the topic's entities (rungs 2–4).

If any of these returns relevant, current-enough material, **use it and STOP** — do not run
`deep_research`. Only when the corpus genuinely lacks it (or it's plainly stale) do you fire research.
This is the whole point of the compounding corpus: each run makes the next one cheaper, so a topic is
researched from scratch **once**. See [[workflow-deep-research]] for running it well once you commit.

## Discipline

- **One rung at a time.** Fire the cheapest plausible rung, look at the result, and only descend if it
  genuinely failed to answer — not because you'd "rather" use the bigger tool.
- **Name the rung you're on** in your reasoning ("rung 2: search_code for the existing handler") so a
  jump to rung 6 is visibly justified by failures at 2–5, not a reflex.
- **Failure = climb, not retry-louder.** If rung 5 (`conductor_synthesize`) fails, that's the signal to
  consider rung 6/7 — but a *second* synth failure on frontier-novel is the only gate to rung 7
  ([[workflow-escalate]]); below frontier-novel, a synth failure means re-plan, not escalate.
- **Respect availability.** Rung 7 (Opus) may be OFF (no key) and rungs 5–6 are gated by the live
  budget/conductor; if a rung is unavailable the ladder degrades to the best available lower rung and
  says so — never a hard stop.
- Skipping straight to `deep_research` or `escalate` for something the context window, the codebase,
  the docs corpus, or the KG already holds is the single most expensive mistake this ladder prevents.
