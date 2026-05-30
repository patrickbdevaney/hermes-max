---
name: workflow-stuck-detect-reset
description: Detect being stuck and recover with a clean-context reset instead of thrashing.
trigger: same error 3x, no progress for several turns, or confusion about current state
---
# When stuck, do NOT thrash in a polluted context. Summarize, reset, retry with fresh eyes.

Small-model failures are usually CONTEXT-POLLUTION failures: the context has filled with failed
attempts, contradictory state, and dead ends, and the model can no longer find the thread. More
turns in that context make it worse. The fix is a clean reset, not more attempts.

STUCK is detected TWO ways — use both:
- **Across turns (Hermes-native):** same error 3 times, 5+ turns with no verifiable progress, or
  you cannot clearly state the current code state. The native `tool_loop_guardrails` also trip here.
- **Within a turn (mcp-watchdog — the signals the turn-based guardrails can't see):**
  - `mcp_hermes_max_watchdog_check_spiral(recent_thinking_text=...)` → `spiral_detected` (reasoning loop)
  - `mcp_hermes_max_watchdog_check_stall(...)` → `hung` (a tool call silent past its budget)
  - `mcp_hermes_max_watchdog_check_progress(task_id, signals=...)` → `no_progress` (deltas all zero)
  - `mcp_hermes_max_watchdog_check_budget(task_id, ...)` → `budget_exceeded`
  Any of these firing means STUCK — act on it the same as a native trip. (If the watchdog is down,
  the calls just fail; fall back to the across-turns signals — graceful degradation.)

When stuck, climb the RECOVERY LADDER in order — only go to the next rung if the current one doesn't
unblock you. Don't skip to escalate, and don't thrash on one rung:
1. **STOP.** Do not make another edit. Write a STUCK SUMMARY: (a) the goal, (b) what is verifiably
   TRUE right now (what works, what files exist, last green checkpoint), (c) exactly what is failing
   and the precise error, (d) the 2-3 approaches already tried that did NOT work.
2. **incremental-verify** — re-run the verifier on the last-known-good scope to confirm what is
   actually green vs your assumption.
3. **checkpoint-revert** — `mcp_hermes_max_checkpoint_revert_to_last_green()` to land on known-good
   code (it stashes any dirty tree first, nothing is lost), then
   `mcp_hermes_max_checkpoint_restore_state(task_id)` to bring back the PLAN + decision notes (so the
   reset is lossless — you reset CONTEXT, not your understanding of the goal).
4. **replan** — RESET CONTEXT: fresh attempt with ONLY the STUCK SUMMARY + reverted-clean code +
   restored PLAN.md. Drop the failed-attempt history — it's noise now.
5. **alternative-approach** — try a DIFFERENT approach than the ones in the summary.
6. **decompose-further** — if the subtask is too big to land green, split it smaller and retry the
   smallest piece.
7. **fresh-subagent** — hand a clean, well-scoped restatement to an isolated sub-agent (no polluted
   context).
8. **escalate** — a genuinely-hard, well-scoped subproblem → `mcp_hermes_max_escalation_escalate`
   (if enabled); a missing DECISION or a repeated wall after reset → ping the human (Telegram) with
   the STUCK SUMMARY and wait. This is the "loop overnight then ping me" contract.
9. **abort-clean** — if even escalation can't proceed, stop on the last green checkpoint with the
   STUCK SUMMARY recorded. A clean stop beats a broken tree.
