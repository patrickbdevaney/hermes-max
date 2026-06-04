---
name: workflow-task-finish
description: "Never declare done on red: verify, then record what you learned to the knowledge graph."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, verification, knowledge-graph, gate, hermes-max]
    category: hermes-max
    related_skills: [workflow-task-start, workflow-stuck]
---

<!-- TRIGGERS WHEN: "Never declare done on red: verify, then record what you learned to the knowledge graph." version: 1.0.0 author: Hermes Max license: MIT platforms: [linux, maco -->

# Task Finish — the verification gate + knowledge capture

Run this **before reporting any coding task complete**. You may not declare
"done" while verification is red.

## Steps

1. **Verify (hard gate).** Call `verify(path)` (mcp-verify) on the code you
   changed. It runs lint → typecheck → unit tests.
   - If `passed` is **false**, read the per-stage diagnostics, fix the cause,
     and re-run `verify`. Repeat until green. Do **not** report done on red.
   - If the same failure persists after a few honest attempts, switch to the
     `workflow-stuck` skill instead of thrashing.
   - If `verify` is unavailable (server down), say so explicitly and fall back
     to running the project's lint/type/test commands yourself — but still do
     not claim success unattended without some green signal.
2. **Record what you learned** to mcp-knowledge-graph so the next session starts
   ahead:
   - `record_entity` for any new decision (`type="decision"`), bug
     (`type="bug"`), or component (`type="file"|"service"`) — include a short
     `why` in props.
   - `record_relation` to link them, e.g. `(decision)-[applies_to]->(file)`,
     `(bug)-[fixed_in]->(commit)`, `(service)-[depends_on]->(service)`.
3. **Let a skill distill.** If the task involved a novel, reusable technique,
   allow Hermes's skill-creation loop to capture it (don't force it; the nudge
   will prompt you when warranted).

## Verification — run it, show it, verdict it

Reading code is not verifying it, and an implementer model's own claim of "green" is not
evidence. For every criterion in the plan's VERIFICATION section, and every step's
`DONE-WHEN`, EITHER call `run_done_when(command, expected_output | expected_exit_code)`
(mcp-verify) — it executes the command in the verify sandbox and returns a verdict — OR run
the command yourself and record exactly:

- **Check:** what you are confirming
- **Command:** the exact command you ran
- **Observed:** the real terminal output, pasted verbatim (never summarized)
- **Result:** PASS, or FAIL with expected-vs-actual

Close each check with one literal line, exactly one of: `VERDICT: PASS`, `VERDICT: FAIL`,
`VERDICT: PARTIAL`. The harness reads that line; a FAIL/PARTIAL — or a missing verdict —
is not done and routes to retry/escalation.

Do not let yourself off the hook. "It looks right", "this is the standard way", and "the tests
the implementer wrote already pass" are not verification — execute the check or call it a skip.
Report honestly: a `VERDICT: PARTIAL` that names the unverified step is correct; a `VERDICT:
PASS` you did not actually observe is a failed task. For core logic a green exit is necessary
but not sufficient — also run `verify_formal` / `property_test` on the target.

## Git safety (if you commit)

- Don't run history-destroying git commands (force-push, hard reset, `checkout .` / `clean` /
  branch deletion) unless the operator asked for that exact command.
- Create a fresh commit each time; don't `--amend` — a failed pre-commit hook means nothing was
  committed, so amending would overwrite the PREVIOUS (good) commit.
- Stage files explicitly by path, not `-A` / `.`, so unrelated edits never ride along.
- Don't force-push a branch others share.

## Why

A gate that *cannot* be bypassed on broken code makes unattended operation
trustworthy — reliability you can leave alone overnight. Recording decisions is
what makes the *second* related task faster than the first.
