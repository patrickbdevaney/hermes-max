"""Thin cloud router for escalating genuinely-hard, well-scoped subproblems.

Two non-negotiables, both enforced HERE in the server (not in a prompt):
  1. Default OFF. ESCALATION_ENABLED must be explicitly "true" to route anything.
  2. Hard daily USD cap. Spend is tracked in a state file and reset each day;
     once today's spend reaches the cap, escalate refuses — and a per-call
     max_tokens bounds any single call so it can't blow the cap in one shot.

Tier-3 (Opus / Claude Code) is intentionally NOT routable here — those tier
names are rejected — to avoid auth collisions with the laptop's Claude Code.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date
from pathlib import Path
from typing import Any

import httpx

ENABLED = os.environ.get("ESCALATION_ENABLED", "false").strip().lower() == "true"
DAILY_USD_CAP = float(os.environ.get("ESCALATION_DAILY_USD_CAP", "1.00"))
MAX_TOKENS = int(os.environ.get("ESCALATION_MAX_TOKENS", "2048"))
# Empty-base correctness (Stage-6): with NO signals gathered the classifier is
# UNCERTAIN, so default conservative (medium) instead of "easy". Toggle-able.
ESCALATE_WHEN_UNCERTAIN = os.environ.get(
    "CLASSIFIER_ESCALATE_WHEN_UNCERTAIN", "true").strip().lower() in ("1", "true", "yes", "on")
TIMEOUT = float(os.environ.get("ESCALATION_TIMEOUT", "120"))
STATE_PATH = os.path.expanduser(
    os.environ.get("ESCALATION_STATE_PATH", "~/.hermes-max/escalation/spend.json")
)
# The compounding flywheel: every escalation+outcome is appended here as a
# labelled example; dspy-evolution/traces.py reads it to improve classify_difficulty
# on the operator's OWN tasks, so the local model handles progressively more of the
# formerly-escalated band over time.
OUTCOMES_LOG = os.path.expanduser(
    os.environ.get("ESCALATION_OUTCOMES_LOG", "~/.hermes-max/escalation/outcomes.jsonl")
)

# Tier-3 must never be routed through this server.
FORBIDDEN_TIERS = {"opus", "claude", "claude-code", "claude_code", "tier3", "tier-3", "tier_3"}

_lock = threading.Lock()


def _otel(name: str, attrs: dict) -> None:
    """Best-effort OTel span (escalated/route events). Never raises."""
    try:
        import otel_emit

        otel_emit.record(name, attrs, status="ok")
    except Exception:  # noqa: BLE001 - observability is optional
        pass


def _tiers() -> dict[str, dict[str, Any]]:
    """Build the tier map from env. A tier is available only if its base_url is set."""
    tiers: dict[str, dict[str, Any]] = {}
    if os.environ.get("ESCALATION_BASE_URL"):
        tiers["cheap"] = {
            "base_url": os.environ["ESCALATION_BASE_URL"].rstrip("/"),
            "api_key": os.environ.get("ESCALATION_API_KEY", ""),
            "model": os.environ.get("ESCALATION_MODEL", "deepseek-v4-flash"),
            "price_in": float(os.environ.get("ESCALATION_PRICE_IN", "0.14")),
            "price_out": float(os.environ.get("ESCALATION_PRICE_OUT", "0.28")),
        }
    if os.environ.get("ESCALATION_LONG_BASE_URL"):
        tiers["long"] = {
            "base_url": os.environ["ESCALATION_LONG_BASE_URL"].rstrip("/"),
            "api_key": os.environ.get("ESCALATION_LONG_API_KEY", ""),
            "model": os.environ.get("ESCALATION_LONG_MODEL", "kimi-k2.6"),
            "price_in": float(os.environ.get("ESCALATION_LONG_PRICE_IN", "0.60")),
            "price_out": float(os.environ.get("ESCALATION_LONG_PRICE_OUT", "2.50")),
        }
    # LOCAL escalation tier (a bigger LOCAL model — 122B-A10B / 27B-dense — on a
    # second endpoint). It is FREE (same box, no API cost), so it is ON even when
    # cloud escalation is disabled, and the hard kernel tries it BEFORE any cloud.
    if os.environ.get("ESCALATION_LOCAL_BASE_URL"):
        tiers["local"] = {
            "base_url": os.environ["ESCALATION_LOCAL_BASE_URL"].rstrip("/"),
            "api_key": os.environ.get("ESCALATION_LOCAL_API_KEY", ""),
            "model": os.environ.get("ESCALATION_LOCAL_MODEL", "/model-local-hard"),
            "price_in": 0.0,
            "price_out": 0.0,
            "free": True,
        }
    return tiers


def _load_state() -> dict[str, Any]:
    today = date.today().isoformat()
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
        if st.get("date") != today:
            st = {"date": today, "spend_usd": 0.0, "calls": 0}
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        st = {"date": today, "spend_usd": 0.0, "calls": 0}
    return st


def _save_state(st: dict[str, Any]) -> None:
    Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def _post_chat(tier_cfg: dict[str, Any], task: str, max_tokens: int) -> dict[str, Any]:
    """Real OpenAI-compatible call. This is the seam the smoke test stubs."""
    headers = {"Content-Type": "application/json"}
    if tier_cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {tier_cfg['api_key']}"
    payload = {
        "model": tier_cfg["model"],
        "messages": [{"role": "user", "content": task}],
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(f"{tier_cfg['base_url']}/chat/completions",
                           json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _cost(tier_cfg: dict[str, Any], usage: dict[str, Any]) -> float:
    pin = usage.get("prompt_tokens", 0)
    pout = usage.get("completion_tokens", 0)
    return pin / 1e6 * tier_cfg["price_in"] + pout / 1e6 * tier_cfg["price_out"]


def _compose(task: str, context: Any) -> str:
    """Surgical handoff: prepend the FULL context (PLAN.md + relevant diffs +
    failure traces) to the task — NOT a lossy summary — so the bigger model picks
    up exactly where the local agent got stuck (reuses the 0.5 state snapshot)."""
    if not context:
        return task
    if isinstance(context, str):
        return f"## Handoff context\n{context}\n\n## Task\n{task}"
    parts = ["## Handoff context"]
    for key in ("plan", "diffs", "failure_traces", "traces", "notes"):
        val = context.get(key) if isinstance(context, dict) else None
        if val:
            parts.append(f"### {key}\n{val}")
    parts.append(f"## Task\n{task}")
    return "\n\n".join(parts)


def escalate(task: str, tier: str = "cheap", context: Any = None) -> dict[str, Any]:
    tier = (tier or "cheap").strip().lower()
    if tier in FORBIDDEN_TIERS:
        return {"ok": False, "error": f"tier '{tier}' is not routable here (Tier-3 stays on Claude Code)"}

    is_local = tier == "local"
    # Cloud tiers are OFF by default; the FREE local tier is always available.
    if not is_local and not ENABLED:
        return {"ok": False, "disabled": True,
                "reason": "cloud escalation is OFF by default; set ESCALATION_ENABLED=true "
                          "(the free local tier needs ESCALATION_LOCAL_BASE_URL and is always on)"}

    tiers = _tiers()
    if tier not in tiers:
        return {"ok": False, "error": f"tier '{tier}' unavailable",
                "available_tiers": sorted(tiers.keys())}
    cfg = tiers[tier]
    prompt = _compose(task, context)

    with _lock:
        st = _load_state()
        # USD cap applies only to PAID (cloud) tiers; the local tier is free.
        if not is_local and st["spend_usd"] >= DAILY_USD_CAP:
            return {"ok": False, "cap_reached": True, "spend_usd": round(st["spend_usd"], 6),
                    "daily_cap_usd": DAILY_USD_CAP,
                    "reason": "daily escalation USD cap reached; falling back to local"}
        try:
            data = _post_chat(cfg, prompt, MAX_TOKENS)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"escalation call failed: {e}", "tier": tier}

        usage = data.get("usage", {}) or {}
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            content = ""
        cost = 0.0 if is_local else _cost(cfg, usage)
        if not is_local:
            st["spend_usd"] += cost
        st["calls"] += 1
        _save_state(st)

    _otel("escalated", {"tier": tier, "free": is_local, "cost_usd": round(cost, 6),
                       "handoff": bool(context)})
    return {
        "ok": True,
        "tier": tier,
        "free": is_local,
        "model": cfg["model"],
        "content": content,
        "usage": usage,
        "cost_usd": round(cost, 6),
        "spend_today_usd": round(st["spend_usd"], 6),
        "daily_cap_usd": DAILY_USD_CAP,
        "handoff_context_included": bool(context),
    }


# ── difficulty classifier (the SHARED signal: Stage-1 search depth, Stage-2 ──
# verify depth, and Stage-3 escalation all consume this one tag) ─────────────
def _novelty_score(novelty: Any) -> int:
    if isinstance(novelty, (int, float)):
        return 2 if novelty >= 0.66 else (1 if novelty >= 0.33 else 0)
    s = str(novelty or "").strip().lower()
    return {"high": 2, "medium": 1, "med": 1, "low": 0, "": 0}.get(s, 0)


def classify_difficulty(signals: dict | None = None) -> dict[str, Any]:
    """Cheap, up-front easy/medium/hard tag from observable signals.

    signals: {file_count, novelty('low'|'medium'|'high'|0..1), prior_failures,
    lines_changed, cross_module(bool)}. This is the ONE difficulty signal the
    whole harness shares — gate search N, verify depth, and escalation off it.
    """
    s = signals or {}
    # Empty-base correctness: on a cold start with NO observable signals gathered,
    # the caller is UNCERTAIN, not "this is easy". Default conservative (medium →
    # the cheap ladder engages) rather than under-escalating. Toggle-able; once the
    # caller passes any real signal, normal scoring applies.
    if ESCALATE_WHEN_UNCERTAIN and not s:
        return {"ok": True, "difficulty": "medium", "score": 2,
                "reasons": ["uncertain: no signals gathered — conservative cold-start default "
                            "(escalate-when-uncertain)"], "uncertain_default": True}
    score = 0
    reasons: list[str] = []
    fc = int(s.get("file_count", 0) or 0)
    if fc >= 8:
        score += 2
        reasons.append(f"touches {fc} files")
    elif fc >= 4:
        score += 1
        reasons.append(f"touches {fc} files")
    pf = int(s.get("prior_failures", 0) or 0)
    if pf >= 2:
        score += 2
        reasons.append(f"{pf} prior failed attempts")
    elif pf == 1:
        score += 1
        reasons.append("1 prior failed attempt")
    nv = _novelty_score(s.get("novelty"))
    if nv:
        score += nv
        reasons.append(f"novelty={s.get('novelty')}")
    if int(s.get("lines_changed", 0) or 0) >= 200:
        score += 1
        reasons.append("large diff (>=200 lines)")
    if s.get("cross_module"):
        score += 1
        reasons.append("spans multiple modules")
    difficulty = "hard" if score >= 4 else ("medium" if score >= 2 else "easy")
    return {"ok": True, "difficulty": difficulty, "score": score,
            "reasons": reasons or ["no complexity signals"]}


# ── plan-need classifier (the plan/execute split, Stage 1) ───────────────────
# A rule-based, NO-LLM gate that decides whether a task warrants an up-front PLAN
# phase on the expensive planner (V4-Pro / the synth role) before the cheap local
# executor implements. The principle: a substantive build (multi-file, multi-
# function, or test-bearing) is where the local 35B drifts, so pay for an
# incontrovertible plan once; a single-file edit / lookup / question stays local.
# Mirrors classify_difficulty's stance: conservative when uncertain (an action-verb
# task with ambiguous scope is better over-planned than under-planned).
_PLAN_VERBS = ("implement", "build", "write", "create", "design", "refactor", "add")
# words that signal the task is a question / lookup / inspection — NOT a build
_NO_PLAN_HINTS = ("what", "why", "how does", "where", "explain", "describe", "look up",
                  "lookup", "find", "show me", "list", "read", "summarize", "summarise")
# crude signals that a string task touches more than one file / function
_MULTI_FILE_HINTS = ("files", "modules", "across", "package", "directory", "and ",
                     " plus ", "multiple", "several", "endpoints", "components")
_TEST_HINTS = ("test", "tests", "pytest", "unit test", "coverage", "tdd")


def classify_plan_need(task: str = "", signals: dict | None = None) -> dict[str, Any]:
    """Decide whether a task needs an up-front PLAN phase (NO LLM call).

    NEEDS_PLAN when an action verb (Implement/Build/Write/Create/Design/Refactor/
    Add) is present AND the work looks substantive — more than one file OR more than
    a single function OR it mentions tests. NO_PLAN for single-file edits, lookups,
    one-line fixes, and pure questions.

    Args:
        task: the task description string (verb + scope are scanned from it).
        signals: optional structured hints that override the string scan —
            {file_count:int, mentions_tests:bool, multi_function:bool,
             single_file:bool}.

    Returns {ok, plan_required:bool, reason, matched_verb, signals_used:bool}.
    Never raises. Conservative: an action-verb task with ambiguous scope is flagged
    NEEDS_PLAN (better to over-plan a borderline task than let the executor drift).
    """
    s = signals or {}
    low = (task or "").strip().lower()

    def _result(plan_required: bool, reason: str, matched_verb: str | None) -> dict[str, Any]:
        out = {"ok": True, "plan_required": plan_required, "matched_verb": matched_verb,
               "signals_used": bool(s), "reason": reason}
        _otel("task_classification", {"plan_required": plan_required, "reason": reason})
        return out

    # a pure question / lookup is never a plan task, even if it contains a verb
    if low and any(low.startswith(h) or f" {h}" in low[:40] for h in _NO_PLAN_HINTS) \
            and not any(low.startswith(v) for v in _PLAN_VERBS):
        return _result(False, "question/lookup/inspection — no build, stay local (NO_PLAN)", None)

    matched_verb = next((v for v in _PLAN_VERBS if re.search(rf"\b{v}\b", low)), None)

    # structured signals take precedence over the string scan when provided
    file_count = int(s.get("file_count", 0) or 0)
    multi_file = (file_count > 1) or bool(s.get("multi_function")) \
        or any(h in low for h in _MULTI_FILE_HINTS)
    mentions_tests = bool(s.get("mentions_tests")) or any(
        re.search(rf"\b{re.escape(h)}\b", low) for h in _TEST_HINTS)
    single_file = bool(s.get("single_file")) or file_count == 1

    if not matched_verb:
        return _result(False, "no build/implement verb — not a multi-step build (NO_PLAN)", None)

    # an explicit single-file edit with no tests / no multi-function is NO_PLAN
    if single_file and not mentions_tests and not s.get("multi_function"):
        return _result(
            False,
            f"'{matched_verb}' but a single-file change with no tests — stay local (NO_PLAN)",
            matched_verb)

    substantive = multi_file or mentions_tests
    reasons: list[str] = []
    if file_count > 1:
        reasons.append(f"touches {file_count} files")
    elif multi_file:
        reasons.append("scope reads as multi-file/multi-function")
    if mentions_tests:
        reasons.append("mentions tests")
    if not substantive:
        # action verb but no scope signal at all -> conservative over-plan
        reasons.append("action verb with ambiguous scope (conservative over-plan)")
    return _result(True, f"'{matched_verb}' build — {'; '.join(reasons)} (NEEDS_PLAN)", matched_verb)


def record_outcome(task: str, signals: dict | None = None, difficulty: str | None = None,
                   outcome: str = "unknown", escalated: bool = False,
                   tier: str | None = None) -> dict[str, Any]:
    """Append a labelled (signals → difficulty → outcome) example to OUTCOMES_LOG —
    the compounding flywheel. Call this when a task finishes, ESPECIALLY when it
    escalated and the higher tier solved it: that becomes training signal so the
    next GEPA run improves classify_difficulty and the local model handles more of
    the formerly-escalated band. Best-effort; never raises."""
    sig = signals or {}
    if not difficulty:
        difficulty = classify_difficulty(sig).get("difficulty")
    rec = {"task": task, "signals": sig, "difficulty": difficulty,
           "outcome": outcome, "escalated": bool(escalated), "tier": tier}
    try:
        Path(OUTCOMES_LOG).parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(OUTCOMES_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return {"ok": True, "recorded": rec, "log": OUTCOMES_LOG}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def should_escalate(signals: dict | None = None) -> dict[str, Any]:
    """Auto-trigger check: escalate when verifier-guided search exhausted N
    without green, OR backtracking exhausted approaches, OR confidence is low on
    an irreversible/high-stakes change."""
    s = signals or {}
    reasons: list[str] = []
    if s.get("search_exhausted"):
        reasons.append("verifier-guided search exhausted N without green")
    if s.get("backtrack_exhausted"):
        reasons.append("backtracking exhausted all approaches")
    if s.get("confidence_low") and (s.get("irreversible") or s.get("high_stakes")):
        reasons.append("low confidence on an irreversible/high-stakes change")
    return {"ok": True, "escalate": bool(reasons),
            "reasons": reasons or ["no auto-trigger condition met"]}


def route(task: str, difficulty: str | None = None, signals: dict | None = None,
          context: Any = None) -> dict[str, Any]:
    """Tiered routing for a HARD kernel: easy/medium stay on the primary local
    model; hard tries the FREE local escalation tier FIRST, then a cloud tier
    ONLY if local is unavailable/failed (and cloud is enabled + under cap)."""
    if difficulty is None:
        difficulty = classify_difficulty(signals)["difficulty"]
    difficulty = difficulty.strip().lower()
    if difficulty in ("easy", "medium"):
        return {"ok": True, "escalated": False, "route": "local_model", "difficulty": difficulty,
                "note": "handle on the primary local model; no escalation needed"}

    tiers = _tiers()
    order = [t for t in ("local", "cheap", "long") if t in tiers]
    attempts: list[dict[str, Any]] = []
    for tier in order:
        r = escalate(task, tier, context)
        attempts.append({"tier": tier, "ok": bool(r.get("ok")),
                         "why": r.get("reason") or r.get("error") or "ok"})
        if r.get("ok"):
            return {"ok": True, "escalated": True, "route": tier, "difficulty": "hard",
                    "result": r, "attempts": attempts}
    return {"ok": False, "escalated": False, "difficulty": "hard", "attempts": attempts,
            "reason": "no tier could handle the hard kernel (local unavailable/failed; "
                      "cloud disabled or capped) — write a STUCK SUMMARY and ping the human"}


def status() -> dict[str, Any]:
    st = _load_state()
    tiers = _tiers()
    return {
        "enabled": ENABLED,
        "daily_cap_usd": DAILY_USD_CAP,
        "spend_today_usd": round(st["spend_usd"], 6),
        "calls_today": st["calls"],
        "max_tokens_per_call": MAX_TOKENS,
        "tiers_available": sorted(tiers.keys()),
        "local_tier_available": "local" in tiers,  # free; on by default when configured
        "cloud_gated_off": not ENABLED,
        "forbidden_tiers": sorted(FORBIDDEN_TIERS),
    }
