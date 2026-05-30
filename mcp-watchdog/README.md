# mcp-watchdog (port 9107)

The **non-turn-based detection layer** — the Stage-0 robustness floor. Hermes'
native `tool_loop_guardrails` only see things *across turns*. The two
field-observed failures were *single unbounded operations within one turn*:

- a **CoT/thinking spiral** (the model loops inside one reasoning block), and
- a **server-poll hang** (a single tool call blocks forever waiting on a daemon).

This server gives the workflow skills deterministic, model-free signals to
self-check *inside* a turn. It does **not** modify Hermes' loop.

## Tools

| tool | purpose |
|------|---------|
| `check_spiral(recent_thinking_text)` | repeated-n-gram + LZ-compressibility + consecutive-segment similarity → `spiral_detected` |
| `tool_budget(tool_name)` | the per-tool adaptive budget: expected class, soft budget, **hard ceiling**, heartbeat timeout, look-ahead input |
| `estimate_duration(tool_name, inputs)` | **look-ahead** — estimate how long a variable-duration tool *should* take BEFORE it runs; flags a doomed run whose estimate alone exceeds the ceiling |
| `record_heartbeat(task_id, tool_name, progress, done, total)` | stamp a liveness beat (per file-batch / per source) so `check_stall` knows the tool is WORKING |
| `check_stall(tool_name, elapsed_s, expecting_heartbeat, last_heartbeat_age_s, per_tool_budget_s, task_id)` | HUNG vs WAITING via the per-tool budget+ceiling — a heartbeating process is **never** killed for being slow (OpenHands #5355 false-kill trap), only a silent over-budget one or a hard-ceiling runaway |
| `check_progress(task_id, signals, n)` | progress-delta across calls (`files_touched`/`tests_passing`/`checkpoints`/`turn`); flags `no_progress` after N stalls |
| `start_task_budget(task_id, wall_clock_s, max_turns, usd_cap)` / `check_budget(...)` | per-task budget Hermes config has no native knob for |

## Per-tool adaptive budgets + look-ahead (Stage 1)

The single global `WATCHDOG_TOOL_BUDGET_S` was wrong in both directions — too
short for genuinely-long work (deep_research 3–8 min, a large `index_repo`) and
useless against a do-nothing hang. The fix: a **per-tool registry** with a hard
ceiling + a **look-ahead** estimate + **heartbeat liveness**.

| tool | expected | hard ceiling | look-ahead input |
|------|----------|-------------:|------------------|
| `quick_check` / `lint` / `type` | seconds | 60s | file size |
| `verify` (full tests) | tens of seconds | 300s | test count |
| `index_repo` | scales with repo | 1800s | file count × avg size |
| `search_code` / RAG query | sub-second | 30s | — |
| `kg_query` / `kg_record` | ms | 15s | — |
| `fetch_clean` (Crawl4AI) | seconds/page | 90s | page count |
| `deep_research` | minutes | 900s | query count × per-source |
| `parallel_draft` | seconds (concurrent) | 120s | pool size |
| `synth` / `steer` / `escalate` | seconds | 120s | — |

Ceilings are overridable per tool via **`BUDGET_<TOOL>_S`** in `.env` (e.g.
`BUDGET_INDEX_REPO_S`, `BUDGET_DEEP_RESEARCH_S`). The **kill rule**:

> kill only when `elapsed > ceiling` (a hard runaway), **or** when
> `elapsed > budget` **and** no heartbeat for `> HEARTBEAT_TIMEOUT_S` (silent hang).

A tool that keeps heartbeating runs to completion *past* its estimate — it is
**slow-but-alive**, never false-killed — while a tool that goes silent past its
budget IS killed with a clear reason. `estimate_duration` logs what's normal
before the run (`index_repo: 1,240 files, est ~98s, ceiling 1800s`); if the
estimate *alone* exceeds the ceiling it flags a doomed run to chunk or raise
rather than start. Unknown tools fall back to the global budget with **no** hard
ceiling (heartbeat governs entirely — a long-lived dev server is never killed).

## OTel

Emits spans to Phoenix via `otel_emit.py`: `spiral_detected`, `poll_hang_caught`,
`no_progress`, `budget_exceeded`, plus the Stage-1 spans `tool_estimate`,
`tool_heartbeat`, `tool_killed_hung`, `tool_slow_but_alive`. If Phoenix is down,
spans drop silently.

## Graceful degradation

If this process is killed, Hermes reports its tools unavailable and the agent
keeps working on the native turn-based guardrails alone — it never crashes
Hermes. The skills treat a watchdog call failure as "skip the self-check".

## Run / test

```bash
.venv/bin/python smoke_test.py     # standalone, no network
./healthcheck.sh                   # GET /health
```

All config is env-driven: `MCP_WATCHDOG_PORT` (9107), `WATCHDOG_TOOL_BUDGET_S`
(120), `WATCHDOG_STATE_DIR` (`~/.hermes-max/watchdog`), and the
`WATCHDOG_SPIRAL_*` thresholds.
