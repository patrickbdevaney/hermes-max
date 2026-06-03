"""mcp-router — outcome-memory + bandit routing as an independent MCP (Phase 2).

Transport: streamable-http on $MCP_ROUTER_PORT (default 9117), path /mcp.
Health:    GET /health.

The cost-asymmetry engine: classify a task, pick a backend (default local-serial-free,
escalate local→fabric→cloud only on positive uplift-per-dollar), log outcomes, learn.
Deterministic-first; degrades to heuristics with the fabric/cloud/profiler down.
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

_REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
for _d in (_REPO, *(os.path.join(_REPO, d) for d in
                    ("mcp-costprofiler", "mcp-verify", "mcp-research"))):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import router_core

PORT = int(os.environ.get("MCP_ROUTER_PORT", "9117"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP("mcp-router", instructions=(
    "Bandit routing over three backends (local-serial/fabric/cloud-deepseek). Call route() "
    "to pick a backend (default local-serial-free; escalate only on positive uplift-per-"
    "dollar) and log_outcome() after a task to close the loop. Deterministic-first."),
    host=HOST, port=PORT, stateless_http=True, json_response=True)


def _threaded(fn):
    @functools.wraps(fn)
    async def _aw(*a, **k):
        return await asyncio.to_thread(fn, *a, **k)
    return _aw


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-router", "port": PORT})


@mcp.tool()
@_threaded
def route(task_text: str, attempt: int = 0, verify_failed: bool = False,
          task_class: str = "") -> dict:
    """Pick a backend + escalation decision. Default local-serial-free single attempt;
    escalate local→fabric→cloud-deepseek only when warranted AND the predicted uplift-per-
    dollar clears the floor. Never fans blindly onto local. Returns
    {task_class, backend, escalate, reason, difficulty, critical}."""
    return router_core.route(task_text, attempt, verify_failed, task_class or None)


@mcp.tool()
@_threaded
def classify(task_text: str, path: str = "") -> dict:
    """Criticality/difficulty classifier: {critical, predicted_difficulty,
    recommended_backend, escalate, task_class}. Deterministic rules first."""
    return router_core.classify(task_text, path or None)


@mcp.tool()
@_threaded
def log_outcome(task_class: str, backend: str, solved: bool, cost_usd: float = 0.0,
                wall_ms: int = 0, failure_class: str = "", note: str = "") -> dict:
    """Record a task outcome (profiler reads it) and update the bandit. The enforced write
    that closes the loop between outcomes and routing."""
    return router_core.log_outcome(task_class, backend, solved, cost_usd, wall_ms,
                                   failure_class, note)


@mcp.tool()
@_threaded
def accuracy_cost_table(task_class: str = "") -> dict:
    """Per-(task-class, backend) pass-rate + avg cost from the outcome log."""
    return router_core.accuracy_cost_table(task_class or None)


@mcp.tool()
@_threaded
def bandit_scores(task_class: str) -> dict:
    """UCB1 score per backend for a task class (unvisited arms force exploration)."""
    return {"task_class": task_class, "scores": router_core.bandit_scores(task_class)}


@mcp.tool()
@_threaded
def recall_notes(task_class: str, n: int = 3) -> dict:
    """Reflexion-style episodic notes for a task class ('on tasks like this, Y worked')."""
    return {"task_class": task_class, "notes": router_core.recall_notes(task_class, n)}


@mcp.tool()
@_threaded
def router_stats() -> dict:
    """Report the router config + backend arms + persistence paths."""
    return router_core.router_stats()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
