---
name: workflow-retrieve-before-act
description: >-
  Before ANY external API call, web fetch, or research query, ALWAYS check the
  existing corpus first with search_code. If the corpus has relevant material, use
  it. Only escalate to external tools if search_code returns nothing relevant. This
  is the brain-first lookup pattern — it fires on every message that is about to
  reach outside the local stack.
---

# workflow-retrieve-before-act

<!-- TRIGGERS WHEN: about to make any external/web/research call (every such message) -->

The single most expensive recurring mistake is reaching outside the stack for
something the corpus, codebase, or KG already holds. Make corpus-first **systematic**,
not dependent on remembering it.

**Deterministic rule (not advice):**

1. Before `deep_research`, `fetch_clean`, `research_topic`, or any web/API call, run:
   `search_code("<the question>")` (and `recall_about` for entities) over the corpus.
2. If it returns relevant, current-enough material → **use it and stop.**
3. Only if it returns nothing relevant → escalate to the external tool (which is
   itself rationed: see [[workflow-tool-selection]] and the corpus-first / exhaustion
   / budget gates the research server enforces).

First action when this skill applies: call
`record_skill_fired("retrieve-before-act")` so trigger reliability is measurable.

This is the same compounding-corpus principle the deep_research corpus-first gate
enforces at the server level — this skill makes it the agent's default reflex for
*every* outward call, not just research.
