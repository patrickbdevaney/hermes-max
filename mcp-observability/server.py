"""mcp-observability — OpenTelemetry traces/metrics to Phoenix as an MCP server.

Transport: streamable-http on $MCP_OBSERVABILITY_PORT (default 9104), path /mcp.
Health:    GET /health (LIVENESS — fast, no upstream calls, the UP/DOWN signal).
           GET /ready  (READINESS — Phoenix reachability; informational).

Independent process. If killed, Hermes reports the tools unavailable; nothing
else is affected. If Phoenix itself is down, recording still succeeds locally
(spans are dropped on export) — never blocks the agent.
"""

from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import observability_core

# scripts/ holds the self-contained, stdlib-only condenser (M-Stage 2) — import its
# condense() directly so the tool and the CLI share one implementation.
import sys as _sys
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in _sys.path:
    _sys.path.insert(0, _SCRIPTS)
try:
    import condenser as _condenser
except Exception:  # noqa: BLE001
    _condenser = None

# lib/livelog writes the operator-facing live.jsonl span stream. The observability
# server's own emitter targets Phoenix/OTLP, so for spans that must show in the live
# log (skill_fired) we write through livelog directly too.
_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
if _LIB not in _sys.path:
    _sys.path.insert(0, _LIB)
try:
    import livelog as _livelog
except Exception:  # noqa: BLE001
    _livelog = None

PORT = int(os.environ.get("MCP_OBSERVABILITY_PORT", "9104"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-observability",
    instructions=(
        "Emit OpenTelemetry traces/metrics to Phoenix. Record task metrics "
        "(tokens, time, verify pass, retrieval precision, skill reuse, "
        "escalation spend, loop stalls) so the operator can tune the system."
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
async def health(request: Request) -> JSONResponse:
    """LIVENESS — process up + HTTP answering, returned immediately with NO
    upstream calls (sub-10ms). The UP/DOWN signal: a live span-emitter must NEVER
    show DOWN because Phoenix is unreachable (spans just drop on export, the agent
    is never blocked). Phoenix reachability moved to /ready. `?deep=1` forwards."""
    if request.query_params.get("deep", "").lower() in ("1", "true", "yes"):
        return await ready(request)
    return JSONResponse({"status": "ok", "server": "mcp-observability", "port": PORT})


@mcp.custom_route("/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    """READINESS — informational: exporter config + a live Phoenix-reachability
    TCP probe. A down Phoenix is a WARNING here, never DOWN; recording still
    succeeds locally and spans drop silently on export."""
    return JSONResponse({"status": "ok", "server": "mcp-observability", "port": PORT,
                         **observability_core.status()})


@mcp.tool()
@_threaded
def record_trace(name: str, attributes: dict | None = None, status: str = "ok",
                 duration_ms: float | None = None) -> dict:
    """Emit one OTel span named `name` with arbitrary attributes to Phoenix."""
    return observability_core.record_trace(name, attributes, status, duration_ms)


@mcp.tool()
@_threaded
def record_metric(name: str, value: float, unit: str = "", attributes: dict | None = None) -> dict:
    """Emit a numeric metric (modeled as a `metric:<name>` span) to Phoenix."""
    return observability_core.record_metric(name, value, unit, attributes)


@mcp.tool()
@_threaded
def record_task_metrics(
    task_id: str,
    tokens: int | None = None,
    duration_ms: float | None = None,
    verify_passed: bool | None = None,
    retrieval_precision: float | None = None,
    skill_reused: bool | None = None,
    escalation_usd: float | None = None,
    loop_stalled: bool | None = None,
    attributes: dict | None = None,
) -> dict:
    """Emit one span capturing the standard per-task observability surfaces."""
    return observability_core.record_task_metrics(
        task_id, tokens, duration_ms, verify_passed, retrieval_precision,
        skill_reused, escalation_usd, loop_stalled, attributes,
    )


@mcp.tool()
@_threaded
def record_skill_fired(skill_name: str, trigger: str | None = None) -> dict:
    """Record that a workflow skill activated (M-Stage 7 skill-firing instrumentation).
    Emits a skill_fired span {skill_name, trigger} to live.jsonl + Phoenix so
    under-firing skills can be identified (Anthropic: 'measure trigger reliability
    rather than assume it'). Skills self-report by calling this as their first action."""
    if _livelog is not None:
        try:
            _livelog.forward("skill_fired", {"skill_name": skill_name, "trigger": trigger}, "ok")
        except Exception:  # noqa: BLE001
            pass
    return observability_core.record_trace("skill_fired",
                                            {"skill_name": skill_name, "trigger": trigger})


@mcp.tool()
@_threaded
def condense_context(history: list, threshold_ratio: float = 0.80, keep_first: int = 4,
                     condense_oldest_ratio: float = 0.40, force: bool = False) -> dict:
    """Condense a long conversation when it nears the context limit (M-Stage 2,
    OpenHands LLMSummarizingCondenser). `history` is a list of {role,content}
    messages. At >= threshold_ratio of the model's max_model_len it summarizes the
    OLDEST condense_oldest_ratio of the middle (always preserving the first
    keep_first and the most recent turns) via the local model, and returns
    {fired, history, tokens_before, tokens_after, ratio, ...}; under threshold it
    returns the history unchanged (fired=false). Emits a condenser_fired span.

    CAVEAT: condensing rewrites the prompt PREFIX → lower prompt-cache hit rate.
    Only condense when genuinely near the limit, NOT as a routine optimization."""
    if _condenser is None:
        return {"ok": False, "fired": False, "error": "condenser module unavailable", "history": history}
    out = _condenser.condense(history, threshold_ratio=threshold_ratio, keep_first=keep_first,
                              condense_oldest_ratio=condense_oldest_ratio, force=force)
    out["ok"] = True
    return out


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
