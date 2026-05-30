# CLAUDE_reliability_observability.md — Make hermes-max Work End-to-End: Adaptive Timeouts, Robust Init, Full Verbosity

You are fixing three classes of real failure in the completed `hermes-max` stack so it runs reliably
end-to-end and the operator has FULL CLARITY on what every tool is doing in real time. Work in STAGES,
in order; each independently committed, smoke-tested, validated. Read the whole spec first. Report
after each stage. This is a reliability + observability pass — it changes how existing tools time-out,
initialize, and report, NOT what they do.

## THE THREE PROBLEMS (verified from real runs)
1. **Timeouts are wrong in both directions.** A single global `WATCHDOG_TOOL_BUDGET_S` (default 120s)
   is too short for genuinely-long operations (deep_research can take 3-8 min; index_repo on a large
   repo can exceed 120s) and gives no signal on operations that do-nothing-and-hang. Result: long
   legitimate work gets killed prematurely, OR hung work isn't caught. Need PER-TOOL adaptive budgets
   with look-ahead (estimate the operation's expected duration before running it) + heartbeat-based
   liveness (distinguish "working" from "hung") so neither failure happens.
2. **RAG index initialization fails silently or ungracefully** on edge cases (empty directory, huge
   repo, unparseable files, missing embed endpoint). Need robust init that ALWAYS produces a usable
   index state (even if empty) and reports exactly what happened — never a silent failure or an
   unhandled timeout.
3. **No real-time clarity.** The operator cannot see what the agent is doing moment-to-moment — which
   tool is running, how long it's been running, what it returned, why it fell back. Need full verbose
   observability: structured, timestamped, human-readable tool-call logging visible live.

## NON-NEGOTIABLE DISCIPLINE (unchanged)
Extend via native surfaces only; never modify Hermes's core loop; each fix lives in the relevant MCP
server or the watchdog/observability layer; presence-gated; degrades cleanly; back up config; commit
per stage; failures reported honestly. No CUDA above `$VLLM_BASE_URL`.

---

## STAGE 1 — PER-TOOL ADAPTIVE TIMEOUTS WITH LOOK-AHEAD (no premature kill, no silent hang)
Replace the single global tool budget with per-tool budgets + look-ahead estimation + heartbeat
liveness. The principle: BEFORE running a tool, estimate how long it should take (look-ahead); set the
budget to that estimate plus headroom; WHILE running, require a heartbeat — kill only if the budget is
exceeded AND no heartbeat (genuinely hung), never if it's actively progressing.

- **Per-tool budget registry** (in mcp-watchdog or a shared config) — each tool declares an expected
  duration class and a hard ceiling:
  | Tool | Expected | Hard ceiling | Look-ahead input |
  |---|---|---|---|
  | quick_check, lint, type | seconds | 60s | file size |
  | verify (full tests) | tens of seconds | 300s | test count |
  | index_repo | scales with repo | 1800s (30min) | file count × avg size (estimate FIRST) |
  | search_code, RAG query | sub-second | 30s | — |
  | KG query/record | ms | 15s | — |
  | fetch_clean (Crawl4AI) | seconds-per-page | 90s/page | — |
  | deep_research | minutes | 900s (15min) | planned query count × per-source budget |
  | parallel_draft | seconds (concurrent) | 120s | pool size |
  | synth/steer/escalate call | seconds | 120s | — |
  Budgets configurable via `.env` (`BUDGET_<TOOL>_S`); these are defaults.
- **Look-ahead estimation:** for the variable-duration tools (index_repo, deep_research, fetch_clean),
  the tool computes an estimate BEFORE starting and logs it: "index_repo: 1,240 files, est ~95s,
  ceiling 1800s" / "deep_research: 12 planned queries × ~30s = est ~360s, ceiling 900s". This tells the
  watchdog what's normal and tells the OPERATOR what to expect. If the estimate alone exceeds the hard
  ceiling, the tool warns and either chunks the work or asks for a raised ceiling rather than starting
  a doomed run.
- **Heartbeat liveness (the do-nothing-hang detector):** long-running tools emit a heartbeat to the
  watchdog every N seconds (e.g. index_repo heartbeats per-file-batch; deep_research per-source). The
  watchdog's `check_stall` kills ONLY when (elapsed > budget) AND (no heartbeat for > heartbeat_timeout).
  A tool that's actively heartbeating is never killed even past its estimate — it's working. A tool
  silent past its heartbeat timeout is hung — killed and reported. This is the core fix: it
  distinguishes legitimately-long from genuinely-stuck.
- **On timeout/kill:** the watchdog reports EXACTLY what was killed, why (budget+no-heartbeat vs
  estimate-exceeds-ceiling), and how long it ran, then the agent's `workflow-stuck-detect-reset` fires
  (summarize → revert_to_last_green → try different). Never a silent hang.

**Stage-1 DoD:** index_repo on a large repo runs to completion past 120s because it heartbeats (proven:
a >120s index completes, not killed); a deliberately-hung tool (sleep with no heartbeat) is killed at
budget+heartbeat_timeout with a clear report; deep_research logs its look-ahead estimate and runs to its
real duration without premature kill; every variable-duration tool logs an estimate before starting.
Committed.

---

## STAGE 2 — ROBUST RAG INDEX INITIALIZATION (always a usable state, never a silent fail)
Make index_repo bulletproof across edge cases. It must ALWAYS leave a usable index state and report
exactly what happened.
- **Empty/near-empty repo:** index_repo on an empty or tiny directory completes instantly, creates a
  valid empty index, and reports "indexed 0 files (empty repo) — RAG will return no results until
  files exist." NOT a timeout, NOT a failure — a clean empty success. (This was the observed failure:
  empty dir caused a hang/timeout instead of an instant empty success.)
- **Pre-flight scan:** before indexing, scan the directory and report counts (files, by language,
  total size, est duration from Stage 1 look-ahead). Log it so the operator sees the scope upfront.
- **Large repo:** chunk the indexing into batches, heartbeat per batch (Stage 1), checkpoint progress
  so a kill mid-index doesn't lose everything. Report progress live ("indexed 400/1240 files…").
- **Unparseable files:** a file that tree-sitter can't parse is SKIPPED with a logged warning, never
  crashes the index. Report the skip count at the end.
- **Missing embed endpoint (EMBED_BASE_URL blank/down):** index in BM25 + graph mode (no dense lane),
  report "dense embeddings unavailable — indexed in BM25+graph mode." Already the documented behavior;
  ensure init honors it cleanly rather than failing.
- **Idempotent + resumable:** re-running index_repo updates incrementally; a partial index from a
  killed run resumes rather than restarting from zero.
- **Validation:** after init, the tool self-checks the index is queryable (run a trivial query) and
  reports the index health, so a corrupt/empty index is caught at init, not at first use mid-task.

**Stage-2 DoD:** index_repo on an EMPTY dir returns instant clean empty success (the original failure
is gone); on a large repo it pre-flight-scans, batches, heartbeats, and reports progress; an unparseable
file is skipped not fatal; blank EMBED_BASE_URL degrades to BM25+graph with a clear report; a killed
mid-index resumes on re-run; post-init self-check confirms queryability. Committed.

---

## STAGE 3 — FULL VERBOSE OBSERVABILITY (real-time clarity on every tool call)
The operator must see, live, exactly what the agent is doing. Add structured, timestamped,
human-readable tool-call logging visible in real time — beyond the Phoenix spans (which are for
post-hoc analysis), a live operator-facing stream.
- **Live tool-call log:** every MCP tool invocation emits, to a tailable log AND optionally stdout, a
  structured human-readable line:
  `[HH:MM:SS] → TOOL tool_name (server:port) | input: {brief summary} | est: ~Ns`
  `[HH:MM:SS] ⟳ tool_name heartbeat | progress: 400/1240 | elapsed 45s`
  `[HH:MM:SS] ✓ tool_name OK | 1.2s | returned: {brief summary}`
  `[HH:MM:SS] ✗ tool_name FAILED | reason: {429/timeout/empty} | falling back to: {next}`
  `[HH:MM:SS] ⚠ tool_name SLOW | 95s elapsed, est was 60s, still heartbeating (not killed)`
- **A single `scripts/watch.sh`** that tails the unified live log with color, so the operator runs ONE
  command in a side terminal and sees the entire agent loop in real time: which tool, what input, how
  long, what came back, every fallback, every heartbeat, every timeout decision.
- **Verbosity levels** via `.env` (`HERMES_MAX_VERBOSITY=quiet|normal|verbose|debug`): `normal` shows
  tool start/finish/fallback; `verbose` adds heartbeats + look-ahead estimates + input/output summaries;
  `debug` adds full payloads. Default `verbose` so the operator has clarity out of the box.
- **Decision transparency:** when the conductor routes (steer vs synth vs escalate), when RAG falls
  back to BM25, when a research source is skipped, when the watchdog makes a kill/keep decision — each
  logs the DECISION and the REASON, not just the action. The operator should never wonder "why did it
  do that."
- **Per-tool summary at task end:** `scripts/run-summary.sh` (or auto-printed at task end) — a table of
  every tool called this task: count, total time, failures, fallbacks, est-vs-actual duration. So after
  a run the operator sees exactly where time went and what fell back.
- These are observability additions ONLY — they emit alongside the existing Phoenix/OTel spans, never
  replace them, and degrade silently (logging failure never breaks a tool).

**Stage-3 DoD:** running any task with `scripts/watch.sh` in a side terminal shows a live, readable
stream of every tool call with input/output summaries, heartbeats, est-vs-actual, and every
fallback/routing/kill DECISION with its reason; verbosity levels switch via .env; a per-task summary
table prints at the end; logging never breaks a tool. Committed.

---

## STAGE 4 — END-TO-END DRY-RUN WITH THE NEW RELIABILITY + OBSERVABILITY
Prove the three fixes work together on a real end-to-end sequence with full visibility.
- Extend (or add) `scripts/dry_run.sh` to run a real sequence — index_repo (on both an empty dir AND a
  real repo), a RAG query, a KG record/recall, a verify pass, a short deep_research call (one source
  per type, real), a parallel_draft (if free keys present, else N=1-local), a checkpoint — ALL with
  `watch.sh` streaming live and the per-task summary printing at the end.
- Assert: no premature timeout on the legitimately-long steps (index, research heartbeat through);
  the empty-dir index returns clean empty success; every step's est-vs-actual is logged; a deliberately
  killed step reverts cleanly; the live log and summary are readable and complete.
- Output `dry_run_trace.md` (the readable artifact) showing the full sequence with timings, decisions,
  and the per-tool summary.

**Stage-4 DoD:** dry_run.sh runs the full sequence with live observability; empty-dir index is a clean
success not a hang; long steps complete via heartbeat without premature kill; the summary table and
trace file are complete and readable; runs in local/free/full modes. Committed.

---

## STAGE 5 — PROCESS LIFECYCLE (clean stop/restart/status, first-class not ad-hoc)
Make process management three reliable commands instead of manual PID/grep.
- **`scripts/stop-all.sh`** — kill every MCP by its PID file; fall back to port-based kill
  (lsof -ti:PORT) for any that don't die; confirm all MCP ports (9101-9110) + embed/rerank (8002/8003)
  are free; report what was stopped. Idempotent (safe to run when nothing's up).
- **`scripts/restart.sh [server|all]`** — stop then start one named server or all; re-runs health
  checks; reports final state. `restart.sh research` restarts only mcp-research.
- **`scripts/status.sh`** — for every server in the manifest: UP/DOWN, port, PID, uptime, last
  health-check result. One glance shows the whole stack's state. (Distinct from healthcheck.sh which
  is pass/fail for scripting; status.sh is the human view.)
- All read the manifest as the single source of truth, so adding a server doesn't require editing the
  lifecycle scripts.

**Stage-5 DoD:** stop-all.sh cleanly stops everything (PID + port fallback) and confirms ports free;
restart.sh restarts one server and all; status.sh shows UP/DOWN/port/PID/uptime per server; all
manifest-driven. Committed.

## STAGE 6 — STORE SNAPSHOTS (isolate test sessions; permanent-compounding by default)
The RAG/KG/corpus stores are PERMANENT and COMPOUNDING by default (long-term accumulated knowledge —
do not change this). Add snapshot/restore for ISOLATING test sessions without losing the real stores.
- **`scripts/snapshot-stores.sh <name>`** — copy current RAG index + KG db + on-disk corpus to
  `~/.hermes-max/snapshots/<name>/` with a timestamp + manifest of what was captured.
- **`scripts/restore-stores.sh <name>`** — swap a named snapshot back into the active store paths
  (backing up the current state first, so restore is reversible).
- **`scripts/list-snapshots.sh`** — list snapshots with timestamps + sizes.
- **Use pattern for a test run:** snapshot baseline → run the eval → inspect the compounded stores →
  either keep (real compounding continues) or restore baseline (clean slate for the next test). Default
  behavior with no snapshot calls = permanent compounding, unchanged.

**Stage-6 DoD:** snapshot-stores captures RAG+KG+corpus to a named dir; restore-stores swaps it back
(backing up current first); list-snapshots works; default no-snapshot behavior is unchanged
permanent-compounding. Committed.

## STAGE 7 — TQDM-STYLE EMPIRICAL PROGRESS + BOTTLENECK ANALYSIS (the key question: are the features wasting time?)
Two parts: real progress bars on the long operations, and the timing-split analysis that proves whether
the advanced MCPs introduce artificial bottleneck.

### 7a — tqdm-style progress on variable-duration tools
deep_research and index_repo (and any multi-item operation) emit tqdm-style empirical progress to the
live log, NOT vague "running…" messages:
```
deep_research: exploring sources [4/12] | arxiv.org/abs/2401.xxxx | crawl 3.2s · distil 8.1s | elapsed 47s · ETA ~95s
index_repo: [840/1240 files] | 67% | 52s elapsed · ETA ~24s | 3 skipped (unparseable)
```
Current item, total, per-item timing, running ETA, running elapsed. The operator sees real movement and
can tell instantly if it's progressing or stuck on one slow item.

### 7b — research-distillation defaults to LOCAL (avoid the rate-limit bottleneck)
The research cascade's per-source distillation defaults to the LOCAL model (already running, no rate
limit, handles bulk summarization fine), NOT a rate-limited cloud tier. Gating high-volume
distillation on Groq's 6-8K TPM would force serialization + 429 backoffs — a real artificial
bottleneck on exactly the highest-volume step. Cloud distillation is an explicit opt-in flag only, with
a warning that it's rate-limit-bound. Keep Groq for fast slop-drafting of small verifiable tasks (what
it's good at), not for the research cascade's bulk distillation.

### 7c — the bottleneck-analysis timing split (how you KNOW the features aren't wasting time)
Every task's per-tool summary (Stage 3) must break wall-clock time into three buckets:
- **inference** — local model thinking/generation (irreducible real work)
- **tool-work** — tool execution that does real work (Crawl4AI extraction, test runs, indexing)
- **artificial** — waiting on rate-limited APIs, 429/5xx backoffs+retries, redundant/sequential calls
  that could be concurrent, MCP overhead
Print the split per task: e.g. "inference 4m12s (58%) · tool-work 2m30s (35%) · artificial 0m31s (7%)".
If `artificial` is a large fraction, a specific feature is wasting the agent's time — the summary names
which (e.g. "artificial dominated by groq 429 backoff in research cascade — 14 retries, 48s").
- **Comparative timing eval (`scripts/bottleneck-eval.sh`):** run the SAME task twice — once full
  (all advanced MCPs active) and once bare (Hermes + local model only, conductor/research/RAG/KG off) —
  and print both the wall-clock and the 3-bucket split for each, plus a quality note. This is the ONLY
  empirical way to answer "do the advanced features earn their time?" If full takes 3x longer but the
  result is meaningfully better, justified. If 3x longer and no better, the features are artificial
  bottleneck → gate them more conservatively. Output a readable `bottleneck_report.md`.

**Stage-7 DoD:** deep_research and index_repo show tqdm-style progress (item N/total, per-item timing,
ETA); research distillation defaults local (cloud opt-in only, warned); every task summary prints the
inference/tool-work/artificial split and names the dominant artificial cost if large; bottleneck-eval.sh
runs the same task full vs bare and reports both time-splits + quality so the operator can SEE whether
the advanced features earn their latency. Committed.

## STAGE 8 — ERGONOMIC LAUNCHER: ONE `hm` COMMAND + TMUX COCKPIT (both `hm` and `./script.sh` work)
The stack now has many scripts (start-all/stop-all/restart/status/watch/snapshot/embed/rerank/hermes).
Collapse them into ONE memorable terminal-native command WITHOUT removing the underlying scripts — every
`scripts/*.sh` must still be directly runnable on its own. `hm` is a thin dispatch wrapper OVER them, not
a replacement. Both invocation styles are first-class and tested.

- **`hm` dispatch wrapper** (installed to the user's PATH via bootstrap; also runnable as `./hm` from the
  repo root). Pure dispatch over existing scripts — every verb maps to a script that still works
  standalone:
  | Command | Does | Underlying script (still runnable directly) |
  |---|---|---|
  | `hm up` | start all MCPs + embed + rerank + supporting containers, backgrounded | scripts/start-all.sh (+ serve-embed.sh, serve-rerank.sh) |
  | `hm down` | stop everything | scripts/stop-all.sh |
  | `hm restart [server\|all]` | restart one or all | scripts/restart.sh |
  | `hm status` | UP/DOWN/port/PID/uptime table | scripts/status.sh |
  | `hm watch` | tail the live tool-call log | scripts/watch.sh |
  | `hm logs [server]` | tail a server's log | (reads ~/.hermes-max/logs/) |
  | `hm run "task"` | launch hermes with a task | hermes |
  | `hm snapshot <name>` / `hm restore <name>` | store snapshot/restore | scripts/snapshot-stores.sh / restore-stores.sh |
  | `hm dev` | the tmux cockpit (below) | new |
  | `hm attach` | reattach to the running tmux cockpit | tmux attach |
  | `hm health` | pass/fail healthcheck (scripting) | scripts/healthcheck.sh |
  `hm` with no args prints the verb list. Unknown verb → helpful usage. `hm` is generated by bootstrap
  and symlinked/PATH-added; the repo also ships `./hm` so it works pre-install from the repo root.

- **`hm dev` — the one-command tmux cockpit (terminal-native, detachable, 24/7).** Spawns (or attaches
  to) a named tmux session `hermes-max` with a pre-laid-out window:
  - servers brought up backgrounded (start-all + embed + rerank) if not already healthy
  - pane 1 (large): interactive `hermes` prompt, ready for a task
  - pane 2: `watch.sh` live tool-call stream (the real-time clarity view)
  - pane 3: `status.sh` auto-refreshing every few seconds (stack health at a glance)
  - detach with the standard tmux binding; the session + servers keep running (24/7 unattended grind)
  - **reattach from anywhere** (e.g. laptop → Thor over the existing network) with `hm attach` — the
    always-on-and-checkable property is first-class. If the session already exists, `hm dev`/`hm attach`
    just attaches rather than respawning.
  - tmux is the ONLY new dependency; bootstrap checks for it and installs (apt/brew) or, if absent and
    not installable, `hm dev` degrades to a clear message + the manual multi-terminal instructions
    (never a hard fail — the individual scripts still work without tmux).

- **Both styles tested:** a smoke test asserts (a) every `scripts/*.sh` runs standalone exactly as
  before, AND (b) the matching `hm <verb>` produces the same effect. Neither is privileged; `hm` is
  sugar, the scripts are the substance.

**Stage-8 DoD:** `hm up/down/restart/status/watch/run/snapshot/dev/attach` all work and each maps to a
script that ALSO still runs directly (`./scripts/start-all.sh` etc unchanged); `hm dev` spawns a tmux
cockpit (hermes pane + live-watch pane + status pane) with servers backgrounded; detach keeps it running;
`hm attach` reattaches (including from another machine over the network); tmux absent → `hm dev` degrades
to manual instructions, individual scripts unaffected; smoke test confirms both invocation styles.
Committed.

## CROSS-CUTTING
- All new logging emits alongside existing Phoenix/OTel spans (new spans: tool_estimate,
  tool_heartbeat, tool_killed_hung, tool_slow_but_alive, index_progress, decision_logged). The live log
  is the operator-facing view; Phoenix is the analysis view; both fed by the same events.
- `.env` additions (all with sane defaults): `HERMES_MAX_VERBOSITY=verbose`,
  `BUDGET_INDEX_REPO_S=1800`, `BUDGET_DEEP_RESEARCH_S=900`, `BUDGET_VERIFY_S=300`,
  `HEARTBEAT_TIMEOUT_S=90` (kill only if no heartbeat this long past budget), per-tool overrides.
- README: document the per-tool budget table, the look-ahead+heartbeat model (why long work isn't
  killed and hung work is), the empty-repo clean-success behavior, `scripts/watch.sh` for live clarity,
  the `hm` launcher + `hm dev` tmux cockpit (with the note that every `scripts/*.sh` still runs directly
  — `hm` is sugar, not a replacement), and the detach/reattach-over-network always-on flow. State that
  verbose is the default so the operator always sees what's happening.

## OUT OF SCOPE
- No change to what tools DO (only their timeout/init/reporting behavior).
- No core-loop modification; no new heavy machinery.
- No replacing Phoenix/OTel (the live log is additive).
- No removing the global watchdog (it stays as the backstop; per-tool budgets refine it).

## REPORT (per stage)
What landed as {watchdog budget registry / index init / live-log / watch.sh / summary}; native-vs-built;
smoke + validation PASS/FAIL per assertion (failures are signal); the empty-dir-index test, the
long-op-not-killed test, the hung-op-killed test, the live-observability test; git SHA.

## DEFINITION OF DONE
index_repo never silently fails or hangs (empty = instant clean success, large = pre-flight-scanned +
batched + heartbeated + resumable, unparseable = skipped, no-embed = BM25+graph); every tool has a
per-tool adaptive budget with look-ahead estimation and heartbeat liveness so legitimately-long work
runs to completion and genuinely-hung work is killed with a clear report (neither premature kill nor
silent hang); the operator has full real-time clarity via `scripts/watch.sh` — a live readable stream of
every tool call's input/output/timing/heartbeat plus every routing/fallback/kill DECISION and its
reason, with a per-task summary table — verbose by default; process lifecycle is three reliable
manifest-driven commands (stop-all / restart / status); store snapshots isolate test sessions while the
default stays permanent-compounding; deep_research and index_repo show tqdm-style empirical progress with
ETAs; research distillation defaults local to avoid the rate-limit bottleneck; every task reports the
inference/tool-work/artificial time split and bottleneck-eval.sh proves (full vs bare) whether the
advanced features earn their latency; one ergonomic `hm` command subsumes the whole stack
(up/down/restart/status/watch/run/snapshot/dev/attach) while every `scripts/*.sh` still runs directly,
and `hm dev` gives a detachable, reattach-from-anywhere tmux cockpit (hermes + live-watch + status panes);
and an end-to-end dry-run proves all fixes work together with complete visibility. A typical
deep_research run completes in ~3-6 min (flagged if >10). Nothing out-of-scope built; core loop
untouched; config backed up; each stage committed; failures reported honestly.