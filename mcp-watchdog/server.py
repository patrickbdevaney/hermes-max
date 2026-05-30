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
        "Deterministic self-check signals for never-getting-stuck. Call "
        "check_spiral on recent reasoning to catch a thinking loop; check_stall "
        "ONCE on a long-running tool call (it will NOT report a heartbeating "
        "process as hung); check_progress every few subtasks to catch a stall; "
        "and start_task_budget/check_budget for per-task wall-clock/turns/USD "
        "limits. On spiral_detected / no_progress / hung / budget_exceeded, write "
        "a STUCK SUMMARY and call revert_to_last_green + replan."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-watchdog", "port": PORT,
                         **watchdog_core.status()})


@mcp.tool()
def check_spiral(recent_thinking_text: str, ngram: int | None = None) -> dict:
    """Detect a reasoning/CoT spiral in supplied recent reasoning text.

    Model-free: combines repeated n-gram ratio, LZ compressibility, and
    consecutive-segment similarity. Returns spiral_detected + reason + metrics.
    Call it when a turn's reasoning is getting long or circular; on True, stop
    thinking, write a STUCK SUMMARY, and replan / revert instead of looping.
    """
    return watchdog_core.check_spiral(recent_thinking_text, ngram)


@mcp.tool()
def check_stall(tool_name: str, elapsed_s: float, expecting_heartbeat: bool = False,
                last_heartbeat_age_s: float | None = None,
                per_tool_budget_s: float | None = None) -> dict:
    """Decide if an in-flight tool call is HUNG vs legitimately WAITING.

    Hung only if it exceeded its per-tool budget AND is silent. A heartbeating
    process (recent last_heartbeat_age_s) is WAITING — never killed. Call this
    ONCE on a backgrounded long-running process instead of polling it to
    completion (the poll-hang fix).
    """
    return watchdog_core.check_stall(tool_name, elapsed_s, expecting_heartbeat,
                                     last_heartbeat_age_s, per_tool_budget_s)


@mcp.tool()
def check_progress(task_id: str, signals: dict | None = None, n: int = 3) -> dict:
    """Progress-delta since the last call for this task. signals carries
    monotonic observables {files_touched, tests_passing, checkpoints, turn}.
    Flags no_progress=True after n consecutive calls with zero forward delta."""
    return watchdog_core.check_progress(task_id, signals, n)


@mcp.tool()
def start_task_budget(task_id: str, wall_clock_s: float | None = None,
                      max_turns: int | None = None, usd_cap: float | None = None) -> dict:
    """Begin per-task budget tracking (wall-clock seconds / turns / USD). Any
    limit left None is unbounded. Pair with check_budget."""
    return watchdog_core.start_task_budget(task_id, wall_clock_s, max_turns, usd_cap)


@mcp.tool()
def check_budget(task_id: str, turns_used: int | None = None, usd_spent: float | None = None,
                 elapsed_s_override: float | None = None) -> dict:
    """Report whether any per-task budget limit is exceeded and which. On
    budget_exceeded, checkpoint cleanly and stop rather than running over."""
    return watchdog_core.check_budget(task_id, turns_used, usd_spent, elapsed_s_override)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
