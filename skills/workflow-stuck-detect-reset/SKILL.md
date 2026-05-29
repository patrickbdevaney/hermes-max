---
name: workflow-stuck-detect-reset
description: Detect being stuck and recover with a clean-context reset instead of thrashing.
trigger: same error 3x, no progress for several turns, or confusion about current state
---
# When stuck, do NOT thrash in a polluted context. Summarize, reset, retry with fresh eyes.

Small-model failures are usually CONTEXT-POLLUTION failures: the context has filled with failed
attempts, contradictory state, and dead ends, and the model can no longer find the thread. More
turns in that context make it worse. The fix is a clean reset, not more attempts.

STUCK is: same error 3 times, OR 5+ turns with no verifiable progress, OR you cannot clearly
state what the current state of the code is.

When stuck:
1. STOP. Do not make another edit.
2. Write a STUCK SUMMARY: (a) the goal, (b) what is verifiably TRUE right now (what works, what
   files exist, last green checkpoint), (c) exactly what is failing and the precise error, (d)
   the 2-3 approaches already tried that did NOT work.
3. Revert to the last green git checkpoint by calling
   `mcp_hermes_max_checkpoint_revert_to_last_green()` so the code is in a known-good state, not a
   half-broken one. (It stashes any dirty tree first, so nothing is lost.)
4. RESET CONTEXT: start a fresh attempt with ONLY the STUCK SUMMARY + the reverted-clean code +
   PLAN.md. Drop all the failed-attempt history — it's noise now.
5. Try a DIFFERENT approach than the ones in the summary. If no different approach is obvious,
   or the same wall is hit again after reset → ESCALATE: ping the human (Telegram) with the
   STUCK SUMMARY and the specific blocker, and wait. Do not keep grinding. This is the
   "loop overnight then ping me" contract — a clean stuck-ping beats hours of thrashing.
   (A genuinely-hard but well-scoped subproblem may instead go to `mcp_hermes_max_escalation_escalate`
   if escalation is enabled; a missing DECISION always goes to the human.)
