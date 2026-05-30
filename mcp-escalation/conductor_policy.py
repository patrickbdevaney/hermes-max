"""Conductor invocation policy (Stage 5) — STINGY, classifier-gated.

The driver handles everything locally and reaches up ONLY when justified. This
module is the ADVISOR: given a subtask's signals it returns which ladder rung to
use — it never fires a cloud call itself. The ladder, by subtask type:

  routine (easy/medium)          -> LOCAL (no cloud)
  verifiable + hard              -> parallel_draft (best-of-N free) -> synthesize
  ambiguous  + hard              -> steer (cheap nudge) -> synthesize
  frontier-novel / synth-failed  -> escalate (Opus) ONLY if synth failed verify
                                    twice OR two opinions disagree on a high-blast
                                    -radius change

Every rung is PRESENCE-GATED: an inactive role is skipped and the ladder degrades
(parallel_draft pool off -> synthesize -> local; steer off -> synthesize; synth
off -> local; Opus off -> surface to human/local). Outcomes are recorded to the KG
so the difficulty classifier learns which subtasks needed which tier (the
compounding flywheel), and a frequency report keeps invocation honest.
"""

from __future__ import annotations

import os
from typing import Any

import conductor_core as cc
import conductor_registry as reg
import conductor_resolver as resolver
import escalation_core
from brief_assemble import KG_PORT, _mcp

# honest targets for a real project (the Stage-5 DoD); exceeding them is a signal.
TARGET_SYNTH_PER_PROJECT = int(os.environ.get("CONDUCTOR_TARGET_SYNTH", "15"))
TARGET_OPUS_PER_PROJECT = int(os.environ.get("CONDUCTOR_TARGET_OPUS", "3"))

# RISK-C remedy (Stage-6): a GLOBAL per-subtask budget so one hard subtask can't
# cascade driver→steer→synth→research→synth→escalate, burning money/time before
# any single per-tier trigger fires. When a subtask hits EITHER ceiling we STOP
# escalating and surface to the operator instead of climbing further. Toggle-able.
SUBTASK_USD_CAP = float(os.environ.get("CONDUCTOR_SUBTASK_USD_CAP", "0.50"))
SUBTASK_MAX_TIERS = int(os.environ.get("CONDUCTOR_SUBTASK_MAX_TIERS", "4"))


def subtask_budget_check(tiers_used: int, cost_usd_so_far: float) -> dict[str, Any]:
    """Has THIS subtask exhausted its global cascade budget? Returns stop=True (with
    a reason) when the cumulative tier-count or USD spend ceiling is hit — the
    caller then stops escalating and surfaces to the operator."""
    if cost_usd_so_far >= SUBTASK_USD_CAP:
        return {"stop": True, "reason": f"per-subtask USD cap hit "
                f"(${cost_usd_so_far:.4f} >= ${SUBTASK_USD_CAP}) — stop escalating, surface to operator"}
    if tiers_used >= SUBTASK_MAX_TIERS:
        return {"stop": True, "reason": f"per-subtask tier ceiling hit "
                f"({tiers_used} >= {SUBTASK_MAX_TIERS} tiers) — stop escalating, surface to operator"}
    return {"stop": False, "reason": "within per-subtask budget",
            "tiers_used": tiers_used, "cost_usd_so_far": round(cost_usd_so_far, 4),
            "usd_cap": SUBTASK_USD_CAP, "max_tiers": SUBTASK_MAX_TIERS}


def _active(env: dict[str, str]) -> dict[str, bool]:
    cfg = reg.load_config()
    providers = cfg["providers"]
    roles = resolver.active_roles(cfg["role_chains"], providers, env)
    roles["parallel_draft"] = bool(resolver.resolve_pool(cfg["draft_pool"], providers, env))
    return roles


def plan_invocation(signals: dict | None = None, *, verifiable: bool = False,
                    blast_radius: str | None = None, synth_failures: int = 0,
                    opinions_disagree: bool = False, tiers_used: int = 0,
                    cost_usd_so_far: float = 0.0) -> dict[str, Any]:
    """Advise the ladder rung for a subtask. Returns the chosen tier, the full
    ladder, presence-gated availability, the Opus gate verdict, and the next rung
    to try on failure. NEVER fires a cloud call.

    `tiers_used` / `cost_usd_so_far` track THIS subtask's cascade so far; once the
    global per-subtask budget (RISK-C remedy) is hit, the plan forces local + a
    surface-to-operator note instead of escalating further."""
    cls = escalation_core.classify_difficulty(signals or {})
    difficulty = cls["difficulty"]
    env = dict(os.environ)
    roles = _active(env)

    # RISK-C global per-subtask budget — overrides every per-tier trigger.
    budget = subtask_budget_check(tiers_used, cost_usd_so_far)
    if budget["stop"]:
        return {"ok": True, "difficulty": difficulty, "classifier": cls, "roles_active": roles,
                "tier": "local", "ladder": ["local"], "next_if_fail": None,
                "subtask_budget": budget, "budget_exceeded": True,
                "reason": budget["reason"],
                "note": "STUCK SUMMARY: per-subtask budget exhausted — surfacing to operator "
                        "rather than cascading further"}
    blast = blast_radius or ("high" if (signals or {}).get("cross_module") or
                             int((signals or {}).get("file_count", 0) or 0) >= 4 else "low")

    plan: dict[str, Any] = {"ok": True, "difficulty": difficulty, "classifier": cls,
                            "roles_active": roles, "blast_radius": blast,
                            "verifiable": verifiable}

    if difficulty in ("easy", "medium"):
        plan.update(tier="local", ladder=["local"], next_if_fail=None,
                    reason="routine subtask — stay on the local model; do NOT reach to cloud")
        return plan

    # the Opus gate — the ONLY way to the escalate rung
    opus_allowed = (synth_failures >= 2) or (opinions_disagree and blast == "high")

    ladder = (["parallel_draft", "synthesize", "local"] if verifiable
              else ["steer", "synthesize", "escalate", "local"])

    notes: list[str] = []
    chosen: str | None = None
    for t in ladder:
        if t == "parallel_draft":
            if synth_failures >= 1:
                notes.append("parallel_draft already tried/exhausted -> next")
                continue
            if roles["parallel_draft"]:
                chosen = t
                break
            notes.append("parallel_draft pool OFF (no free keys) -> next")
        elif t == "steer":
            if synth_failures >= 1:
                notes.append("past the steer nudge (already escalated to synthesize) -> next")
                continue
            if roles["steer"]:
                chosen = t
                break
            notes.append("steer role OFF -> next")
        elif t == "synthesize":
            if synth_failures >= 2:
                notes.append("synthesize already failed twice -> the Opus gate")
                continue
            if roles["synth"]:
                chosen = t
                break
            notes.append("synth role OFF -> next")
        elif t == "escalate":
            if not opus_allowed:
                notes.append("Opus gate NOT met (need synth-failed-twice or high-blast disagreement) -> skip")
                continue
            if roles["escalate"]:
                chosen = t
                break
            notes.append("escalate role OFF (no Opus key) -> surface to human/local")
        elif t == "local":
            chosen = "local"
            break

    chosen = chosen or "local"
    try:
        nxt = ladder[ladder.index(chosen) + 1] if chosen in ladder else None
    except IndexError:
        nxt = None
    reason = {
        "parallel_draft": "verifiable+hard — best-of-N across the free pool; verifier selects",
        "steer": "ambiguous+hard — a cheap nudge first; escalate to synthesize if it doesn't unblock",
        "synthesize": "ambiguous+hard — deep brief->directive->verify (steer unavailable or skipped)",
        "escalate": "frontier/synth-failed AND the Opus gate is met — capped Opus escalation",
        "local": "no active cloud rung applies — proceed local-only / surface to human",
    }[chosen]
    plan.update(tier=chosen, ladder=ladder, gate_notes=notes, next_if_fail=nxt,
                opus_allowed=opus_allowed, reason=reason)
    if chosen == "local" and not opus_allowed and not verifiable:
        plan["note"] = ("ambiguous-hard but every cloud rung is off/unmet — write a STUCK SUMMARY "
                        "and proceed local or ping the human")
    return plan


def record_conductor_outcome(subtask: str, tier: str, outcome: str, *,
                             signals: dict | None = None, difficulty: str | None = None,
                             cost_usd: float = 0.0) -> dict[str, Any]:
    """Record a conductor decision+outcome to the KG (compounding flywheel) so the
    classifier learns which subtasks needed which tier. Also forwards to the
    escalation outcomes log. Best-effort; never raises."""
    if not difficulty:
        difficulty = escalation_core.classify_difficulty(signals or {}).get("difficulty")
    name = f"conductor:{subtask[:80]}"
    kg = _mcp(KG_PORT, "record_entity", {"type": "conductor_event", "name": name,
              "props": {"tier": tier, "outcome": outcome, "difficulty": difficulty,
                        "cost_usd": round(cost_usd, 6)}})
    rel = _mcp(KG_PORT, "record_relation", {"a": name, "rel": "resolved_by", "b": tier,
               "props": {"outcome": outcome}})
    log = escalation_core.record_outcome(subtask, signals, difficulty, outcome,
                                         escalated=(tier in ("synthesize", "escalate")), tier=tier)
    return {"ok": True, "kg_entity": bool(kg and kg.get("ok")),
            "kg_relation": bool(rel and rel.get("ok")), "outcomes_logged": log.get("ok", False),
            "recorded": {"subtask": subtask, "tier": tier, "outcome": outcome,
                         "difficulty": difficulty}}


def frequency_report() -> dict[str, Any]:
    """Honest invocation-frequency + cost report for the current project. Pulls
    spend from the conductor ledger and tier counts from the KG conductor_events;
    flags when synth/Opus exceed their targets (brief quality is the bottleneck)."""
    ledger = cc.cost_report()
    counts: dict[str, int] = {}
    q = _mcp(KG_PORT, "query_graph", {"type": "conductor_event", "limit": 500})
    for e in (q or {}).get("entities", []) if q else []:
        tier = (e.get("props") or {}).get("tier", "unknown")
        counts[tier] = counts.get(tier, 0) + 1
    warnings: list[str] = []
    if counts.get("synthesize", 0) > TARGET_SYNTH_PER_PROJECT:
        warnings.append(f"synthesize {counts['synthesize']} > target {TARGET_SYNTH_PER_PROJECT} "
                        "— the brief-assembler quality is likely the bottleneck")
    if counts.get("escalate", 0) > TARGET_OPUS_PER_PROJECT:
        warnings.append(f"Opus escalate {counts['escalate']} > target {TARGET_OPUS_PER_PROJECT} "
                        "— fix the brief quality before spending more on Opus")
    return {"ok": True, "spend": {"today_usd": ledger["spend_today_usd"],
            "month_usd": ledger["spend_month_usd"], "by_provider": ledger["by_provider"],
            "by_role": ledger["by_role"]},
            "tier_counts": counts,
            "targets": {"synthesize": TARGET_SYNTH_PER_PROJECT, "escalate_opus": TARGET_OPUS_PER_PROJECT},
            "kg_available": bool(q),
            "warnings": warnings or ["within targets"]}
