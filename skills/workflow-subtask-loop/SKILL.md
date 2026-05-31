---
name: workflow-subtask-loop
description: How to execute ONE subtask. Apply to every subtask in the plan.
trigger: executing a planned subtask
---

<!-- TRIGGERS WHEN: How to execute ONE subtask. -->
# One subtask at a time. Bounded, verified, committed.

For the CURRENT subtask only (ignore the rest of the project for now):
1. Re-read the current subtask from PLAN.md and its definition of done.
2. Make the minimal change that satisfies just this subtask. Do not scope-creep into other
   subtasks — that pollutes context and causes drift.
3. Run mcp-verify on the affected files (`mcp_hermes_max_verify_verify`). If RED: fix and
   re-run. Max 3 fix attempts on the same error — if still red, invoke
   workflow-stuck-detect-reset. Do NOT thrash.
4. When GREEN: record what changed to knowledge-graph
   (`mcp_hermes_max_knowledge_graph_record_entity` / `mcp_hermes_max_knowledge_graph_record_relation`:
   what file, what decision, why). This is how the next subtask and the next session benefit.
5. Checkpoint: call `mcp_hermes_max_checkpoint_checkpoint(label="<subtask label>")`. This
   creates a verified-green commit (it asks mcp-verify first and refuses on red), and it is the
   rollback point — if a later subtask drifts, we revert to here, not to zero.
6. Mark the subtask done in PLAN.md. Move to the next subtask with a FRESH focus — you do not
   need the details of the completed subtask in active attention anymore.

**Pick the next subtask by PLAN.md dependency order — finish what you started.** The build/coding
loop does NOT use the Banyan UCB1 bandit (`banyan_select`): UCB1 is a stationary explorer scoped to
the RESEARCH loop only (`BANYAN_SCOPE=research_only`, the default), and its exploration term would
pull you off a half-finished hard subsystem toward a shinier easy one. Keep an in-progress
incomplete subtask until it's green and checkpointed before switching; if you need a programmatic
pick, use `build_select_subtask` (finish-what-you-started / dependency-order), never `banyan_select`.
