"""mcp-costprofiler — per-step cost/latency/backend attribution as an independent MCP.

Transport: streamable-http on $MCP_COSTPROFILER_PORT (default 9116), path /mcp.
Health:    GET /health.

Consumes the accounting the system already emits (the lib/inference ledger) + an executor-
call log, and answers the cost-asymmetry queries the conductor and the bandit router read.
Deterministic, no LLM. If it dies, the conductor's enforced attribution degrades to a no-op.
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

# repo root on path so profiler_core can reach lib/inference for the tier fallback.
_REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import cost_profiler
import profiler_core

PORT = int(os.environ.get("MCP_COSTPROFILER_PORT", "9116"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP("mcp-costprofiler", instructions=(
    "Cost/latency/backend attribution. Query profiler_report for per-backend spend + "
    "wall-clock, cost_per_solved_task and uplift_per_dollar to justify any ratio-risky "
    "toggle. Deterministic; no model calls."), host=HOST, port=PORT,
    stateless_http=True, json_response=True)


def _threaded(fn):
    @functools.wraps(fn)
    async def _aw(*a, **k):
        return await asyncio.to_thread(fn, *a, **k)
    return _aw


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-costprofiler", "port": PORT})


@mcp.tool()
@_threaded
def profiler_report(window: str = "today", task_class: str = "") -> dict:
    """Per-backend rollup of calls/tokens/$/wall-clock (p50,p95) over the window
    (today|7d|all). The per-backend wall-clock quantifies the serial-local penalty."""
    return profiler_core.report(window, task_class or None)


@mcp.tool()
@_threaded
def cost_per_solved_task(task_class: str = "", window: str = "all") -> dict:
    """Total spend / number of SOLVED tasks per task class — the honest denominator for
    'did this toggle pay'. Reads the outcome log the bandit router writes."""
    return profiler_core.cost_per_solved_task(task_class or None, window)


@mcp.tool()
@_threaded
def uplift_per_dollar(task_class: str, window: str = "all") -> dict:
    """Per-backend pass-rate + $/attempt with uplift-per-dollar vs the local-serial baseline.
    The number the escalation gate reads to decide whether paid parallel fan-out pays."""
    return profiler_core.uplift_per_dollar(task_class, window)


@mcp.tool()
@_threaded
def log_executor_call(task_class: str = "", in_tok: int = 0, out_tok: int = 0,
                      wall_ms: int = 0, provider: str = "local_vllm", model: str = "") -> dict:
    """Record a local-EXECUTOR call (the external hermes loop bypasses the lib/inference
    ledger). Backend is inferred from the provider; cost is ~0 for local. Used by the
    conductor's enforced post_llm_call attribution."""
    backend = profiler_core.backend_of(provider, model)
    return profiler_core.log_call(backend, task_class, in_tok, out_tok, 0.0, wall_ms,
                                  provider, model, source="executor")


@mcp.tool()
@_threaded
def record_call(run_id: str, provider: str, model: str = "", backend: str = "",
                tokens_in: int = 0, tokens_out: int = 0, tokens_cached: int = 0,
                cost_usd: float | None = None, wall_clock_s: float = 0.0) -> dict:
    """Safeguard 1 — record one LLM call into the SQLite cost ledger (~/.hermes-max/cost.db).
    backend is inferred from provider/model if blank; cost is computed from the rate table
    when not supplied. The append-only source of truth for the spend cap + ratio alert."""
    return cost_profiler.record_call(run_id, provider, model, backend, tokens_in, tokens_out,
                                     tokens_cached, cost_usd, wall_clock_s)


@mcp.tool()
@_threaded
def cost_summary(run_id: str) -> dict:
    """Safeguard 1 — per-run cost breakdown {total_usd, by_backend:{local,fabric,cloud},
    call_count} from the SQLite ledger."""
    return cost_profiler.cost_summary(run_id)


@mcp.tool()
@_threaded
def ratio_check() -> dict:
    """Safeguard 1/3 — 7-day rolling {cost_per_task_7d, cloud_fraction_7d, alert, reason}.
    alert=True if cloud_fraction>0.40 or cost_per_task_7d>$0.15. Observability only."""
    return cost_profiler.ratio_check()


@mcp.tool()
@_threaded
def cost_profiler_stats() -> dict:
    """Report the SQLite ledger path, ratio.log path, rate table, and alert thresholds."""
    return cost_profiler.cost_profiler_stats()


@mcp.tool()
@_threaded
def profiler_stats() -> dict:
    """Report the profiler's log paths and the backend taxonomy."""
    return profiler_core.profiler_stats()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
