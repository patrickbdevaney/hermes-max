# Long-Horizon Scaffolding for a 35B-A3B Agent
## Making a small-active-param model complete full projects reliably

### THE THESIS
Opus holds the plan, monitors its own confusion, models consequences, and recovers from
errors **internally** across dozens of turns. A 35B-A3B model (≈3B active params/token)
cannot sustain that executive function over a long horizon — but it IS genuinely good at
**one bounded, well-specified step at a time**. So we move the long-horizon cognition OUT
of the model and INTO the harness. The harness becomes the executive function:

| Executive function Opus does internally | Where we put it for the 35B-A3B |
|---|---|
| Hold the whole plan in working memory | **Kanban / pinned plan file** (model reads it each step) |
| Judge whether work is correct | **mcp-verify** (deterministic lint→types→tests; not the model's opinion) |
| Notice it's stuck / looping | **tool_loop_guardrails + stuck-detect skill** (caught at attempt 3) |
| Recover from a bad path | **checkpoint per subtask + clean-context reset** (bounded blast radius) |
| World-knowledge ("servers don't terminate") | **process-gotchas skill library** (the gaps made explicit) |
| Look ahead / model consequences | **plan-first + pre-mortem skill** (lookahead done structurally) |
| Keep relevant info in focus | **context hygiene: pin plan+subtask+verify, compress the rest** |

The model never holds the project. It holds: the current subtask, the relevant retrieved
code, and the rules. Everything else is externalized. This is how you get Opus-like
**completion reliability** (not Opus-like raw reasoning) on long-horizon work.

---

## PART 0 — CRITICAL PREREQUISITE: fix the context window

Your screenshot showed `26.3K / 65.5K` — Hermes thinks the window is **65K**, not the 262K
your vLLM serves. On a 65K window, long projects compress constantly and the model loses the
plan. Long-horizon performance is impossible until this is fixed.

Diagnose:
```bash
curl -s http://YOUR_TAILSCALE_IP:8001/v1/models | python3 -m json.tool   # what does vLLM report?
grep -i max-model-len ~/thor-decode/serve-qwen36-production.sh       # what did you serve with?
```
If vLLM is serving 262144 but Hermes capped at 65K, set it explicitly in the provider rather
than relying on auto-detect — edit `~/.hermes/config.yaml`:
```yaml
custom_providers:
- name: qwen_3.6_35b_a3b
  base_url: http://YOUR_TAILSCALE_IP:8001/v1
  model: /model
  api_mode: chat_completions
  context_length: 262144      # force it; don't trust auto-detect
```
If vLLM itself was launched with `--max-model-len 65536`, re-serve with the full length.
**Do not proceed to long-horizon work until the window reads ~262K.**

---

## PART 1 — THE SKILL LIBRARY (drop these into ~/.hermes/skills/)

Each is a markdown skill. They encode the executive function the model lacks. Keep them tight —
the model reads them, so verbosity costs context.

### skills/workflow-plan-first.md
```markdown
---
name: workflow-plan-first
description: ALWAYS run first on any task touching more than one file or more than ~3 steps.
trigger: any multi-step or multi-file task
---
# Plan first — never start coding a multi-step task without a written plan.

1. Restate the goal in one sentence and the DEFINITION OF DONE as a concrete, testable
   checklist (e.g. "endpoint returns 200 with JSON {ok:true}", "pytest passes", "file X exists").
2. Query codebase-rag (search_code) and knowledge-graph (recall_about) for relevant existing
   code, patterns, and prior decisions BEFORE planning. Plan around what exists; don't invent.
3. Decompose into the SMALLEST subtasks that are each independently verifiable. Each subtask =
   one bounded change you could verify with a single test or check. Aim for steps a junior dev
   could do in 10 minutes. Write them as a todo/kanban list.
4. PRE-MORTEM the plan (lookahead): for each subtask, ask "what is the most likely way this
   step hangs, errors, or produces something untestable?" Common traps: long-running processes
   (see process-gotchas), missing deps, wrong working dir, a step with no way to verify it.
   Adjust the plan to make every step verifiable and non-hanging.
5. Write the plan to a file (PLAN.md in the project) so it survives context compression.
   Re-read PLAN.md at the start of each subtask. The plan is the source of truth, not memory.
6. Only then begin subtask 1.
```

### skills/workflow-subtask-loop.md
```markdown
---
name: workflow-subtask-loop
description: How to execute ONE subtask. Apply to every subtask in the plan.
trigger: executing a planned subtask
---
# One subtask at a time. Bounded, verified, committed.

For the CURRENT subtask only (ignore the rest of the project for now):
1. Re-read the current subtask from PLAN.md and its definition of done.
2. Make the minimal change that satisfies just this subtask. Do not scope-creep into other
   subtasks — that pollutes context and causes drift.
3. Run mcp-verify on the affected files. If RED: fix and re-run. Max 3 fix attempts on the
   same error — if still red, invoke workflow-stuck-detect-reset. Do NOT thrash.
4. When GREEN: record what changed to knowledge-graph (record_entity / record_relation:
   what file, what decision, why). This is how the next subtask and the next session benefit.
5. Checkpoint: git add + commit with a one-line message naming the subtask. This is the
   rollback point — if a later subtask drifts, we revert to here, not to zero.
6. Mark the subtask done in PLAN.md. Move to the next subtask with a FRESH focus — you do not
   need the details of the completed subtask in active attention anymore.
```

### skills/workflow-long-running-processes.md  ← (the bug that just bit you)
```markdown
---
name: workflow-long-running-processes
description: How to handle servers, daemons, watchers — anything that runs indefinitely.
trigger: starting any server, daemon, watcher, or long-lived process
---
# A running server is SUCCESS, not a hang. Never poll a process that never ends.

When starting a Flask/FastAPI/uvicorn/node/webpack/any server or watcher:
- Start it backgrounded (append ` &` or use the background-process tool) and capture the PID.
- DO NOT poll it to completion. It will NEVER complete — that is its job. Polling it = infinite hang.
- Instead: wait 2-3 seconds for startup, then TEST IT ONCE:
    - HTTP server → `curl -s -m 5 http://localhost:<port>/<known-route>` (use -m 5 timeout!)
    - check the port is listening → `ss -ltn | grep <port>`
- If it responds correctly, the subtask is DONE. Record the PID so it can be stopped later
  (`kill <pid>`). Report success.
- NEVER wait more than ~10 seconds on a process meant to run forever.
- If you started a server and it is "still running" — that is the success condition, not a problem.
- Any command with no natural exit (tail -f, watch, serve, dev) → same rule: start, test once, move on.
```

### skills/skill-process-gotchas.md  ← (externalized world-knowledge the small model lacks)
```markdown
---
name: skill-process-gotchas
description: Common-sense traps a fast small model misses. Consult when a command behaves oddly.
trigger: any command that hangs, errors unexpectedly, or behaves contrary to expectation
---
# Things that are "obvious" but easy to get wrong. Check here before thrashing.

- LONG-RUNNING PROCESS never exits → don't wait for it (see workflow-long-running-processes).
- INTERACTIVE PROMPT (apt, npm init, ssh host-key, pip on conflict) hangs waiting for input →
  use non-interactive flags (-y, --yes, --no-input, DEBIAN_FRONTEND=noninteractive) or echo input.
- WRONG WORKING DIR: each terminal tool call may reset cwd. Always cd into the project, or use
  absolute paths. If a file "doesn't exist," check `pwd` first.
- VENV NOT ACTIVE: `pip install` then `python` can use different environments. Use the venv's
  python explicitly, or activate in the same command chain with &&.
- PORT ALREADY IN USE: a failed prior run left a server bound. `ss -ltn | grep <port>`,
  kill the old PID before restarting.
- TEST THAT NEEDS A RUNNING SERVER: start server backgrounded FIRST, then run the test, then
  kill the server. Don't run the test against nothing.
- A COMMAND THAT PRINTS NOTHING may have succeeded (many unix tools are silent on success).
  Check the exit code ($?), don't assume silence = failure and retry.
- BUILD/INSTALL IS SLOW not hung: npm install, cargo build, pip compile can take minutes. Give
  them a real timeout (300s+), don't kill at 30s and retry.
- INFINITE RETRY: if the same command failed twice identically, the THIRD identical attempt
  will also fail. Change something or invoke workflow-stuck-detect-reset.
```

### skills/workflow-stuck-detect-reset.md  ← (the killer recovery technique)
```markdown
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
3. Revert to the last green git checkpoint (workflow-subtask-loop step 5) so the code is in a
   known-good state, not a half-broken one.
4. RESET CONTEXT: start a fresh attempt with ONLY the STUCK SUMMARY + the reverted-clean code +
   PLAN.md. Drop all the failed-attempt history — it's noise now.
5. Try a DIFFERENT approach than the ones in the summary. If no different approach is obvious,
   or the same wall is hit again after reset → ESCALATE: ping the human (Telegram) with the
   STUCK SUMMARY and the specific blocker, and wait. Do not keep grinding. This is the
   "loop overnight then ping me" contract — a clean stuck-ping beats hours of thrashing.
```

### skills/workflow-done-definition.md
```markdown
---
name: workflow-done-definition
description: What "done" means. Applied before reporting any task complete.
trigger: about to report a task or subtask complete
---
# "Done" is defined by verification, never by the model's opinion.

Before reporting done:
1. Every item in the task's definition-of-done checklist (from workflow-plan-first) is met.
2. mcp-verify is GREEN (lint + types + tests). If the project has no tests, that itself is a
   gap — write at least one test that exercises the main path, then verify.
3. For anything with a runtime (server, CLI, script): it was actually RUN once and produced the
   expected output (see workflow-long-running-processes for how to test a server).
4. The knowledge-graph has the key decisions recorded; a skill was distilled if the task was novel.
5. Only then report done, with: what was built, the verify result, and how to run it.
If any of the above is not true, the task is NOT done — keep going or invoke stuck-detect-reset.
```

### skills/workflow-context-hygiene.md
```markdown
---
name: workflow-context-hygiene
description: Keep the working set small and the plan always in focus.
trigger: continuously, especially as context fills
---
# The model attends poorly to long context. Keep the relevant set small and pinned.

- PLAN.md is the source of truth, not memory. Re-read it at each subtask start. If your memory
  of the plan and PLAN.md disagree, PLAN.md wins.
- Keep in active focus only: the current subtask, the code being changed now, relevant retrieved
  snippets (from codebase-rag), and the rules. Everything else can be compressed/forgotten.
- Do NOT paste large file contents repeatedly. Retrieve the specific function with codebase-rag
  when needed instead of holding whole files in context.
- After finishing a subtask, you do not need its working details anymore — let them compress.
  The durable record is the git commit + the knowledge-graph entry, not the chat history.
```

---

## PART 2 — THE KICKOFF PROMPT TEMPLATE (front-load the whole operating manual)

The small model can't derive the operating discipline; give it the entire manual up front. Fill
in the GOAL and DEFINITION OF DONE, leave the rest. Use this for any non-trivial project task.

```
PROJECT GOAL:
<one or two sentences: what to build>

DEFINITION OF DONE (concrete, testable — this is the contract):
- <e.g. `pytest` passes>
- <e.g. server responds 200 at GET /health with {"ok":true}>
- <e.g. README explains how to run it>

OPERATING DISCIPLINE (follow exactly — these are your executive function):
1. PLAN FIRST. Before any code: restate the goal + definition of done, query codebase-rag and
   knowledge-graph for what already exists, decompose into the smallest independently-verifiable
   subtasks, PRE-MORTEM each for hangs/errors, and write the plan to PLAN.md. (skill: workflow-plan-first)
2. ONE SUBTASK AT A TIME. Execute only the current subtask; verify with mcp-verify (must be
   green); record the decision to knowledge-graph; git-commit as a checkpoint; then move on.
   (skill: workflow-subtask-loop)
3. SERVERS DON'T TERMINATE. Any server/daemon/watcher: start it backgrounded, test it ONCE with
   a timeout, then move on. NEVER poll a process that runs forever. (skill: workflow-long-running-processes)
4. WHEN A COMMAND MISBEHAVES, consult skill-process-gotchas before retrying. Never run the same
   failing command a third identical time.
5. WHEN STUCK (same error 3x / no progress / lost track of state): STOP. Write a stuck summary,
   revert to the last green commit, reset to a clean context with only the summary + plan, and
   try a DIFFERENT approach. If still stuck, ping me and wait. Do not thrash. (skill: workflow-stuck-detect-reset)
6. DONE means mcp-verify is green AND the definition-of-done checklist is fully met AND any
   runtime was actually run once. Your opinion that it's done does not count. (skill: workflow-done-definition)
7. KEEP CONTEXT CLEAN. PLAN.md is the source of truth; re-read it each subtask; don't hold whole
   files in memory — retrieve with codebase-rag. (skill: workflow-context-hygiene)

Begin with step 1 (PLAN FIRST). Do not write any code until PLAN.md exists.
```

---

## PART 3 — CONFIG GUARDS (tune the harness to enforce the above)

In `~/.hermes/config.yaml`:

```yaml
agent:
  max_turns: 200                 # long projects need room (was 150); steps are bounded so this is safe
  reasoning_effort: high         # free local tokens; use planning depth
  tool_use_enforcement: required

tool_loop_guardrails:
  warnings_enabled: true
  hard_stop_enabled: true        # CRITICAL: catches the poll-forever / thrash class
  warn_after:
    exact_failure: 2
    same_tool_failure: 2         # tightened: warn on 2nd identical failing tool call
    idempotent_no_progress: 2
  hard_stop_after:
    exact_failure: 4
    same_tool_failure: 4         # tightened from 8: stop thrashing sooner, trigger stuck-reset
    idempotent_no_progress: 3    # tightened from 5: a stalled poll trips this fast

terminal:
  timeout: 600                   # real builds need minutes; but bounded so a true hang trips guardrails
  lifetime_seconds: 3600

compression:
  threshold: 0.75
  target_ratio: 0.35
  protect_last_n: 40
  protect_first_n: 5             # protect the kickoff/plan instructions from compression

checkpoints:
  enabled: true
  min_interval_hours: 1          # plus the per-subtask git commits the skills enforce

kanban:
  auto_decompose: true           # native decomposition supports the plan-first discipline

skills:
  guard_agent_created: true
```

A note on the `same_tool_failure` / `idempotent_no_progress` tightening: the poll-forever hang
didn't trip the old thresholds because a long poll reads as "in progress," not "failure." The
real fix is the **workflow-long-running-processes skill** (prevents it) + **idempotent_no_progress: 3**
(a poll that makes no progress for 3 checks trips the guardrail and hands control to stuck-reset).
Belt and suspenders.

---

## PART 4 — THE HONEST CEILING

With this scaffolding a 35B-A3B will **reliably complete bounded, well-decomposed engineering
projects over long horizons** — CRUD apps, integrations, refactors, test suites, scripted
pipelines, multi-file features — because the harness does the long-horizon cognition and the
model only does bounded steps it's genuinely good at. That is the Opus-like *completion
reliability* you're after.

It will NOT match Opus on: novel algorithm design, subtle architectural judgment, gnarly
root-cause debugging of non-obvious bugs, or one-shot reasoning over a huge problem. Those are
raw-capability tasks where active-param count is the bottleneck and no scaffolding closes it.
That's exactly what Lane 2 (Claude Code + Opus) is for — and the mcp-escalation hook (off by
default) is the seam where a genuinely-hard subtask can be handed up.

The division of labor is the design: the 35B-A3B grinds the long, bounded, well-structured 80%
at $0; Opus takes the hard, novel 20% when escalated. The scaffolding above is what makes the
80% reliable rather than a series of poll-forever hangs.
```