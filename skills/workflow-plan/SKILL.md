---
name: workflow-plan
description: "For tasks over ~5 files, plan and decompose before coding using native plan + kanban."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, planning, kanban, decomposition, hermes-max]
    category: hermes-max
    related_skills: [workflow-task-start, workflow-task-finish]
---

<!-- TRIGGERS WHEN: "For tasks over ~5 files, plan and decompose before coding using native plan + kanban." version: 1.0.0 author: Hermes Max license: MIT platforms: [linux, macos, -->

# Plan — decompose before large changes

## Task protocol — required, before any code or file action on a new task

This is not optional. The verify gate will **not** pass a task with no `PLAN.md`
(a task without a plan has no `DONE_CONDITION` and cannot be verified).

1. **Map the scope first.** Call `get_repo_map(cwd)` (the scopemap MCP) to see the
   codebase structure — one line per file, fits the window even on a huge repo — or
   to confirm it's a **greenfield** task with no existing code (empty map → proceed
   straight to step 3).
2. **Request only what you need.** Issue a `CONTEXT_REQUEST` via
   `request_context(cwd, need_full=[...], need_signatures=[...], need_nothing=[...])`
   so the window holds exactly the files you'll edit (full) and call (signatures),
   nothing else — surgical context instead of ingesting everything or flying blind.
3. **Produce the plan contract.** Use the conductor's synthesize/plan path (Kimi/
   V4-Pro via the inference fabric) to write a concrete `PLAN.md`: the files to
   touch, the order, the risky steps, how each is verified, and an explicit
   `DONE_CONDITION` (e.g. "pytest green, N tests"). The plan is a CONTRACT — every
   subsequent action executes against it.
4. **Only then execute.** Begin file actions against the plan. Each unit must pass
   `verify` (`workflow-task-finish`) before it's complete; the gate IS the
   `DONE_CONDITION`.

## When

Always run the map+plan step on a new task. The full decompose (native kanban
`auto_decompose`, planner/coder/reviewer delegation) is for tasks spanning **more
than ~5 files** or with non-trivial architecture — don't free-solo large changes.

## Why

Decomposition + native delegation gives planner/coder/reviewer separation
without building a custom multi-agent framework. Planning against retrieved
reality (not guesses) is what keeps large autonomous changes coherent.

## Don't over-build

These workflow skills are intentionally light. Hermes's self-improvement loop
and the weekly DSPy evolution cron refine them from real session history over
time — resist hand-tuning them into brittle scripts.
