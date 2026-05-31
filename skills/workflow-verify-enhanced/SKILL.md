---
name: workflow-verify-enhanced
description: >-
  After unit tests pass on a non-trivial module, use property_test on the core
  logic functions to find edge cases the examples missed, and mutation_test on
  changed files to confirm the tests actually CATCH bugs (not just run). A surviving
  mutant means a test gap — add a test that kills it before declaring done. Trigger
  on core-logic / algorithmic / parsing / numeric code, not trivial glue edits.
---

# workflow-verify-enhanced

<!-- TRIGGERS WHEN: unit tests pass on non-trivial logic and you're about to declare done -->

Verification accuracy (~87%) far exceeds generation accuracy (~63%), so spending
model budget on STRONGER tests pays off. Two gate extensions in mcp-verify:

- **`property_test(path)`** — the local model generates 3–5 Hypothesis `@given`
  property tests (falsifiable invariants: round-trips, bounds, ordering, agreement
  with a slow reference), runs them, and returns minimal **counterexamples**.
  Hallucinated properties (those that fail to import/collect) are filtered out.
  PGS lifted fix rate 23.1%→53.8% over a TDD baseline (arXiv:2506.18315); Anthropic's
  agentic PBT found real bugs in NumPy/HuggingFace (arXiv:2510.09907).
- **`mutation_test(path, test_path)`** — mutmut mutates the module and runs your
  tests against each mutant. A **surviving mutant** is a test that fails to catch a
  real bug — a gap. Meta ACH: mutation beats coverage (arXiv:2501.12862).

## How to use

1. Get unit tests green first (`verify`).
2. `property_test` the core logic functions — if it returns a counterexample, your
   function (or your understanding of its contract) is wrong; fix it.
3. `mutation_test` the changed files — for each surviving mutant, add a test that
   kills it, then re-run. Only declare done when no mutant survives.

Both are time-bounded and **opt-in** for the primary gate (`ENABLE_PROPERTY_TEST=true`)
because property generation adds wall time and the model can hallucinate properties —
treat a generated property's *failure* as a hypothesis to investigate, not a verdict.
