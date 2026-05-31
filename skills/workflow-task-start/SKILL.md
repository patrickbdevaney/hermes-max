---
name: workflow-task-start
description: "Ground every coding task in the codebase + knowledge graph before acting."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, rag, knowledge-graph, grounding, hermes-max]
    category: hermes-max
    related_skills: [workflow-plan, workflow-task-finish]
---

<!-- TRIGGERS WHEN: "Ground every coding task in the codebase + knowledge graph before acting." version: 1.0.0 author: Hermes Max license: MIT platforms: [linux, macos, windows] me -->

# Task Start — ground yourself before writing code

Run this at the **start** of any coding/engineering task. It is the compounding
advantage: you begin already knowing the user's stack instead of reading cold.

## Steps

1. **Retrieve from the codebase.** Call `search_code` (mcp-codebase-rag) with
   2-3 phrasings of what the task touches (feature names, error messages, the
   modules involved). Read the returned `path:line` locations and snippets to
   learn the existing patterns. If results look thin, the repo may be unindexed —
   call `index_repo(path)` once, then search again.
2. **Recall prior knowledge.** For each key file/service/decision the task
   touches, call `recall_about` (mcp-knowledge-graph). Pull in any past
   decisions, known bugs, and relationships so you don't relitigate solved
   problems or repeat past mistakes.
3. **Find existing conventions.** Use `get_symbol_context` for any symbol you're
   about to modify, so your change matches the surrounding code's style and
   contracts.
4. **Size the task.** If it spans more than ~5 files or is architecturally
   non-trivial, stop and run the `workflow-plan` skill before coding.

## Graceful degradation

If any of these tools reports unavailable (a server is down), note it briefly
and proceed with normal exploration (read/grep). Never block or crash on a
missing tool — these augment your workflow, they don't gate it.

## Why

The win condition is persistence/compounding: retrieval-grounded context plus a
queryable memory of past decisions is what a cold-start agent cannot match. Pay
the small upfront retrieval cost on every task.
