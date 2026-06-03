---
name: workflow-plan
description: "MANDATORY FIRST ACTION on EVERY task (except single-line factual lookups): call conductor_plan so the conductor authors the plan BEFORE you read, search, or write anything."
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

<!-- TRIGGERS WHEN: any new task or multi-step request — the MANDATORY first action is conductor_plan, before reading/searching/writing anything. Only single-line factual lookups are exempt. -->

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

**A plan step is impossible as written** — call `review_and_adapt`, not `reasoning_escalation`:
a referenced API doesn't exist, the planned approach can't work, or `verify` has failed 3+
times on the same step. `review_and_adapt(issue="…", current_step=N, completed_steps=[…],
cwd="…")` asks the conductor to REVISE PLAN.md from step N onward (your completed steps are
preserved). **Do not attempt impossible implementations and do not spin** — ask the
conductor to revise, then execute the revision (re-read PLAN.md).

## When — UNCONDITIONAL

Call `conductor_plan` **FIRST on every task**, with no size threshold: a one-file change
still gets a (fast, idempotent — an existing signed PLAN.md is reused) plan before you
touch anything. Do not let the task feel "too small to plan" — the call is cheap (free
Kimi rung) and the plan is the contract verify checks against. The heavier full decompose
(native kanban `auto_decompose`, planner/coder/reviewer delegation) is reserved for tasks
spanning **more than ~5 files** or with non-trivial architecture — but the `conductor_plan`
call itself is NEVER gated on size.

## The only exception

A single-line factual lookup — "what is X", "show me Y", "which file defines Z" — does
not need a plan; answer it directly. **Everything else gets `conductor_plan` first. When
in doubt, plan.** And mid-run, if you get stuck or an issue repeats, the ladder rises back
to the conductor — see the escalation triggers above and `workflow-escalate`.

## Why

Decomposition + native delegation gives planner/coder/reviewer separation
without building a custom multi-agent framework. Planning against retrieved
reality (not guesses) is what keeps large autonomous changes coherent.

## Don't over-build

These workflow skills are intentionally light. Hermes's self-improvement loop
and the weekly DSPy evolution cron refine them from real session history over
time — resist hand-tuning them into brittle scripts.
