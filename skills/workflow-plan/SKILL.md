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

# Plan — the conductor plans, you execute

## Your FIRST action on any new task

Before any file write, before any reasoning, **before you think through the
architecture at all** — call the `conductor_plan` MCP tool:

```
conductor_plan(task="<the task>", cwd="<the working directory>")
```

**Do not plan internally. Do not reason through the design yourself.** The conductor
routes the plan through a strong cloud reasoner (Kimi-K2.6:free → DeepSeek-V4-Pro) with
an 8192-token thinking budget and writes a signed `PLAN.md` to the working directory.
Your job is to **execute against what it returns** — transcribe the FILE SPEC, follow
the Steps, satisfy the `DONE_CONDITION`. The architectural thinking is the conductor's;
the implementation is yours.

`conductor_plan` auto-fetches the repo map (`get_repo_map`) first, so the planner has
full structural context. On a greenfield task it plans from the prompt.

**This is enforced, not advisory.** The verify gate rejects any `PLAN.md` that lacks the
conductor signature —

```
## Plan authored by: <model> via conductor
```

— so a plan you wrote yourself **cannot pass verify**. Only `conductor_plan` produces a
valid plan. If verify reports `PLAN.md is not conductor-authored`, you skipped this step:
call `conductor_plan` and start over against its output.

Then **execute**: each unit must pass `verify` (`workflow-task-finish`) before it's
complete; the gate IS the `DONE_CONDITION`.

## While executing — think briefly, then ask or act

You have a **thinking budget of ~1024 tokens** per step: enough to read the relevant
PLAN.md step, decide which file to write, and write it. It is **not** enough to redesign
architecture, derive a novel concurrency invariant, or invent a property-test strategy
from scratch — and you should not try. When you hit a question you can't resolve
confidently within that budget, **do not keep reasoning** — call `reasoning_escalation`
with the specific question and act on the precise answer it returns. Ask and act.

Three times to escalate (all return a `## Frontier guidance` block — put it at the top
of your next step):

- **You're unsure** (`trigger="self_declared"`): an architectural/algorithmic question
  you can't resolve in budget. `reasoning_escalation(question="…", context="<code>",
  trigger="self_declared", budget="standard")`.
- **A plan step is marked `complexity: HIGH`**: call `reasoning_escalation` with the step
  description + its `note:` **before** attempting the implementation
  (`trigger="complex_step"`). The planner already flagged which steps are hard — use that
  foresight proactively.
- **verify failed twice on the same file**: if a `verify` result includes an `escalate`
  recommendation, call `reasoning_escalation` with it (`trigger="verify_double_fail",
  budget="deep"`) and re-implement against the guidance. Two failures means the approach
  is wrong, not just buggy — a third attempt at the same approach wastes effort.

Escalation is capped per run (standard×5, deep×2) so it can't burn the credit; past the
cap, proceed with what you have.

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
