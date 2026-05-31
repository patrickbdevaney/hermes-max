---
name: workflow-effort-routing
description: Concentrate deep reasoning where it pays (planning, hard debugging) and stay terse on mechanical turns — caps spirals, saves tokens.
trigger: at the start of every turn — decide how much to think before acting
---

<!-- TRIGGERS WHEN: Concentrate deep reasoning where it pays (planning, hard debugging) and stay terse on mechanical turns — caps spirals, saves tokens. -->
# Spend reasoning where it counts. High effort on EXECUTION turns is what caused the spiral.

The global default reasoning effort is **MEDIUM** (lowered from high by `apply-config-deadlines.sh`).
Medium is the right baseline: high effort on mechanical execution turns burned tokens and looped;
low effort on planning produced shallow plans. Route effort to the work:

## HIGH effort (think hard, deliberately)
- Planning / architecture / decomposition (`workflow-plan-first`, `workflow-plan`).
- Hard debugging: a non-obvious failure, a subtask flagged HARD by the difficulty signal, or the
  second attempt after a stuck-reset.
- Designing an interface or data model with downstream consequences.

## LOW / minimal effort (≤3-4 sentences, then act — see `workflow-deadline-discipline`)
- Reads, searches, retrieval (`search_code`, `retrieve_related`, file reads).
- Mechanical edits: applying a planned diff, renaming, formatting, obvious fixes.
- Tool routing / running the verifier / making a checkpoint.

## How to apply
- If Hermes exposes a per-request reasoning effort (per-call override), set HIGH on the turns above
  and LOW on mechanical turns. If it does not, apply it behaviorally: on a HIGH turn, reason
  carefully and lay out the plan; on a LOW turn, do NOT write a long reasoning block — state the one
  next action in a sentence or two and execute.
- Raise to HIGH only for planning and flagged-hard subtasks; never for routine execution. This both
  caps spirals (less unbounded thinking on execution) and concentrates the model's reasoning budget
  where it changes outcomes.
- Pair with the spiral check: if a HIGH-effort turn starts to loop, `check_spiral` and break out
  (`workflow-stuck-detect-reset`) — more thinking is not the fix once it's circular.
