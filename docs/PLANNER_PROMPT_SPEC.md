# PLANNER_PROMPT_SPEC.md

The source of truth for deterministic plan generation. Implemented in
`mcp-escalation/conductor_core.py` and enforced by `lint_plan()` so compliance does not
depend on which model rung answered. The worked example in §6 is a **data instance** (one
hard problem from the open-ended set), never architecture to hardcode — the schema and
prompts are fully domain-general.

## §1 — Plan schema (`PLAN_SCHEMA_SECTIONS`)

A PLAN.md MUST contain these section TYPES, in order (never problem types):

- **CONTEXT** — one paragraph: goal + binding constraints.
- **ARCHITECTURE DECISIONS** — numbered; every non-trivial choice COMMITTED to one named
  mechanism, each ending `BECAUSE <why over the main alternative>`.
- **STEPS** — ordered, atomic; each step is exactly: `DO:` (exact file+function),
  `DONE-WHEN:` (exact command + expected exit/output — mechanical binary check),
  `LIKELY-FAILURE:` + `PREEMPT:`. Frontier steps marked `complexity: HIGH`; multi-file
  steps append `files:` and `depends_on:`.
- **VERIFICATION** — the exact commands proving the whole task done (lint+types+tests).
- **REFERENCES** — the algorithm/paper/known implementation each decision follows, or `none`;
  reference nothing the executor hasn't been shown.

## §2 — Conductor system prompt (`_plan_system`)

Load-bearing rules only (no rationale): commit-don't-offer; banned phrases
(`consider / you could / depending on / either / tests pass / works correctly` and an
unresolved `A or B`) outside a BECAUSE clause; mechanical DONE-WHEN; anticipate-the-
executor's-failure (LIKELY-FAILURE+PREEMPT per step); atomic ordered steps; pin everything
(signatures, versions, paths); no unseen references. Frontier tasks additionally must
commit every invariant with a verbatim reference.

## §3 — User template (`_plan_user`)

XML-tagged, long stable context first, instruction last:
`<repo_context>` (repomap+SKILLS.md) · `<research_context>` (deep-research output or none) ·
`<retry_context>` (escalation summary on a replan, else none) · `<task>` · `<instruction>`.

## §4 — Frontier classifier (`classify_task`)

Keyword SIGNAL CLASSES (not a problem allowlist): concurrency · systems/low-level ·
algorithmic-novelty · crypto/numeric · distributed/protocol, plus a structural `>3 files`
signal. Any class firing → frontier (mandatory embedded invariants, larger synth budget).
None firing and ≤2 files → lightweight. Ambiguity resolves toward frontier (conductor
tokens are cheap; an executor spiral is not).

## §5 — Escalation / replanning (`conductor_escalate`)

Mid-run callback for a step that repeatedly fails its DONE-WHEN. Strict three-field output:
`DIAGNOSIS` (one-sentence root cause), `DECISION ∈ {patch-step, pivot-approach,
abort-and-resummarize}`, `PATCH` (minimal — the failing step or the one changed decision).
Strict-parsed; re-asked once on mismatch; falls back to abort-and-resummarize. Triggered by
the verify/tool-failure path INSTEAD of blind retry.

## §6 — Worked example (regression fixture — structure only)

Task: *"Implement a lock-free bounded MPMC queue in Python using ctypes atomics on
mmap-backed shared memory."* The fixture (`smoke_planner.py`) asserts **structural**
properties — all §1 sections present, every step has DONE-WHEN, LIKELY-FAILURE on frontier
steps, `lint_plan` returns no violations — **never** exact MPMC content. It proves the
schema yields a transcribable plan on a known-hard instance; it does not bake the instance in.

## §7 — Anti-patterns (`lint_plan`, AP1–AP7)

AP1 `consider` · AP2 `you could` · AP3 `depending on` · AP4 `either` · AP5 `tests pass`
· AP6 `works correctly` · AP7 unresolved `A or B` fork — all rejected outside a BECAUSE
justification clause, plus: a missing required section, and a STEP with no DONE-WHEN.

## §8 — Token budget

Synth thinking budget 8192; output cap 4096 (routine) / 6144 (frontier). One lint-retry max
(a second full generation only on violations). Escalation cap 2048 output, re-asked once.
