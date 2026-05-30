"""mcp-escalation — cloud model router with a hard daily USD cap. OFF by default.

Transport: streamable-http on $MCP_ESCALATION_PORT (default 9105), path /mcp.
Health:    GET /health (reports enabled state + today's spend vs cap).

Independent process. If killed, Hermes reports the tool unavailable and the
agent stays on the free local model — which is the default behavior anyway.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import conductor_core
import escalation_core

PORT = int(os.environ.get("MCP_ESCALATION_PORT", "9105"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-escalation",
    instructions=(
        "Escalate ONLY genuinely-hard, well-scoped subproblems to a cheap cloud "
        "tier. OFF by default; a hard daily USD cap is enforced server-side. "
        "Never for routine work; never for Tier-3 (Opus/Claude Code)."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-escalation", "port": PORT,
                         **escalation_core.status()})


@mcp.tool()
def escalate(task: str, tier: str = "cheap", context: dict | None = None) -> dict:
    """Route a hard, self-contained subproblem to an escalation tier.

    tier: "local" (a bigger LOCAL model — FREE, always on when configured),
    "cheap"/"long" (cloud — OFF by default, hard USD-capped). `context` carries
    the SURGICAL HANDOFF: pass {plan, diffs, failure_traces} (the full 0.5 state
    snapshot, not a lossy summary). Returns the result + cost + today's spend.
    Returns a disabled/cap-reached marker (never raises) on a gated cloud call;
    Tier-3 (Opus/Claude Code) is rejected by design.
    """
    return escalation_core.escalate(task, tier, context)


@mcp.tool()
def classify_difficulty(signals: dict | None = None) -> dict:
    """Tag a task/subtask easy/medium/hard from cheap signals (file_count,
    novelty, prior_failures, lines_changed, cross_module). This is the SHARED
    difficulty signal — gate Stage-1 search N, Stage-2 verify depth, and Stage-3
    escalation off this one tag."""
    return escalation_core.classify_difficulty(signals)


@mcp.tool()
def record_outcome(task: str, signals: dict | None = None, difficulty: str | None = None,
                   outcome: str = "unknown", escalated: bool = False,
                   tier: str | None = None) -> dict:
    """Record a finished task's (signals → difficulty → outcome) as a labelled
    example for the weekly GEPA run. Call it at task end — especially when a task
    escalated and the higher tier solved it — so the difficulty classifier learns
    from real outcomes and the local model handles more over time (the compounding
    flywheel). Best-effort; never blocks."""
    return escalation_core.record_outcome(task, signals, difficulty, outcome, escalated, tier)


@mcp.tool()
def should_escalate(signals: dict | None = None) -> dict:
    """Auto-trigger check: escalate when verifier-guided search exhausted N
    without green, OR backtracking exhausted approaches, OR confidence is low on
    an irreversible/high-stakes change."""
    return escalation_core.should_escalate(signals)


@mcp.tool()
def route(task: str, difficulty: str | None = None, signals: dict | None = None,
          context: dict | None = None) -> dict:
    """Tiered routing for a hard kernel: easy/medium stay on the primary local
    model; hard tries the FREE local escalation tier FIRST, then a cloud tier
    only if local is unavailable/failed (and cloud is enabled + under cap). Pass
    `context` for the surgical handoff."""
    return escalation_core.route(task, difficulty, signals, context)


# ── conductor (optional, presence-gated) ─────────────────────────────────────
# Cloud help as STATELESS TOOLS, never a backend swap. Each tool walks a per-role
# provider chain, uses the first PRESENT rung, silently falls-with-log on failure,
# and returns a graceful proceed_local signal when a role is OFF or capped. With
# no cloud keys set these all return proceed_local and the driver stays local.
@mcp.tool()
def conductor_steer(prompt: str, max_tokens: int | None = None) -> dict:
    """Get a CHEAP cloud NUDGE on an ambiguous-but-not-deep blocker. Walks the
    steer chain (default: DeepSeek-V4-Flash@DeepInfra -> Cerebras -> Groq ->
    Gemini), first present rung wins. Returns {ok, provider, content, cost_usd} or
    {proceed_local:True} if steer is OFF/failed. Never raises. Pass a compact
    brief (Stage-2 'compact' profile), not raw scrollback."""
    return conductor_core.run_role("steer", prompt=prompt, max_tokens=max_tokens)


@mcp.tool()
def conductor_synthesize(prompt: str, max_tokens: int | None = None) -> dict:
    """Get a DEEP decomposition / novel-architecture directive on a genuinely-hard,
    AMBIGUOUS blocker (no cheap verifiable oracle). Walks the synth chain (default:
    DeepInfra V4-Pro -> Fireworks -> Together -> DeepSeek -> Kimi -> Opus),
    US-hosted-first, first present rung wins, silent fall-with-log. Returns a
    structured directive in `content` or {proceed_local:True}. Stateless: pass the
    Stage-2 'full' brief; the cloud returns a directive, the LOCAL model executes."""
    return conductor_core.run_role("synth", prompt=prompt, max_tokens=max_tokens)


@mcp.tool()
def parallel_draft_pool(prompt: str, n: int | None = None,
                        max_tokens: int | None = None) -> dict:
    """Fan a draft brief out across the FREE/cheap parallel_draft POOL concurrently
    (Cerebras GLM + gpt-oss, Groq gpt-oss + qwen3 + llama-4, + optional DeepSeek
    V4-Flash anchor) for cross-family DIVERSITY, respecting each provider's live
    RPM/RPD budget (exhausted sources skipped). Returns the RAW candidates; the
    deterministic VERIFIER selects the winner (use mcp-search's verifier-guided
    selection — Stage 4 — on VERIFIABLE subtasks only). Degrades to N=1-local with
    zero keys. Never raises."""
    return conductor_core.draft_fanout(prompt=prompt, n=n, max_tokens=max_tokens)


@mcp.tool()
def conductor_status() -> dict:
    """Report which conductor ROLES are active (>=1 present key), the resolved
    present chain per role, the present draft-pool members, the USD caps + today's
    /this-month's spend, whether conductor.yaml overrode defaults, and recent
    silent rung-falls. The at-a-glance 'what cloud help is on' view."""
    return conductor_core.status()


@mcp.tool()
def conductor_cost_report() -> dict:
    """Per-day + per-month conductor spend, broken down by provider and by role,
    plus call count — the honest cost ledger feeding the Stage-5 report."""
    return conductor_core.cost_report()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
