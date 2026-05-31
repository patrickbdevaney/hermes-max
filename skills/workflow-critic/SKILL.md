---
name: workflow-critic
description: After a hard subtask goes green, run a bounded red-team review of the diff against the spec and tests to catch silent-wrong patches.
trigger: a non-trivial/hard subtask just verified green, before moving on
---

<!-- TRIGGERS WHEN: After a hard subtask goes green, run a bounded red-team review of the diff against the spec and tests to catch silent-wrong patches. -->
# Green isn't always correct. ~20% of patches pass shallow tests but are semantically wrong.

A passing test suite proves the tests pass — not that the code is right. For non-trivial subtasks,
run ONE bounded critic pass before you move on, to catch the silent-wrong-answer class.

Gate by difficulty: skip the critic on trivial/easy changes (it's not worth the your inference host inference);
run it on subtasks the difficulty signal flags MEDIUM/HARD, or anything irreversible/high-stakes.

## The pass (builder → validator, bounded)
1. The subtask must already be GREEN (`verify` passed + checkpointed) before critiquing.
2. Spawn ONE isolated reviewer sub-agent via Hermes delegation (review is reversible —
   `subagent_auto_approve` covers it). Give it: the diff, the spec/DEFINITION OF DONE, and the
   tests. Its job is to RED-TEAM, not to edit. Ask it specifically:
   - Does the diff actually satisfy the spec, or just the tests?
   - Are the tests meaningful, or do they pass trivially / miss the real behavior and edge cases?
   - What input would break this? (off-by-one, empty/None, concurrency, error paths)
   - Any silent-wrong behavior the green gate wouldn't catch?
3. Ground the critique in EXECUTION, not opinion: prefer
   `mcp_hermes_max_verify_deep_verify(path, difficulty="hard")` (property + mutation) over the
   reviewer's say-so. Surviving mutants / failing property tests are concrete, trustworthy signals.
4. If the critic finds a REAL defect: the builder fixes it and re-verifies. Bound this to 1–2
   rounds — if it's still wrong after that, it's a stuck signal (`workflow-stuck-detect-reset`),
   not an infinite critic loop. Re-checkpoint once green again.
5. Record a `critic_rejected` event to observability when the critic rejects a patch.

## Bounds (your inference host)
ONE critic pass, ONE reviewer, ≤2 fix rounds. This is review-only — NOT a swarm, and NEVER on the
edit path (keep editing single-threaded; see `workflow-subagent-isolation`). Samples compete for the
one GPU.

## Optional fast monitor model
If `MONITOR_ENABLED=true` and `MONITOR_BASE_URL` are configured (a small LFM2.5-class model on a
second endpoint), the reviewer delegate MAY run there to keep the critic cheap. Default OFF — two
models share the one memory bus, so measure your inference host contention before adopting it.

## Graceful degradation
If delegation is unavailable, fall back to a self-review checklist (the 4 questions above) plus
`deep_verify` — the execution-grounded layer is the part that actually catches silent-wrong code.
