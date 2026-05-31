---
name: workflow-spec-driven
description: >-
  For a GENUINELY complex, multi-component task, produce durable versioned artifacts
  before implementing — SPEC.md (what + why), PLAN.md (how), TASKS.md (decomposed
  checklist) — then work through TASKS.md item by item. Use this ONLY for real
  multi-component work; it adds rigidity and overhead, so do NOT use it for small
  fixes or single-file changes.
---

# workflow-spec-driven

<!-- TRIGGERS WHEN: a task spans multiple components/files and benefits from an up-front spec; NOT for small fixes -->

GitHub Spec-Kit discipline: for complex work, write the contract down first so the
implementation has a stable target and the decomposition is explicit and reviewable.

**Honest framing (from the research):** this adds rigidity and up-front cost. Use it
for genuinely complex, multi-component tasks (a new subsystem, a cross-cutting change,
a multi-stage feature). For a small fix, a single-file change, or a mechanical edit,
**skip it** — go straight to the change (and at most [[workflow-plan]]).

## The artifacts (write before implementing)

Create these in the project (e.g. under `specs/<task>/`):

1. **`SPEC.md`** — *what* and *why*: the goal, the user-visible behavior, constraints,
   explicit non-goals, and acceptance criteria. No "how".
2. **`PLAN.md`** — *how*: the design, the components touched, the sequence, risks and
   trade-offs. (Pairs with [[workflow-plan]].)
3. **`TASKS.md`** — the decomposed, checkable checklist: each item is one independently
   verifiable change with its own done-condition (a test, a verify-green, a checkpoint).

## Working the spec

- Implement by walking **TASKS.md** top to bottom; check items off as each passes its
  done-condition (verify green + checkpoint per [[workflow-task-finish]]).
- When reality diverges from SPEC/PLAN, **update the artifact** — they're durable and
  versioned (commit them), not write-once. A drifted spec is worse than none.
- The artifacts are also the input to the regression eval and the trajectory store —
  a clear SPEC makes a failure localizable later.
