# CLAUDE_longhorizon.md — Long-Horizon Scaffolding Build Spec
## Add to the existing hermes-max repo. Makes the 35B-A3B complete full projects reliably.

You are extending the already-built `hermes-max/` repo. Everything here ADDS to it; do not
rebuild or modify the five existing MCP servers (verify, codebase-rag, knowledge-graph,
observability, escalation) or their boundaries. Read this whole spec before building.

### THE GOAL (why this exists)
A 35B-A3B model (~3B active params) is good at bounded single steps but cannot sustain
executive function — planning, self-monitoring, error recovery — across a long horizon. We
externalize that executive function into the harness so the model only ever does one small,
verified, checkpointed step at a time. The proof case: it recently hung 9 minutes polling a
backgrounded web server (which never terminates) with no internal monitor to notice. The
scaffolding below prevents, detects, and recovers from that entire failure class.

### THE SAME DESIGN DISCIPLINE AS THE REST OF hermes-max
- Skills are markdown (behavior, zero added instability).
- The ONE new capability (verified-green checkpointing) is an independent MCP server with a
  clean boundary, its own healthcheck and standalone smoke test, killable without crashing Hermes.
- Single-env-var port story preserved: nothing hardcodes a host; prod is one `$VLLM_BASE_URL` swap.
- Anti-Frankenstein gate: killing `mcp-checkpoint` mid-task must degrade gracefully (agent can
  still work, just without checkpoint/revert) — never crash.

---

## PART A — BUILD: `mcp-checkpoint` (git-commit checkpointing as a clean MCP server)

The user chose git commits over Hermes native snapshots: clean rollback, durable, readable
history, and the stuck-reset's "revert to last green" maps exactly to `git reset` to the last
verified commit. Wrap git in a thin MCP server so checkpoint/revert are first-class tools the
skills call, AND so a checkpoint is only ever created from a verified-green state.

Repo location: `hermes-max/mcp-checkpoint/` (same structure as the other servers: own venv,
streamable-http on 127.0.0.1, `/health`, healthcheck.sh, standalone smoke test). Port: **9106**.

### Tools to expose
- `checkpoint(label: str, verify: bool = True)` →
  - If `verify=True`, FIRST call mcp-verify on the repo (or accept a recent-green token); if
    verify is RED, REFUSE to checkpoint and return the diagnostics. A checkpoint MUST represent
    a green state — that invariant is the whole point ("revert to last green" must land on green).
  - `git add -A` then `git commit -m "[hermes-max checkpoint] <label>"`; tag the commit ref.
  - Returns the commit SHA + label. Idempotent if nothing changed (no-op commit → returns last SHA).
- `revert_to_last_green()` →
  - `git stash` any dirty working tree (so nothing is lost), then `git reset --hard <last
    checkpoint SHA>`. Returns the SHA reverted to and what was stashed.
  - This is the stuck-reset recovery primitive. It must put the tree in a known-good state.
- `list_checkpoints(n: int = 10)` → recent `[hermes-max checkpoint]` commits with SHA, label, time.
- `checkpoint_status()` → current branch, dirty/clean, SHA of last green checkpoint, commits-ahead.

### Implementation discipline
- Operate on the repo at the agent's current working directory (the project under work), NOT on
  the hermes-max repo itself. Take an explicit `repo_path` (default: cwd). Refuse to operate
  outside a git repo; offer to `git init` only if the caller passes `init=True`.
- NEVER force-push, never touch remotes, never operate on `~` or `/`. Guard against running
  outside a project dir. These are local working-tree checkpoints only.
- If `mcp-verify` is unreachable, `checkpoint(verify=True)` degrades to `verify=False` with a
  loud warning in the return value (graceful degradation, not a crash).
- Standalone smoke test: in a temp git repo, checkpoint a green state, make a breaking change,
  revert_to_last_green, assert the tree matches the checkpoint; assert checkpoint(verify=True)
  refuses on red.

### Register it
Extend `scripts/register-mcp.sh` to add `hermes-max-checkpoint -> http://127.0.0.1:9106/mcp`
alongside the existing five. Add it to `start-all.sh`, `healthcheck.sh`, `smoke-test.sh`, and the
`.env.example` (`MCP_CHECKPOINT_PORT=9106`).

---

## PART B — WRITE: the seven long-horizon skills (markdown → hermes-max/skills/)

Create these exactly. They are the externalized executive function. Keep them tight — the model
reads them, so verbosity costs context. (Full text is provided; transcribe faithfully, adjusting
only tool names to match the registered MCP tool names, e.g. `mcp_hermes_max_verify_verify`,
`mcp_hermes_max_checkpoint_checkpoint`, etc.)

1. `skills/workflow-plan-first.md` — restate goal + testable definition-of-done; query
   codebase-rag + knowledge-graph for existing code BEFORE planning; decompose into smallest
   independently-verifiable subtasks; PRE-MORTEM each subtask for hangs/errors (lookahead);
   write the plan to PLAN.md in the project; re-read PLAN.md each subtask. No code until PLAN.md exists.

2. `skills/workflow-subtask-loop.md` — execute ONE subtask only; make the minimal change; run
   mcp-verify (must be green, max 3 fix attempts then stuck-reset); record decision to
   knowledge-graph; call `mcp-checkpoint.checkpoint("<subtask label>")` (verified-green commit);
   mark subtask done in PLAN.md; move on with fresh focus.

3. `skills/workflow-long-running-processes.md` — a running server is SUCCESS not a hang; start
   backgrounded + capture PID; NEVER poll a process that never ends; wait 2-3s then test ONCE
   with a timeout (`curl -m 5` / `ss -ltn`); >10s on a forever-process is wrong; record PID to stop later.

4. `skills/skill-process-gotchas.md` — the externalized world-knowledge list: long-running
   processes, interactive prompts (use -y/--no-input), wrong cwd, inactive venv, port-in-use,
   tests needing a running server, silent-success commands (check $?), slow-not-hung builds,
   infinite-retry ban (no 3rd identical attempt).

5. `skills/workflow-stuck-detect-reset.md` — STUCK = same error 3x / 5+ turns no progress /
   can't state current state. On stuck: STOP; write STUCK SUMMARY (goal, what's verifiably true,
   exact failure, approaches already tried); `mcp-checkpoint.revert_to_last_green()`; RESET to a
   clean context with only the summary + reverted code + PLAN.md (drop failed-attempt history);
   try a DIFFERENT approach; if still stuck → ping human (Telegram) with the summary and wait.

6. `skills/workflow-done-definition.md` — done = mcp-verify green AND every definition-of-done
   item met AND any runtime was actually run once AND key decisions recorded to knowledge-graph.
   The model's opinion that it's done does not count.

7. `skills/workflow-context-hygiene.md` — PLAN.md is source of truth (re-read each subtask; if
   memory disagrees, PLAN.md wins); keep in focus only current subtask + code-being-changed +
   retrieved snippets + rules; don't hold whole files in context (retrieve with codebase-rag);
   after a finished subtask let its details compress — the durable record is the git checkpoint +
   knowledge-graph entry.

(The complete, ready-to-paste text of all seven skills is in long-horizon-scaffolding.md PART 1.
Use it verbatim, wiring the actual registered tool names and adding the checkpoint tool calls in
skills 2 and 5 as noted above.)

---

## PART C — CONFIG GUARDS (apply to ~/.hermes/config.yaml; the harness-level enforcement)

These make the harness catch the thrash/hang classes the skills are designed around. The key
insight from the poll-forever hang: a stalled poll reads as "in progress," not "failure," so the
binding guard is `idempotent_no_progress`, tightened low.

```yaml
agent:
  max_turns: 200
  reasoning_effort: high
  tool_use_enforcement: required

tool_loop_guardrails:
  warnings_enabled: true
  hard_stop_enabled: true          # CRITICAL — was false; this is what stops the 9-minute hang
  warn_after:
    exact_failure: 2
    same_tool_failure: 2
    idempotent_no_progress: 2
  hard_stop_after:
    exact_failure: 4
    same_tool_failure: 4           # was 8 — stop thrashing sooner, hand to stuck-reset
    idempotent_no_progress: 3      # was 5 — a stalled poll trips this fast

compression:
  threshold: 0.75
  target_ratio: 0.35
  protect_last_n: 40
  protect_first_n: 5               # protect the kickoff/plan instructions from compression

checkpoints:
  enabled: true                    # Hermes native snapshots as a backstop; git is the primary
  min_interval_hours: 1

kanban:
  auto_decompose: true             # native decomposition supports plan-first

skills:
  guard_agent_created: true
```

---

## PART D — CRITICAL PREREQUISITE (document in README, do not skip)

Long-horizon work requires the full context window. The vLLM serve script's `production` mode
serves only 65536 tokens (the header docstring claiming 262k is stale; the code sets
MAX_LEN=65536). For long projects, the inference server must be launched in **longctx** mode:
```bash
./serve-qwen36-production.sh longctx     # MAX_LEN=262144
curl -s http://localhost:8001/v1/models | python3 -m json.tool   # confirm max_model_len: 262144
```
Hermes then auto-detects 262K — do NOT pin context_length in Hermes; let it read the live
endpoint. Add a one-line check to `healthcheck.sh`: warn if the served `max_model_len` < 200000,
since long-horizon skills assume the big window.

---

## BUILD ORDER
1. Build `mcp-checkpoint` (git wrapper); standalone smoke test (checkpoint green / refuse red /
   revert-to-last-green restores tree).
2. Register it; update start-all/healthcheck/smoke-test/.env.example.
3. Write the seven skills into hermes-max/skills/ (verbatim from long-horizon-scaffolding.md PART
   1, wired to real tool names + checkpoint calls).
4. Apply the Part C config guards to ~/.hermes/config.yaml (back up first).
5. Add the Part D max_model_len check to healthcheck.sh and a README section.
6. Integration test (below).

## INTEGRATION / ACCEPTANCE TEST (the bar for done)
Re-run the same task that hung before, but as a planned project:
"Write and deploy a small Flask jokes API with a /health route and one /joke route, with a test."
Confirm:
- [ ] PLAN.md was written before any code (plan-first).
- [ ] Each subtask ended with a verified-green `mcp-checkpoint` commit (`git log` shows
      `[hermes-max checkpoint]` commits).
- [ ] The Flask server was started backgrounded and tested ONCE with curl — NOT polled to death.
      (This is the regression test for the original hang.)
- [ ] mcp-verify green before "done"; definition-of-done checklist met.
- [ ] Kill `mcp-checkpoint` mid-task → agent keeps working, just warns it can't checkpoint
      (graceful degradation).
- [ ] Force a stuck state (e.g. an unsatisfiable subtask) → agent writes a STUCK SUMMARY, reverts
      to last green, and pings rather than thrashing past hard_stop.
- [ ] Runs identically on laptop (Tailscale) and your inference host (localhost) with only `$VLLM_BASE_URL` changed.

## DEFINITION OF DONE
- `mcp-checkpoint` built, isolated, smoke-tested, registered; six MCP servers now total.
- Seven skills in place and wired to real tool names.
- Config guards applied; healthcheck warns on small context window.
- The Flask regression test passes WITHOUT the poll-forever hang.
- Nothing on the hermes-max out-of-scope list was added.