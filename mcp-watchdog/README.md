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
| `check_stall(tool_name, elapsed_s, expecting_heartbeat, last_heartbeat_age_s, per_tool_budget_s)` | HUNG vs legitimately WAITING — a heartbeating process is **never** killed (OpenHands #5355 false-kill trap) |
| `check_progress(task_id, signals, n)` | progress-delta across calls (`files_touched`/`tests_passing`/`checkpoints`/`turn`); flags `no_progress` after N stalls |
| `start_task_budget(task_id, wall_clock_s, max_turns, usd_cap)` / `check_budget(...)` | per-task budget Hermes config has no native knob for |

## OTel

Emits spans to Phoenix (`spiral_detected`, `poll_hang_caught`, `no_progress`,
`budget_exceeded`) via `otel_emit.py`. If Phoenix is down, spans drop silently.

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
