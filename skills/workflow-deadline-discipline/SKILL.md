---
name: workflow-deadline-discipline
description: Bound every turn and every long operation; self-check with the watchdog so you never spiral, hang, or run over budget.
trigger: every execution turn, before/after any long-running operation, and every few subtasks
---
# Never get stuck INSIDE a turn. The turn-based guardrails don't see spirals or hangs.

Hermes' native `tool_loop_guardrails` only fire ACROSS turns. The two ways work dies are *inside
one turn*: a reasoning spiral (you think in circles and never emit) and a poll-hang (one tool call
blocks forever on a server that never exits). The `mcp-watchdog` server gives you deterministic,
model-free checks for exactly these. Use them — they are cheap and they don't call the model.

## Per-turn discipline
- Reason in **≤3-4 sentences, then ACT**. Execution turns are not for deep thinking — that is what
  caused the spiral. (High reasoning effort is reserved for planning; see `workflow-effort-routing`.)
- If a turn's reasoning is getting long or circular, call
  `mcp_hermes_max_watchdog_check_spiral(recent_thinking_text=<your last reasoning>)`. If
  `spiral_detected` is true: **STOP thinking**, write a STUCK SUMMARY, and go to the recovery ladder
  (`workflow-stuck-detect-reset`) — revert + replan. Do NOT push through with more reasoning.

## Long-running operations (the poll-hang fix)
- NEVER block on a process that may not return. Start it **backgrounded**, then call
  `mcp_hermes_max_watchdog_check_stall(tool_name, elapsed_s, expecting_heartbeat, last_heartbeat_age_s)`
  **once**.
  - `hung: true` → it is silent past its budget; kill it and try another approach.
  - `waiting: true` → it is heartbeating/serving; that is SUCCESS, leave it running, move on.
- Do this exactly once per process. Never poll-loop. (See `workflow-long-running-processes`.)

## Progress & budget (every few subtasks)
- Call `mcp_hermes_max_watchdog_check_progress(task_id, signals={files_touched, tests_passing,
  checkpoints, turn})` every few subtasks. If `no_progress` is true (deltas all zero over N), you
  are stalling — go to the recovery ladder instead of grinding.
- At task start, call `mcp_hermes_max_watchdog_start_task_budget(task_id, wall_clock_s, max_turns,
  usd_cap)`. Periodically `check_budget(task_id, turns_used, usd_spent)`. On `budget_exceeded`:
  checkpoint cleanly, write a STUCK SUMMARY, and STOP — do not run over the budget.

## On ANY watchdog flag (spiral / hung / no_progress / budget_exceeded)
1. Stop. Do not make another edit.
2. Snapshot then revert: `mcp_hermes_max_checkpoint_snapshot_state(task_id, ...)` is for the GOOD
   state; when stuck call `mcp_hermes_max_checkpoint_revert_to_last_green()` to land on known-good
   code, then `restore_state(task_id)` to bring the PLAN back.
3. Replan from the STUCK SUMMARY + clean tree (the recovery ladder in `workflow-stuck-detect-reset`).

## Graceful degradation
If the watchdog server is down, its calls fail — that is fine. Skip the self-check and fall back to
Hermes' native turn-based guardrails + the ≤3-4-sentence rule. Never let a watchdog outage block you.
