---
name: workflow-plan-contract
description: >-
  The CONTRACT the expensive planner (V4-Pro / the synth role) must satisfy when it
  writes PLAN.md for a substantive build, so the cheap local executor implements
  literally and never has to invent. The plan specifies exact signatures, the
  algorithm in prose, edge cases, a concrete DONE_CONDITION, and an absolute
  WORKING_DIRECTORY — then plan_lint gates it before execution. Use when generating
  the up-front plan for a NEEDS_PLAN task; NOT for single-file edits or questions.
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, planning, plan-execute, contract, conductor, hermes-max]
    category: hermes-max
    related_skills: [workflow-plan-first, workflow-conductor, workflow-done-definition, workflow-execute-from-plan]
---

<!-- TRIGGERS WHEN: a NEEDS_PLAN task is about to be planned on the expensive planner (V4-Pro/synth) — write PLAN.md to this contract so the cheap executor never invents -->

# Plan contract — the incontrovertible brief the executor can transcribe

This is the **plan/execute split** (compress-then-reason, inverted): pay for the
expensive planner's judgment **once**, up front, as a brief so complete the cheap
local executor never faces a design decision. The local 35B can WRITE any code a
good plan specifies; it cannot reliably INVENT the rich structure. So the plan must
leave nothing to invent.

**Where this sits.** [[workflow-plan-first]] decides a task needs a plan and seeds
the definition-of-done; THIS skill is the stricter contract the **expensive
planner** (the `synth` role = DeepSeek V4-Pro) must meet when it actually writes
PLAN.md. The cloud mechanics are [[workflow-conductor]]; the executor side is
[[workflow-execute-from-plan]].

## When to use

Only for a **NEEDS_PLAN** task — `classify_plan_need` / `plan_route(phase="plan")`
returns `tier=synth`. That is: an action verb (Implement/Build/Write/Create/Design/
Refactor/Add) AND substantive scope (more than one file, more than a single
function, or it mentions tests). For a single-file edit, a lookup, or a question,
skip this — stay local.

## How the plan is generated (don't hand-write the brief)

1. `plan_route(task)` → confirms `phase=plan`, `tier=synth`, the V4-Pro `model_id`.
2. `brief_assemble(task_id, current_blocker, decision_needed, profile="full")` —
   you (the local model) write ONLY `current_blocker` + `decision_needed`; the
   assembler pulls goal/done/constraints from PLAN.md, the KG, and codebase-rag.
3. `conductor_synthesize(brief)` — V4-Pro returns the plan content.
4. Write it to **PLAN.md**, then **`plan_lint`** it (below) BEFORE any execution.

## The PLAN.md schema (what V4-Pro must produce)

For the whole task:

- **TASK** — one sentence restating what is being built.
- **WORKING_DIRECTORY** — the **absolute** path the executor operates in (resolved
  at plan time). `plan_lint` rejects a relative path.
- **FILES** — every file to create/modify, full relative path + one-line purpose.
- **DONE_CONDITION (Definition of Done)** — concrete and checkable (e.g. "verify
  green; property_test passes; 12+ tests; FPR within 10% of theoretical at
  capacity"). Use this exact header so the brief-assembler also surfaces it.
- **RISKS** — what could go wrong and how the executor detects it early.

And, for EACH file, a **FILE SPEC** (header `## FILE SPEC: <path>`):

- Every public class/function with its **exact signature** (name, typed params,
  return type).
- For each function, a **prose** description of the algorithm precise enough that
  writing the body needs no design decision — the formula, the data structure, the
  library call, the control flow. No ambiguity about HOW.
- The **edge cases** each function handles and the **exact error type/message** to
  raise.
- For test files, the **test cases by name** with the property each one checks.

The plan specifies **WHAT and HOW-in-prose** — it does **not** contain the actual
implementation code. If the planner writes the whole file body, you're paying
steer-tier prices for execution; that's the wrong split.

## The completeness gate (plan_lint) and revision

- Run **`plan_lint(repo=…)`** (or `plan_text=…`) on the generated PLAN.md. It
  deterministically checks: WORKING_DIRECTORY is absolute; a FILE SPEC exists for
  every listed file; each FILE SPEC has a signature **and** prose; DONE_CONDITION is
  concrete. It returns `{complete, missing:[...]}`.
- If `complete` is false, the plan goes **BACK to the planner (synth)** with the
  specific `missing` gaps — not forward to the executor. Re-synthesize, re-lint.
  Pass `revision_round` (increment each round); after `PLAN_LINT_MAX_ROUNDS` the
  result is `bounded`/`proceed_flagged` — proceed with a flagged-incomplete plan
  rather than looping forever.
- `plan_lint` validates the PLAN.md **document**. It is distinct from
  `directive_verify`, which gates a JSON directive against repo state — don't call
  `directive_verify` on a PLAN.md.

## The principle

The executor must never face a design decision the planner didn't already make.
Every "how should I do X" must be answered in the plan. If the executor has to
invent an approach, the plan failed — that's a **planner-quality** bug, fix the
plan (tighten this contract), not the executor.
