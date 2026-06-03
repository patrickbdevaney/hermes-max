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
                    ("mcp-costprofiler", "mcp-verify", "mcp-research", "mcp-search",
                     "mcp-escalation"))):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import committee_core
import dag_core
import dispatch_core
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
def dispatch_target(n: int = 3, verify_failed: bool = False) -> dict:
    """Where should a fan-out of `n` branches land? Honors the asymmetry: fabric (free,
    parallel) → cloud (paid, parallel) → local (serial, bounded N≤3, verify-fail only).
    Never a blind N-way fan-out onto the single-stream local executor."""
    return dispatch_core.target_for(n, verify_failed)


@mcp.tool()
@_threaded
def best_of_n(task_spec: str, tests: dict, target_path: str = "solution.py",
              language: str = "python", n: int = 3, verify_failed: bool = False,
              critical: bool = False, base_files: dict | None = None) -> dict:
    """Criticality-gated, EXECUTION-verified best-of-N (Phase 3). OFF unless gated on (a
    verify-failure or a critical/high-value task). Drafts N candidates through the parallelism
    dispatcher (fabric→cloud, never a blind local fan-out) and selects by the verify oracle —
    never self-judgment. Requires `tests` as the execution oracle. Logs the outcome."""
    return dispatch_core.best_of_n(task_spec, tests, target_path, language, n,
                                   verify_failed, critical, base_files)


@mcp.tool()
@_threaded
def dispatch_stats() -> dict:
    """Report fabric/cloud availability + the local-serial fan-out rule."""
    return dispatch_core.dispatch_stats()


@mcp.tool()
@_threaded
def dag_schedule(steps: list, done: list | None = None, repo_path: str = "") -> dict:
    """Phase 5 — given parsed PLAN.md steps (each may carry depends_on + files), compute the
    next wave of independent ready nodes, decide parallel (off-local) vs context-isolated-
    but-serial (local) via the dispatcher, and flag merge conflicts (overlapping files).
    Independent nodes parallelize ONLY off-local; on local the benefit is context isolation,
    not wall-clock. Returns {wave, backend, parallel, conflicts, note}."""
    parsed = dag_core.parse_dag(steps)
    return dag_core.schedule(parsed["nodes"], done or [], repo_path)


@mcp.tool()
@_threaded
def dag_stats() -> dict:
    """Report the DAG scheduling rule (local = context-isolated serial, not parallel)."""
    return dag_core.dag_stats()


@mcp.tool()
@_threaded
def committee_plan(task: str, n: int = 3, repo_map: str = "", critical: bool = False,
                   task_class: str = "plan") -> dict:
    """Phase 7 — gated committee planning for a high-consequence planning decision. OFF unless
    critical=True. Fans 2-3 plan drafts across cloud-deepseek (V4 Pro) + fabric ONLY (never
    serialized on local), scores by structural well-formedness × the Phase-2 backend accuracy
    weight (oracle-scored where a proposed gate is checkable), returns the winning plan."""
    return committee_core.committee_plan(task, n, repo_map, critical, task_class)


@mcp.tool()
@_threaded
def committee_stats() -> dict:
    """Report committee config (OFF by default; cloud/fabric-only fan-out, never local)."""
    return committee_core.committee_stats()


@mcp.tool()
@_threaded
def router_stats() -> dict:
    """Report the router config + backend arms + persistence paths."""
    return router_core.router_stats()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
