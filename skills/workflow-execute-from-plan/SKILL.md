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
