# CLAUDE_harness_max.md — Implement the Two-Axis Harness Upgrade into hermes-max

You are upgrading the already-built `hermes-max/` harness (six MCP servers + workflow skills +
hermes-config) to maximize the realized engineering capability of a local model
(Qwen3.6-35B-A3B / 27B-dense / 122B-A10B) on a single Jetson AGX Thor. This spec implements the
research roadmap on BOTH axes: **robustness (never get stuck)** and **capability (engineer like
Opus)**. Work in STAGES, in order; each stage is independently committable, smoke-tested, and
validated before the next. Read this whole spec first. Report after each stage.

## NON-NEGOTIABLE ARCHITECTURE DISCIPLINE (the anti-Frankenstein gate — unchanged from the original build)
1. **Extend only via native surfaces.** Hermes config, MCP servers, skills (SKILL.md), and hooks.
   **Never modify Hermes's core agent loop.** If a lever seems to require a loop change, STOP and
   implement it as a watchdog MCP server + skill + config instead, and note the limitation.
2. **Each value-add is an independent MCP server OR an extension of an existing one** — never a
   tangle. New servers bind 127.0.0.1, own venv, own healthcheck, own smoke_test, registered via
   `scripts/register-mcp.sh`.
3. **Kill-any-component-degrades-gracefully.** If a new server is down/disabled, the agent must
   keep working with a clear warning, never crash. Test this for every new component.
4. **Single `$VLLM_BASE_URL` story.** Never hardcode a host. All model access reads the env var.
5. **Discovery-first.** Before building anything that might be a Hermes-native config knob (per-turn
   token caps, tool timeouts, reasoning_effort, budgets), FIRST `grep` the Hermes config schema and
   `hermes config` help / docs to find the native setting. Prefer a config line over a built server.
   Only build a server for what config genuinely cannot do. Report what was native vs built.
6. **your inference host-aware.** This is a single bandwidth-bound box (~273 GB/s, one model stream). Any lever
   that multiplies model calls (best-of-N, MCTS, multi-agent, critic) MUST be bounded by the
   Stage-0 budgets and default to conservative N, because samples compete for the one GPU.
7. **Back up `~/.hermes/config.yaml` (timestamped .bak) before any config edit.** Commit each stage
   to git with a clear message; never commit `.env`, venvs, or caches (`.gitignore` already covers).

## EXISTING STACK (build on this, do not duplicate)
Servers: mcp-verify (9101, lint→type→tests), mcp-codebase-rag (9102, BM25 + optional embeddings),
mcp-knowledge-graph (9103), mcp-observability (9104, OTel→Phoenix), mcp-escalation (9105,
off-by-default, USD cap), mcp-checkpoint (9106, git revert_to_last_green). Skills: plan-first,
subtask-loop, long-running-processes, stuck-detect-reset, done-definition, context-hygiene,
escalate, plus task-start/task-finish/plan/stuck/process-gotchas. Config: compression 0.75,
tool_use_enforcement required, turn-based guardrails (same_tool_failure:4, idempotent_no_progress:3,
hard_stop), MTP-2. Validation harness: scripts/finalize_validation.py (V1/V2/V3).

---

## STAGE 0 — ROBUSTNESS FLOOR (do first; closes the two field-observed failures + the full deadline/detection layer)
The field failures were a CoT/thinking spiral and a server-poll hang — BOTH invisible to turn-based
guardrails because they are *single unbounded operations within a turn*. This stage installs
wall-clock/token deadlines and non-turn-based detection, routed into the existing checkpoint/revert.

### 0.1 — Discovery
- Grep the Hermes config schema for native: per-turn max output tokens, per-tool timeout, per-task
  wall-clock/USD/iteration budget, reasoning_effort. Report which exist natively. Implement those
  via config; build the rest as the `mcp-watchdog` server below.

### 0.2 — Config deadlines (native where possible)
- **Per-tool wall-clock timeout:** lower `terminal.timeout` default to a short value (e.g. 120s) BUT
  add a documented "waiting mode" — long-running processes must be started backgrounded and polled
  ONCE with a timeout, never blocked-on (this is the poll-hang fix; the `workflow-long-running-
  processes` skill already describes it — make the timeout enforce it).
- **Per-turn output cap:** set the native per-turn max-tokens to a bound that forces emission
  (prevents the unbounded thinking block). If Hermes exposes this, set it; if not, the watchdog
  (0.3) approximates it.
- **Per-task budget:** confirm/set `max_turns`, add a wall-clock task lifetime and (reuse the
  escalation server's accounting) a USD ceiling for any escalated calls.

### 0.3 — BUILD: `mcp-watchdog` server (port 9107) — the missing detection layer
A new independent MCP server the agent (and the workflow skills) call to self-check. It does NOT
modify the loop; it provides deterministic signals the skills act on. Tools:
- `check_progress(task_id)` → returns progress-delta since last call: files changed, tests passing
  delta, checkpoint cadence, turns-since-last-green. Flags `no_progress` if all deltas zero over N.
- `check_spiral(recent_thinking_text)` → runs an n-gram / LZ-style repetition + semantic-similarity-
  of-consecutive-segments check on the supplied recent reasoning; returns `spiral_detected` bool +
  reason. (This is the CoT-spiral detector; the skill calls it and aborts/redirects on true.)
- `check_stall(tool_name, elapsed_s, expecting_heartbeat)` → returns `hung` if a single tool call
  exceeds its per-tool budget without a heartbeat; distinguishes "hung" from "legitimately waiting"
  (the OpenHands #5355 false-kill bug — do NOT kill a process that is heartbeating/producing output).
- `start_task_budget(task_id, wall_clock_s, max_turns, usd_cap)` / `check_budget(task_id)` →
  per-task budget tracking; returns `budget_exceeded` + which limit.
Own venv, healthcheck, smoke_test. Smoke test must assert: a repeated-n-gram string trips
check_spiral; a zero-delta sequence trips check_progress; a heartbeating long call does NOT trip
check_stall but a silent over-budget one does.

### 0.4 — Skills wiring (the prevention + recovery half)
- NEW skill `workflow-deadline-discipline`: every execution turn, reason in ≤3-4 sentences then act;
  for any long-running process, start backgrounded + `check_stall` once, never poll-loop; call
  `check_progress` every few subtasks; on `no_progress`/`spiral_detected`/`budget_exceeded` →
  write a STUCK SUMMARY and invoke `revert_to_last_green` + replan (the recovery ladder).
- UPDATE `workflow-stuck-detect-reset` to consume the watchdog signals (not just turn-based) and to
  climb the recovery ladder: incremental-verify → checkpoint-revert → replan → alternative-approach
  → decompose-further → fresh-subagent → escalate → abort-clean.
- UPDATE `workflow-long-running-processes` to mandate the backgrounded+check_stall-once pattern.

### 0.5 — EXTEND mcp-checkpoint: agent-state snapshot
- Add `snapshot_state(task_id, plan, notes)` / `restore_state(task_id)` so revert restores the
  reasoning context (PLAN.md + decision notes), not only the git tree. Store under
  `~/.hermes-max/state/`. Keep it small; smoke-test round-trip.

### 0.6 — Stage-0 validation (extend finalize_validation.py)
- V-spiral: feed a prompt that historically spiraled; confirm check_spiral fires and the agent
  aborts-and-replans instead of looping (assert turn/token count bounded).
- V-poll: task that starts a server; confirm it backgrounds + check_stall-once + proceeds (never
  hangs); assert wall-clock bounded.
- V-budget: confirm budget_exceeded triggers clean checkpoint+stop.
- All existing V1/V2/V3 still pass.

**Stage-0 DoD:** mcp-watchdog live + smoke-green; config deadlines set (native-where-possible,
reported); deadline/spiral/poll/budget skills wired into the existing recovery; agent-state snapshot
works; V-spiral/V-poll/V-budget + V1/V2/V3 pass; killing mcp-watchdog degrades gracefully.

---

## STAGE 1 — HIGHEST-ROI CAPABILITY LEVERS (largest measured SWE-bench impact)

### 1.1 — EXTEND mcp-codebase-rag: graph/AST-aware retrieval (the biggest multi-file lever)
RepoGraph-class retrieval gave +32.8% relative resolve-rate; this is the operator's top capability
gap. Add ON TOP of existing BM25+embeddings (do not replace — hybrid):
- tree-sitter AST chunking + a repo-map (symbol/def graph) with PageRank-style ranking (Aider-style),
  token-budgeted.
- call-graph / dependency-aware retrieval: `retrieve_related(symbol)` returns multi-hop neighbors
  (callers/callees/imports), since ~90% of real fixes need multi-hop connections.
- Keep agentic/iterative retrieval: the agent decides what to fetch next.
- Graceful degradation: if tree-sitter/graph build fails, fall back to BM25+embeddings with a clear
  "graph retrieval unavailable" warning (healthcheck banner). Smoke-test the graph path and the
  fallback.

### 1.2 — BUILD: `mcp-search` server (port 9108) — verifier-guided test-time search
SWE-PRM-class selection gave +10.7 pts. your inference host-bounded:
- `generate_and_select(task_spec, n)` → generate N candidate patches (bounded, default N=3 on your inference host;
  configurable), run each through mcp-verify, select the green one; if multiple green, prefer the
  smallest diff / one passing the most tests. Lossless-by-construction (selection is execution-based).
- Default OFF / low-N; only invoke on hard subtasks (the difficulty signal from Stage 3) so it
  doesn't multiply your inference host inference on easy work. Bounded by Stage-0 per-task budget.
- Own venv/health/smoke. Smoke-test: given 3 candidate functions (1 correct), it selects the one
  that passes the provided tests.

### 1.3 — Edit-format discipline (Aider 20%→61% lever)
- NEW skill `workflow-edit-format`: prefer diff/search-replace edits over whole-file rewrites; every
  edit must be well-formed and verified by `file_mutation_verifier` (Hermes has this) + an
  incremental verify after each edit. If Hermes's native edit tool supports a diff mode, mandate it
  via the skill; if a structured semantic-edit helper is needed, add it as a small tool on
  mcp-verify (it already owns the code-correctness surface). Do NOT add a whole new server for this.

### 1.4 — Reasoning-effort / thinking-budget routing
- Discovery: confirm how Hermes exposes per-request reasoning_effort / enable_thinking and whether
  per-turn override is possible (config or per-call).
- NEW skill `workflow-effort-routing`: HIGH effort on plan/architecture/hard-debug turns, LOW/OFF on
  reads/searches/mechanical edits/tool-routing. Set the global default to MEDIUM (not high — high on
  execution turns is what caused the spiral), and have the skill raise to high only for planning and
  flagged-hard subtasks. This caps spirals AND concentrates reasoning where it counts.

**Stage-1 DoD:** graph-RAG live with BM25 fallback; mcp-search live, default-low-N, selects by
verify; edit-format + effort-routing skills wired; a multi-file task demonstrably uses graph
retrieval and (on a hard subtask) verifier-guided selection; effort routing measurably lowers
thinking tokens on mechanical turns. Killing either new capability degrades gracefully.

---

## STAGE 2 — DEPTH & COMPOUNDING

### 2.1 — EXTEND mcp-verify: deeper verification (closes silent-wrong-answer; ~20% of patches are semantically wrong)
- Add optional layers beyond lint→type→unit: property-based tests (hypothesis), mutation testing
  (mutmut/cosmic-ray — report surviving mutants), and a lightweight fuzz harness. Gate "done" on the
  layers appropriate to the task (don't run mutation testing on trivial changes — tie depth to the
  difficulty signal). Each layer independently skippable with a warning if its tool isn't installed.

### 2.2 — Critic / reviewer sub-agent + fast monitor model
- NEW skill `workflow-critic`: after a green subtask, spawn a bounded review pass (builder→validator)
  that red-teams the diff against the spec and the tests. Use Hermes delegation (subagent_auto_approve
  for reversible review). OPTIONAL: stand up an LFM2.5-class fast model on a second `$..._BASE_URL`
  as a combined cheap watchdog+critic; gate behind an env flag, default off, measure your inference host contention
  before adopting (per the earlier analysis — two models share the one bus).

### 2.3 — Wire GEPA/DSPy automated skill curation
- The repo already has a dspy-evolution cron wrapper but `hermes-agent-self-evolution` wasn't
  bundled. Install/wire it (graceful no-op if unavailable), schedule the GEPA/DSPy optimization of
  the most-used skills/prompts/tool-descriptions from session traces. Bounded ($/run), scheduled,
  off the hot path. This is the compounding lever — the agent gets better on the operator's codebase
  over time.

### 2.4 — Sub-agent context isolation
- NEW skill `workflow-subagent-isolation`: use isolated read-only sub-agents (Glob/Grep/Read-scoped)
  for research/localization, returning only a summary to the parent; KEEP the edit thread single and
  linear with disciplined compaction (Cognition's lesson — don't fan out the edit path). Wire via
  delegation config + tool-scoping.

**Stage-2 DoD:** deeper verify layers available and difficulty-gated; critic pass runs on a real
task and catches an injected silent-wrong patch; GEPA curation runs (or graceful no-op); sub-agent
isolation used for a localization task without corrupting the edit thread. All degrade gracefully.

---

## STAGE 3 — ESCALATION (the honest capability ceiling)

### 3.1 — EXTEND mcp-escalation: auto-triggers + difficulty classifier + local tier
- Add a difficulty classifier (cheap, up-front): tag each task/subtask easy/medium/hard from signals
  (file count, novelty, prior-failure). Route: easy/medium → local model; hard-kernel → first the
  LOCAL escalation tier (122B-A10B or 27B-dense via a second `$ESCALATION_LOCAL_BASE_URL`), then
  cloud frontier only if local-tier also fails.
- Auto-trigger escalation when: verifier-guided search exhausts N without green; backtracking
  exhausts approaches; confidence-low + irreversible/high-stakes. Keep the hard USD cap and
  off-by-default for CLOUD; the local tier is free so it can be on by default.
- **Surgical handoff:** pass full PLAN.md + relevant diffs + failure traces (NOT a lossy summary) —
  reuse the agent-state snapshot from 0.5.

**Stage-3 DoD:** difficulty classifier tags tasks; hard kernel routes to local-tier then cloud;
auto-triggers fire on exhausted search/backtrack; handoff carries full context; cloud stays
USD-capped and gated.

---

## CROSS-CUTTING (apply throughout)
- **Observability:** every new server emits OTel spans to mcp-observability (Phoenix) so stuck/
  recovery/escalation events are visible. Add spans for: spiral_detected, poll_hang_caught,
  budget_exceeded, search_selected, critic_rejected, escalated.
- **Ports/registration:** new servers 9107 (watchdog), 9108 (search); register via
  `scripts/register-mcp.sh`; add to `scripts/start-all.sh`, `healthcheck.sh`, `smoke-test.sh`.
  Update `.env.example` with any new ports/flags.
- **Graceful degradation matrix:** for EACH new/extended capability, document and test what happens
  when it's down — the agent must continue with a clear warning, never crash.
- **Difficulty signal is shared:** Stage-1 search depth, Stage-2 verify depth, and Stage-3 escalation
  all consume the same difficulty tag — implement it once (Stage 3 classifier) and reference it.

## OUT OF SCOPE (do NOT build — the anti-Frankenstein gate)
- No core-loop modification. No replacing Hermes's planner/memory/delegation with bespoke versions.
- No full multi-agent swarm on the EDIT path (Cognition's warning + your inference host contention) — critic/review
  and isolated reads only.
- No unbounded test-time search/MCTS (your inference host is one stream) — everything bounded by Stage-0 budgets.
- No new memory tier beyond the existing KG + session store + skills (you have the full stack).
- Don't reintroduce known traps: OpenHands' kill-the-waiter loop detector, lossy sub-agent summaries,
  intrinsic (un-grounded) self-correction loops.

## REPORT (after each stage)
Per stage: what was implemented as {config / new server / extended server / skill}; what was native
vs built; smoke-test + validation results (PASS/FAIL per assertion, honestly — a failed validation
is signal, not something to hide); the graceful-degradation test result; the git commit SHA.

## DEFINITION OF DONE (whole spec)
Stages 0–3 implemented in order, each committed and validated. The two field-observed failures
(CoT spiral, poll-hang) are structurally closed (V-spiral/V-poll pass). Graph-RAG, verifier-guided
search, deeper verify, critic, GEPA curation, and tiered escalation are live and difficulty-gated.
Every new component degrades gracefully when killed. Nothing on the out-of-scope list was built; the
core loop was never modified; `$VLLM_BASE_URL` is the only model-host story; config was backed up
before edits. If any stage's validation FAILS, report it honestly with diagnostics rather than
papering over — a failed spiral-detector or a search that doesn't beat baseline is important signal.