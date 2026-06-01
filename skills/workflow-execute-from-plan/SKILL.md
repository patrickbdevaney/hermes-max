---
name: workflow-execute-from-plan
description: >-
  When a PLAN.md written to the plan contract exists, implement it LITERALLY — do
  not make design decisions. First confirm the working directory, implement files in
  the plan's order, verify each one, and finish only when the DONE_CONDITION is
  literally satisfied. If you hit a decision the plan does not answer, call
  request_plan_revision (which asks the expensive planner) rather than inventing.
  Always-on during the EXECUTE phase of a plan/execute task.
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, execution, plan-execute, no-invention, conductor, hermes-max]
    category: hermes-max
    related_skills: [workflow-plan-contract, workflow-conductor, workflow-verify-enhanced, workflow-done-definition, workflow-stuck]
---

<!-- TRIGGERS WHEN: a PLAN.md (written to the plan contract) exists and you are in the EXECUTE phase — implement it literally, escalate any gap rather than inventing -->

# Execute from the plan — implement literally, never invent

A complete PLAN.md (produced on the expensive planner per [[workflow-plan-contract]])
already made every design decision. Your job is **transcription + local correctness
loops**, not design. The plan leaves nothing to invent, so don't.

## The discipline (in order)

1. **Confirm the working directory FIRST.** `pwd`; if it is not the plan's
   `WORKING_DIRECTORY` (an absolute path), `cd` there and confirm. Directory drift
   is the #1 executor failure — kill it before writing a line.
2. **Implement files in the plan's order, one at a time.** For each file, follow its
   FILE SPEC: the exact signatures, the prose algorithm, the edge cases, the error
   types. Write the body the plan describes — do not redesign it.
3. **Verify each file as you finish it.** Run the verify gate
   (`mcp_hermes_max_verify_verify`); on core logic use [[workflow-verify-enhanced]]
   (`property_test` / `metamorphic_test`) as the DONE_CONDITION specifies.
4. **Checkpoint after each green file** (per [[workflow-task-finish]]).
5. **Finish only when the DONE_CONDITION is LITERALLY satisfied.** Re-read it and
   check each clause (see [[workflow-done-definition]]). "Done" is the plan's
   condition met and verified — never your opinion.

## The iterative repair loop (closing the gap to Opus, cheapest rung first)

A gap-free V4-Pro plan closes ~80% of the Opus-vs-local quality gap on planned
tasks. The rest is closed **iteratively** by a verify-driven repair ladder that
spends the cheapest rung that can fix the failure — never a full regen, never Opus
prices on every token. For each file, in the plan's order:

1. **Execute from the plan** (`code_execute` role — local/V4-Flash, the workhorse).
2. **`quick_check` immediately after each edit** (lint + typecheck, ~1s).
   - fails → `lsp_diagnostics` for the exact symbol/error → one targeted
     **`code_repair`** call with that diagnostic (not a regen).
   - still failing after **2** repairs → `request_plan_revision` (V4-Pro fills the
     gap; the executor was missing a decision, not a fix).
3. **`verify(file)`** once edits pass quick_check.
   - fails → run `property_test` / `metamorphic_test` to get the **minimal
     counterexample**, hand THAT to `code_repair` (targeted).
   - still failing after **2** repair attempts → escalate to **`code_steer`** (a
     fast Scout/V4-Flash directional nudge).
   - still failing after steer → route to **`code_plan`** for a *partial re-plan of
     the failing file only*.
4. **`quality_check`** (advisory: docstrings, annotations, no stray TODOs).
5. **Checkpoint on green.**
6. **DONE only when every DONE_CONDITION clause is literally met.**

**Rung spend order — cheapest first, most expensive last (let cost climb only as
failures persist):**

```
LSP repair (~$0)  →  code_repair / Groq-Scout (~$0)  →  code_steer / V4-Flash (~$0.001)
  →  code_plan re-plan (~$0.01)  →  code_frontier / Opus (~$0.08–1.25, triple-gated ONLY)
```

`code_frontier` (Opus) is reached only through the conductor's triple gate
(mode=frontier **and** synth-failed-twice **and** large blast-radius). Most tasks
never get near it. Budget at most one Opus call for a genuinely hard file when the
verify gate refuses to go green — it's still cheap relative to time spent.

> The loop is the same in every posture; only WHO answers each role changes
> (`hm mode`). In `free`/`local` the repair rungs degrade to the local model; in
> `full` they're V4-Flash/Scout; the *discipline* never changes.

## The "no invention" rule (the mechanism that closes the gap)

If you hit a point the plan is **silent** on — a missing signature, an unspecified
algorithm, an ambiguous edge case — **STOP. Do not guess.** Call:

```
request_plan_revision(question="<the precise gap>", repo="<WORKING_DIRECTORY>",
                      task_id="<id>", request_index=<n>)
```

It routes the specific question to the **expensive planner** (the `synth` role =
V4-Pro — the same model that wrote the plan), appends the answer under a
`## PLAN REVISION` header in PLAN.md, and you resume. This is the whole point: the
cheap model **asks** the expensive model instead of writing code it can't reliably
invent.

- Pass `request_index` and **increment it** each time (the tool is stateless). After
  `PLAN_REVISION_MAX` requests it returns `bounded` — then proceed best-effort with a
  flagged note rather than ping-ponging forever.
- If it returns `proceed_local` (the planner role is OFF or capped), do **not**
  invent — fall to [[workflow-stuck]] (write a STUCK SUMMARY, ping the operator).

A frequent need for revisions on a simple task means the **plan** was thin — that's a
planner-quality bug for [[workflow-plan-contract]] to fix, not a reason to start
guessing here.

## Don't

- Don't redesign what the FILE SPEC already specifies.
- Don't skip the working-directory check or the per-file verify.
- Don't declare done before re-reading and satisfying every DONE_CONDITION clause.
- Don't invent around a plan gap — `request_plan_revision`, or surface via
  [[workflow-stuck]].
