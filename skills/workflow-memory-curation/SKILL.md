---
name: workflow-memory-curation
description: "Deliberately curate the always-in-context core-memory block at task boundaries — keep high-signal, evict stale."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, memory, core-memory, curation, hermes-max]
    category: hermes-max
    related_skills: [workflow-task-start, workflow-task-finish]
---

# Curate core memory (own your working memory)

Core memory is the small, **always-in-context** block of the highest-signal facts
about the current project — conventions, gotchas, the architecture one-liner. It
is **size-bounded** (it costs context on every turn), so it must be *curated*, not
just accumulated. This is MemGPT's insight: the agent deliberately owns its
working memory, distinct from the auto-accumulated KG triples and RAG chunks.

It is wired to Hermes's **native MEMORY.md**, so anything you put here is
auto-loaded into context — one source of truth, no parallel store.

## At task START

- `core_memory_get()` — read the block to orient. Trust it, but verify any
  file/symbol it names still exists before acting (it reflects a past moment).

## At task END / boundaries (the curation pass)

1. `core_memory_get()` and ask: **what's the highest-signal thing learned this
   task that a future session must know** (a convention, a non-obvious gotcha, a
   key decision)? Not routine facts — those belong in the KG / RAG.
2. Add it: `core_memory_append("<one crisp fact>")`. If it's rejected for
   overflow, that's the signal to **prune**.
3. Prune / update: `core_memory_replace(old=..., new=...)` to fix a stale fact,
   or `core_memory_replace(block="<the whole rewritten block>")` to do a full
   curation pass — keep the few highest-signal facts, evict what's stale or
   now-obvious. Keep it well under the limit; tighter is better.

## Rules

- **High-signal only.** If it's derivable from the code or already in the KG,
  it doesn't belong in the always-in-context block.
- **Bounded.** The append tool rejects overflow on purpose — respond by pruning,
  never by trying to grow the window.
- **Deliberate.** This is a conscious edit, not a dump. One good line beats five
  vague ones.
- This curation pass is itself a future GEPA optimization target (it improves
  with feedback over time).
