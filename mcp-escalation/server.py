"""mcp-escalation — cloud model router with a hard daily USD cap. OFF by default.

Transport: streamable-http on $MCP_ESCALATION_PORT (default 9105), path /mcp.
Health:    GET /health (reports enabled state + today's spend vs cap).

Independent process. If killed, Hermes reports the tool unavailable and the
agent stays on the free local model — which is the default behavior anyway.
"""

from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import brief_assemble as brief
import conductor_core
import conductor_policy
import directive_verify as dv
import escalation_core
import frontier_core

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
    return JSONResponse({"status": "ok", "server": "mcp-escalation", "port": PORT,
                         **escalation_core.status()})


@mcp.tool()
@_threaded
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
@_threaded
def classify_difficulty(signals: dict | None = None) -> dict:
    """Tag a task/subtask easy/medium/hard from cheap signals (file_count,
    novelty, prior_failures, lines_changed, cross_module). This is the SHARED
    difficulty signal — gate Stage-1 search N, Stage-2 verify depth, and Stage-3
    escalation off this one tag."""
    return escalation_core.classify_difficulty(signals)


@mcp.tool()
@_threaded
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
@_threaded
def should_escalate(signals: dict | None = None) -> dict:
    """Auto-trigger check: escalate when verifier-guided search exhausted N
    without green, OR backtracking exhausted approaches, OR confidence is low on
    an irreversible/high-stakes change."""
    return escalation_core.should_escalate(signals)


@mcp.tool()
@_threaded
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
@_threaded
def conductor_steer(prompt: str, max_tokens: int | None = None) -> dict:
    """Get a CHEAP cloud NUDGE on an ambiguous-but-not-deep blocker. Walks the
    steer chain (default: DeepSeek-V4-Flash@DeepInfra -> Cerebras -> Groq ->
    Gemini), first present rung wins. Returns {ok, provider, content, cost_usd} or
    {proceed_local:True} if steer is OFF/failed. Never raises. Pass a compact
    brief (Stage-2 'compact' profile), not raw scrollback."""
    return conductor_core.run_role("steer", prompt=prompt, max_tokens=max_tokens)


@mcp.tool()
@_threaded
def conductor_synthesize(prompt: str, max_tokens: int | None = None) -> dict:
    """Get a DEEP decomposition / novel-architecture directive on a genuinely-hard,
    AMBIGUOUS blocker (no cheap verifiable oracle). Walks the synth chain (default:
    DeepInfra V4-Pro -> Fireworks -> Together -> DeepSeek -> Kimi -> Opus),
    US-hosted-first, first present rung wins, silent fall-with-log. Returns a
    structured directive in `content` or {proceed_local:True}. Stateless: pass the
    Stage-2 'full' brief; the cloud returns a directive, the LOCAL model executes."""
    return conductor_core.run_role("synth", prompt=prompt, max_tokens=max_tokens)


@mcp.tool()
@_threaded
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
@_threaded
def conductor_status() -> dict:
    """Report which conductor ROLES are active (>=1 present key), the resolved
    present chain per role, the present draft-pool members, the USD caps + today's
    /this-month's spend, whether conductor.yaml overrode defaults, and recent
    silent rung-falls. The at-a-glance 'what cloud help is on' view."""
    return conductor_core.status()


@mcp.tool()
@_threaded
def conductor_cost_report() -> dict:
    """Per-day + per-month conductor spend, broken down by provider and by role,
    plus call count — the honest cost ledger feeding the Stage-5 report."""
    return conductor_core.cost_report()


# ── frontier tier — SPARING Opus 4.8 escalation (--frontier mode + key only) ──
@mcp.tool()
@_threaded
def frontier_escalate(task: str, signals: dict | None = None, context: dict | None = None,
                      repo: str | None = None, task_id: str | None = None,
                      synth_failures: int = 0, opinions_disagree: bool = False,
                      blast_radius: str | None = None,
                      compressed_brief: str | None = None) -> dict:
    """Escalate a genuinely FRONTIER-NOVEL, twice-failed subtask to Opus 4.8 via
    COMPRESS-THEN-REASON, behind THREE gates (ALL must trip): (1) CONDUCTOR_MODE=
    frontier + ANTHROPIC_API_KEY present; (2) the classifier flags the subtask
    frontier-novel (blue-ocean — pass signals with novelty='high' and
    blue_ocean=true / no_reference_impl=true; merely-HARD stays at V4-Pro); (3)
    V4-Pro synth has ALREADY failed verify twice (pass synth_failures>=2) OR two
    opinions disagree on a high-blast change (opinions_disagree=true). When all
    trip: V4-Pro compresses the situation into a dense ~12K brief, Opus reasons on
    it (~$0.18), the plan is written to FRONTIER_PLAN.md + RAG/KG with provenance
    and passed through directive_verify (advisory). A hard frontier USD cap blocks
    + falls back to V4-Pro. Returns opus_invoked + which gate failed if not. Never
    raises; degrades to V4-Pro/local. Opus is RARE by design — keep it that way."""
    return frontier_core.frontier_escalate(
        task, signals=signals, context=context, repo=repo, task_id=task_id,
        synth_failures=synth_failures, opinions_disagree=opinions_disagree,
        blast_radius=blast_radius, compressed_brief=compressed_brief)


@mcp.tool()
@_threaded
def frontier_status() -> dict:
    """Frontier-tier state: active mode, whether Opus is eligible (frontier mode +
    key), month-to-date Opus call count + spend vs the daily/monthly USD cap and
    the sparing target (≤15 calls/mo), and whether the cap is blocking."""
    return frontier_core.frontier_status()


# ── brief-assembler (Stage 2) ─────────────────────────────────────────────────
@mcp.tool()
@_threaded
def brief_assemble(task_id: str, current_blocker: str, decision_needed: str,
                   profile: str = "full", repo: str | None = None,
                   query: str | None = None, directives: str | None = None,
                   acceptance_tests: list[str] | None = None) -> dict:
    """DETERMINISTICALLY assemble a structured cloud brief from harness state.
    You (the local model) write ONLY `current_blocker` and `decision_needed`;
    goal/done/constraints/success come from PLAN.md, architecture_state +
    failed_approaches from the KG + watchdog, and token-budgeted code_excerpts
    from codebase-rag — so the WEAK local model never hand-writes the brief.
    profile: 'compact' (steer, <=8K tok) | 'full' (synth, 15-30K) | 'draft'
    (parallel_draft — pass acceptance_tests, the objective oracle). Feed the
    returned brief to conductor_steer/synthesize/parallel_draft_pool. Degrades
    gracefully (missing servers/PLAN.md -> empty sections); never raises."""
    return brief.brief_assemble(task_id, current_blocker, decision_needed,
                                profile=profile, repo=repo, query=query,
                                directives=directives, acceptance_tests=acceptance_tests)


@mcp.tool()
@_threaded
def brief_request_more(task_id: str, section: str, query: str = "", k: int = 8,
                       offset: int = 0, repo: str | None = None) -> dict:
    """Progressive disclosure: pull MORE of a section the brief capped, when the
    cloud asks for it. section: 'code_excerpts' | 'failed_approaches' |
    'architecture_state'."""
    return brief.brief_request_more(task_id, section, query=query, k=k,
                                    offset=offset, repo=repo)


# ── advisory-with-verify-gate authority (Stage 3) ─────────────────────────────
@mcp.tool()
@_threaded
def directive_verify(directive: dict, repo: str | None = None, task_id: str | None = None,
                     second_directive: dict | None = None, run_static: bool = True) -> dict:
    """GATE a cloud directive BEFORE executing it — the cloud is smart but BLIND.
    (1) verify each `assumptions` entry vs ACTUAL repo state (a false one rejects
    the directive and is recorded as a failed_approach); (2) check `apis_to_use`
    exist + repo baseline (verify.quick_check); (3) require concrete
    `tests_to_write`; (4) on low-confidence + high-blast-radius demand a second
    synth opinion (pass `second_directive` to compare — disagreement -> escalate/
    human). Returns `execute` (bool) + per-gate detail. Only execute + checkpoint
    when execute is True. Deterministic; degrades if verify/KG are down."""
    return dv.directive_verify(directive, repo=repo, task_id=task_id,
                               second_directive=second_directive, run_static=run_static)


@mcp.tool()
@_threaded
def compare_directives(a: dict, b: dict) -> dict:
    """Cheap agreement check between two synth opinions (file-set overlap +
    first-step similarity). agree=False => escalate to Opus or surface to human."""
    return dv.compare_directives(a, b)


# ── invocation policy (Stage 5) ───────────────────────────────────────────────
@mcp.tool()
@_threaded
def conductor_plan(signals: dict | None = None, verifiable: bool = False,
                   blast_radius: str | None = None, synth_failures: int = 0,
                   opinions_disagree: bool = False) -> dict:
    """ADVISE which ladder rung a subtask should use (does NOT fire a cloud call).
    Routine (easy/medium) -> LOCAL. verifiable+hard -> parallel_draft -> synthesize.
    ambiguous+hard -> steer -> synthesize. Opus escalate ONLY if synth_failures>=2
    or (opinions_disagree AND high blast). Every rung is presence-gated and the
    ladder degrades when a role is OFF. Pass the cheap `signals` (file_count,
    novelty, prior_failures, lines_changed, cross_module) and whether the subtask
    has an objective test oracle (`verifiable`). Then call the returned tier's tool
    (parallel_draft / conductor_steer / conductor_synthesize / escalate), gate with
    directive_verify, and record the outcome with conductor_record_outcome."""
    return conductor_policy.plan_invocation(signals, verifiable=verifiable,
                                            blast_radius=blast_radius,
                                            synth_failures=synth_failures,
                                            opinions_disagree=opinions_disagree)


@mcp.tool()
@_threaded
def conductor_record_outcome(subtask: str, tier: str, outcome: str,
                             signals: dict | None = None, difficulty: str | None = None,
                             cost_usd: float = 0.0) -> dict:
    """Record a conductor decision+outcome to the KG (the compounding flywheel) so
    the difficulty classifier learns which subtasks needed which tier. Call at
    subtask end. tier: local|parallel_draft|steer|synthesize|escalate. outcome:
    e.g. 'verified'|'failed'|'reverted'. Best-effort; never blocks."""
    return conductor_policy.record_conductor_outcome(subtask, tier, outcome,
                                                     signals=signals, difficulty=difficulty,
                                                     cost_usd=cost_usd)


@mcp.tool()
@_threaded
def conductor_frequency_report() -> dict:
    """Honest invocation-frequency + cost report: spend by provider/role (ledger)
    + tier counts (KG), checked against the per-project targets (synth<=15,
    Opus<=3). A breach means brief quality is the bottleneck — fix the assembler."""
    return conductor_policy.frequency_report()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
