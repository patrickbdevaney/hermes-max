---
name: workflow-quality-bar
description: >-
  Software implementations must meet senior-engineer review standards: complete type
  annotations, docstrings on public classes/methods, explicit error handling with
  informative messages, no placeholder comments or TODOs in committed code, and tests
  covering edge cases — not just the happy path. Applies to the planner (the plan must
  SPECIFY these) and the executor (the code must HAVE them). quality_check surfaces the
  gaps as advisory warnings; it never replaces the hard verify gate.
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, quality, senior-review, plan-execute, advisory, hermes-max]
    category: hermes-max
    related_skills: [workflow-verify-enhanced, workflow-plan-contract, workflow-execute-from-plan, workflow-done-definition]
---

<!-- TRIGGERS WHEN: about to declare a Python implementation done, or writing the plan contract — raise the output to senior-review texture (advisory, not a gate) -->

# Quality bar — senior-review texture, advisory

The deterministic gate (lint + types + tests, [[workflow-verify-enhanced]]) proves
code is *correct*. It does not prove it is *senior-grade*. A finished implementation
has no gap a senior reviewer would flag:

- complete type annotations on public functions/methods;
- docstrings on all public classes and methods;
- explicit error handling with informative messages (no silent `except:`);
- no placeholder comments, no `TODO`/`FIXME` left in committed code;
- tests covering edge cases, not just the happy path.

## Applies to both phases of the plan/execute split

- **Planner** ([[workflow-plan-contract]]): the PLAN.md FILE SPECs must *specify*
  these — exact typed signatures, the error type/message per edge case, the
  edge-case test names. If the plan demands them, the executor produces them.
- **Executor** ([[workflow-execute-from-plan]]): the code must *have* them. Before
  declaring a file done, run the advisory check below and close what it flags.

## The advisory check (quality_check)

Run `mcp_hermes_max_verify_quality_check(path)` on a Python file you implemented.
It flags, without failing the build:

- `annotations_missing` — params/return with no type annotation;
- `docstrings_missing` — public functions/methods with no docstring (dunders exempt);
- `placeholders` — TODO/FIXME/placeholder/stub markers;
- `bare_excepts` — `except:` with no type (swallows everything).

`clean: true` means none of the above. Treat each finding as a texture gap to close,
not a verdict.

## The line that must not blur

`quality_check` is **advisory** — it raises texture, it does **not** gate. The hard
pass/fail stays `verify()` / `deep_verify()` ([[workflow-verify-enhanced]]) and the
DONE_CONDITION ([[workflow-done-definition]]). Never block a green build on a style
warning; never declare done while a senior reviewer would still flag the code.
