"""mcp-watchdog — the non-turn-based detection layer (the Stage-0 robustness floor).

Transport: streamable-http on $MCP_WATCHDOG_PORT (default 9107), path /mcp.
Health:    GET /health (reports tool budget + spiral thresholds).

It modifies NOTHING in Hermes' loop. It only exposes deterministic self-check
signals the workflow skills call WITHIN a turn to catch the two failures the
turn-based guardrails are blind to: CoT spirals and silent poll/stall hangs,
plus per-task budgets. If this process dies, Hermes reports its tools
unavailable and the agent keeps working on the native turn-based guardrails —
it never takes Hermes down.
"""

from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import watchdog_core

PORT = int(os.environ.get("MCP_WATCHDOG_PORT", "9107"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-watchdog",
    instructions=(
        "Deterministic self-check signals for never-getting-stuck. For a "
        "variable-duration tool call estimate_duration FIRST (look-ahead) and log "
        "it; record_heartbeat as it makes progress; then check_stall ONCE — it uses "
        "the PER-TOOL adaptive budget + hard ceiling (tool_budget) and will NOT "
        "report a heartbeating process as hung, only a silent over-budget one or a "
        "hard-ceiling runaway. Call check_spiral on recent reasoning to catch a "
        "thinking loop; check_progress every few subtasks to catch a stall; and "
        "start_task_budget/check_budget for per-task wall-clock/turns/USD limits. On "
        "spiral_detected / no_progress / hung / budget_exceeded, write a STUCK "
        "SUMMARY and call revert_to_last_green + replan."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _threaded(fn):
    """Run a sync @mcp.tool() body on a worker thread so it never blocks the event
    loop. FastMCP (1.27) calls sync tool handlers directly in the single event-loop
    thread, so any long tool (running tests, indexing a repo, an LLM/cloud call,
    fetching+distilling a page) stalls EVERY other request — including GET /health,
    which is what made a live server show DOWN while it was actively serving the
    agent. asyncio.to_thread offloads the body so /health and concurrent calls stay
    responsive; functools.wraps preserves the typed signature for the schema, and
    the body runs in a thread with no running loop (so MCP-to-MCP asyncio.run works).
    """
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-watchdog", "port": PORT,
                         **watchdog_core.status()})


@mcp.tool()
@_threaded
def check_spiral(recent_thinking_text: str, ngram: int | None = None) -> dict:
    """Detect a reasoning/CoT spiral in supplied recent reasoning text.

    Model-free: combines repeated n-gram ratio, LZ compressibility, and
    consecutive-segment similarity. Returns spiral_detected + reason + metrics.
    Call it when a turn's reasoning is getting long or circular; on True, stop
    thinking, write a STUCK SUMMARY, and replan / revert instead of looping.
    """
    return watchdog_core.check_spiral(recent_thinking_text, ngram)


@mcp.tool()
@_threaded
def check_stall(tool_name: str, elapsed_s: float, expecting_heartbeat: bool = False,
                last_heartbeat_age_s: float | None = None,
                per_tool_budget_s: float | None = None,
                task_id: str | None = None) -> dict:
    """Decide if an in-flight tool call is HUNG vs legitimately WAITING, using the
    PER-TOOL adaptive budget + hard ceiling registry (Stage 1).

    Killed when elapsed exceeds the tool's HARD ceiling, OR when it is over its soft
    budget AND has produced no heartbeat for > HEARTBEAT_TIMEOUT_S. A heartbeating
    process is "slow-but-alive" — never killed for being slow. Pass task_id to let
    the watchdog read the heartbeat age stamped by record_heartbeat (no need to
    track it yourself). Call ONCE on a backgrounded long-running process instead of
    polling it to completion (the poll-hang fix).
    """
    return watchdog_core.check_stall(tool_name, elapsed_s, expecting_heartbeat,
                                     last_heartbeat_age_s, per_tool_budget_s, task_id)


@mcp.tool()
@_threaded
def tool_budget(tool_name: str) -> dict:
    """Return the per-tool adaptive budget for a tool: expected-duration class, soft
    budget, HARD ceiling (env-overridable via BUDGET_<TOOL>_S), heartbeat timeout,
    and the look-ahead input. Unknown tools fall back to the global budget."""
    return watchdog_core.tool_budget(tool_name)


@mcp.tool()
@_threaded
def estimate_duration(tool_name: str, inputs: dict | None = None) -> dict:
    """Look-ahead: BEFORE running a variable-duration tool, estimate how long it
    SHOULD take so neither legitimately-long work is killed nor a doomed run is
    started. inputs keys by tool — index_repo: {file_count, total_bytes};
    deep_research: {query_count, per_source_s}; fetch_clean: {page_count};
    verify: {test_count}. Returns est_s, hard ceiling, exceeds_ceiling, and a
    human-readable basis to log for the operator."""
    return watchdog_core.estimate_duration(tool_name, **(inputs or {}))


@mcp.tool()
@_threaded
def record_heartbeat(task_id: str, tool_name: str, progress: str | None = None,
                     done: int | None = None, total: int | None = None) -> dict:
    """Stamp a liveness heartbeat for an in-flight long-running tool (per file-batch
    for index_repo, per source for deep_research). Proves the tool is WORKING so
    check_stall(task_id=...) won't kill it for being slow. Carries item N/total for
    the live progress log."""
    return watchdog_core.record_heartbeat(task_id, tool_name, progress, done, total)


@mcp.tool()
@_threaded
def check_progress(task_id: str, signals: dict | None = None, n: int = 3) -> dict:
    """Progress-delta since the last call for this task. signals carries
    monotonic observables {files_touched, tests_passing, checkpoints, turn}.
    Flags no_progress=True after n consecutive calls with zero forward delta."""
    return watchdog_core.check_progress(task_id, signals, n)


@mcp.tool()
@_threaded
def start_task_budget(task_id: str, wall_clock_s: float | None = None,
                      max_turns: int | None = None, usd_cap: float | None = None) -> dict:
    """Begin per-task budget tracking (wall-clock seconds / turns / USD). Any
    limit left None is unbounded. Pair with check_budget."""
    return watchdog_core.start_task_budget(task_id, wall_clock_s, max_turns, usd_cap)


@mcp.tool()
@_threaded
def check_budget(task_id: str, turns_used: int | None = None, usd_spent: float | None = None,
                 elapsed_s_override: float | None = None) -> dict:
    """Report whether any per-task budget limit is exceeded and which. On
    budget_exceeded, checkpoint cleanly and stop rather than running over."""
    return watchdog_core.check_budget(task_id, turns_used, usd_spent, elapsed_s_override)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
