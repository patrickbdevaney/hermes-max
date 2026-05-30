"""Conductor role executor + cost ledger — piece (c) of the three-piece router.

Two execution shapes, both presence-gated and both NEVER-RAISE:

  • run_role(role, ...) — ORDERED chains (steer/synth/escalate). Walk the present
    rungs; call the first; on failure/429/5xx/timeout/deprecation SILENTLY advance
    and log a one-line trace entry; if none succeed (or all paid rungs are blocked
    by the USD cap) return a graceful {proceed_local: True} signal. It is a hard
    invariant that this returns a dict and never propagates an exception into the
    local orchestrator's core loop.

  • draft_fanout(...) — the UNORDERED parallel_draft pool. Fan out concurrently to
    every present pool member that is within its live RPM/RPD budget; skip
    exhausted sources; degrade to fewer candidates (or N=1-local) rather than
    failing. Returns the raw candidates — the VERIFIER (mcp-search, Stage 4), not
    this module, selects the winner.

Cost is metered to a conductor ledger (separate from mcp-escalation's spend.json
so neither perturbs the other) with per-day + per-month caps; once a cap is hit,
paid rungs behave as if absent. The free Opus escalate rung still routes through
mcp-escalation's own capped server — this module never calls Opus directly.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

import conductor_registry as reg
import conductor_resolver as resolver

TIMEOUT = float(os.environ.get("CONDUCTOR_TIMEOUT", "90"))
MAX_TOKENS = int(os.environ.get("CONDUCTOR_MAX_TOKENS", "4096"))
LEDGER_PATH = os.path.expanduser(
    os.environ.get("CONDUCTOR_LEDGER_PATH", "~/.hermes-max/conductor/ledger.json"))
BUDGET_PATH = os.path.expanduser(
    os.environ.get("CONDUCTOR_BUDGET_PATH", "~/.hermes-max/conductor/budget.json"))

_lock = threading.Lock()
# rolling in-memory trace of silent falls (also emitted as OTel spans); the
# Stage-5 report and `status()` read it. Bounded.
_TRACE: list[dict[str, Any]] = []
_TRACE_MAX = 200


def _otel(name: str, attrs: dict[str, Any]) -> None:
    try:
        import otel_emit

        otel_emit.record(name, attrs, status="ok")
    except Exception:  # noqa: BLE001 - observability optional
        pass


def _trace(event: str, **attrs: Any) -> None:
    rec = {"event": event, **attrs}
    with _lock:
        _TRACE.append(rec)
        if len(_TRACE) > _TRACE_MAX:
            del _TRACE[: len(_TRACE) - _TRACE_MAX]
    _otel(event, attrs)


# ── cost ledger (per-day + per-month, per-provider/role) ──────────────────────
def _blank_ledger() -> dict[str, Any]:
    return {"date": date.today().isoformat(), "month": date.today().isoformat()[:7],
            "spend_today": 0.0, "spend_month": 0.0, "calls": 0,
            "by_provider": {}, "by_role": {}}


def _load_ledger() -> dict[str, Any]:
    today = date.today().isoformat()
    month = today[:7]
    try:
        with open(LEDGER_PATH) as f:
            lg = json.load(f)
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        return _blank_ledger()
    if lg.get("date") != today:
        lg["date"] = today
        lg["spend_today"] = 0.0
    if lg.get("month") != month:
        lg["month"] = month
        lg["spend_month"] = 0.0
        lg["by_provider"] = {}
        lg["by_role"] = {}
    lg.setdefault("by_provider", {})
    lg.setdefault("by_role", {})
    lg.setdefault("calls", 0)
    return lg


def _save_ledger(lg: dict[str, Any]) -> None:
    Path(LEDGER_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lg, f)
    os.replace(tmp, LEDGER_PATH)


def _record_cost(provider: str, role: str, usd: float) -> dict[str, Any]:
    with _lock:
        lg = _load_ledger()
        lg["spend_today"] += usd
        lg["spend_month"] += usd
        lg["calls"] += 1
        lg["by_provider"][provider] = round(lg["by_provider"].get(provider, 0.0) + usd, 6)
        lg["by_role"][role] = round(lg["by_role"].get(role, 0.0) + usd, 6)
        _save_ledger(lg)
        return lg


def _cap_blocked(caps: dict[str, float], lg: dict[str, Any]) -> str | None:
    if lg["spend_today"] >= caps.get("usd_daily", 1.0):
        return f"daily USD cap reached (${lg['spend_today']:.4f} >= ${caps['usd_daily']})"
    if lg["spend_month"] >= caps.get("usd_monthly", 5.0):
        return f"monthly USD cap reached (${lg['spend_month']:.4f} >= ${caps['usd_monthly']})"
    return None


# ── per-provider RPM/RPD budget (live, persisted sliding window) ──────────────
def _budget_allows(provider: str, prov_cfg: dict[str, Any], *, commit: bool) -> bool:
    """True if `provider` is within its documented RPM and RPD. When commit=True
    and allowed, records a timestamp. Persisted so the window survives across the
    short-lived tool calls. None limits = unlimited (paid headroom)."""
    rpm = prov_cfg.get("rpm")
    rpd = prov_cfg.get("rpd")
    if not rpm and not rpd:
        return True
    now = time.time()
    with _lock:
        try:
            with open(BUDGET_PATH) as f:
                buckets = json.load(f)
        except Exception:  # noqa: BLE001
            buckets = {}
        ts = [t for t in buckets.get(provider, []) if now - t < 86_400]  # prune >1d
        if rpm and sum(1 for t in ts if now - t < 60) >= rpm:
            buckets[provider] = ts
            _save_budget(buckets)
            return False
        if rpd and len(ts) >= rpd:
            buckets[provider] = ts
            _save_budget(buckets)
            return False
        if commit:
            ts.append(now)
        buckets[provider] = ts
        _save_budget(buckets)
        return True


def _save_budget(buckets: dict[str, Any]) -> None:
    Path(BUDGET_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = BUDGET_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(buckets, f)
    os.replace(tmp, BUDGET_PATH)


# ── the single-call primitive (the seam the smoke test stubs) ─────────────────
def _post_chat(base_url: str, api_key: str, model: str, messages: list[dict],
               max_tokens: int) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(f"{base_url.rstrip('/')}/chat/completions",
                           json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _price(prov_cfg: dict[str, Any], role: str) -> dict[str, float]:
    p = prov_cfg.get("price", {})
    return p.get(role) or p.get("synth") or p.get("steer") or {"in": 0.0, "out": 0.0}


def _is_free(prov_cfg: dict[str, Any], role: str) -> bool:
    pr = _price(prov_cfg, role)
    return (pr.get("in", 0.0) + pr.get("out", 0.0)) <= 0.0


def _cost(prov_cfg: dict[str, Any], role: str, usage: dict[str, Any]) -> float:
    pr = _price(prov_cfg, role)
    return (usage.get("prompt_tokens", 0) / 1e6 * pr.get("in", 0.0)
            + usage.get("completion_tokens", 0) / 1e6 * pr.get("out", 0.0))


def _model_for(prov_cfg: dict[str, Any], provider: str, role: str) -> str:
    models = prov_cfg.get("models", {})
    # env single-model overrides (optional) take precedence for steer/synth/escalate
    env_override = {
        "steer": os.environ.get("CONDUCTOR_STEER_MODEL"),
        "synth": os.environ.get("CONDUCTOR_SYNTH_MODEL"),
        "escalate": os.environ.get("CONDUCTOR_ESCALATE_MODEL"),
    }.get(role)
    if env_override and provider == "deepinfra":  # env model strings are DeepInfra-shaped
        return env_override
    return models.get(role) or models.get("synth") or models.get("steer") or ""


# ── ORDERED ROLE EXECUTOR ─────────────────────────────────────────────────────
def run_role(role: str, messages: list[dict] | None = None, *, prompt: str | None = None,
             max_tokens: int | None = None) -> dict[str, Any]:
    """Execute an ordered role (steer/synth/escalate) over its present chain.

    Returns a dict ALWAYS (never raises). On success: {ok:True, provider, model,
    content, usage, cost_usd, fell:[...]}. If the role is OFF (no present rung) or
    every present rung failed/was-capped: {ok:False, proceed_local:True, ...}."""
    role = (role or "").strip().lower()
    if messages is None:
        messages = [{"role": "user", "content": prompt or ""}]
    cfg = reg.load_config()
    providers = cfg["providers"]
    caps = cfg["caps"]
    chain = cfg["role_chains"].get(role, [])
    env = dict(os.environ)
    present = resolver.resolve_chain(chain, providers, env)
    if not present:
        return {"ok": False, "proceed_local": True, "role": role, "role_active": False,
                "reason": f"role '{role}' is OFF (no present provider key in its chain) "
                          "-> the driver proceeds local-only", "attempts": []}

    mt = max_tokens or MAX_TOKENS
    attempts: list[dict[str, Any]] = []
    for pid in present:
        prov = providers[pid]
        free = _is_free(prov, role)
        if not free:
            lg = _load_ledger()
            blocked = _cap_blocked(caps, lg)
            if blocked:
                attempts.append({"provider": pid, "skipped": "usd_cap", "why": blocked})
                _trace("rung_fell", role=role, frm=pid, to="(next)", reason=blocked)
                continue
        model = _model_for(prov, pid, role)
        key = env.get(prov.get("env_key_name", ""), "")
        try:
            data = _post_chat(prov["base_url"], key, model, messages, mt)
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            usage = data.get("usage", {}) or {}
            if content is None:  # reasoning models can return empty content if budget burned
                raise ValueError("empty content (reasoning budget exhausted?)")
        except Exception as e:  # noqa: BLE001 - any failure -> silently fall to next rung
            reason = f"{type(e).__name__}: {str(e)[:80]}"
            attempts.append({"provider": pid, "failed": reason})
            _trace("rung_fell", role=role, frm=pid, to="(next)", reason=reason)
            continue
        cost = 0.0 if free else _cost(prov, role, usage)
        if not free:
            _record_cost(pid, role, cost)
        _trace("role_resolved", role=role, provider=pid, model=model, fell=len(attempts))
        return {"ok": True, "role": role, "role_active": True, "provider": pid, "model": model,
                "content": content, "usage": usage, "cost_usd": round(cost, 6),
                "free": free, "fell": attempts}

    return {"ok": False, "proceed_local": True, "role": role, "role_active": True,
            "attempts": attempts,
            "reason": f"all {len(present)} present rung(s) for '{role}' failed or were "
                      "cap-blocked -> the driver proceeds local-only"}


# ── UNORDERED parallel_draft FAN-OUT (RPM/RPD-budgeted, concurrent) ───────────
def _draft_one(entry: dict[str, str], prov: dict[str, Any], messages: list[dict],
               mt: int, env: dict[str, str]) -> dict[str, Any]:
    pid, model = entry["provider"], entry["model"]
    key = env.get(prov.get("env_key_name", ""), "")
    try:
        data = _post_chat(prov["base_url"], key, model, messages, mt)
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        usage = data.get("usage", {}) or {}
        if content is None:
            raise ValueError("empty content")
    except Exception as e:  # noqa: BLE001
        return {"provider": pid, "model": model, "ok": False,
                "error": f"{type(e).__name__}: {str(e)[:80]}"}
    free = _is_free(prov, "draft")
    cost = 0.0 if free else _cost(prov, "draft", usage)
    if not free:
        _record_cost(pid, "draft", cost)
    return {"provider": pid, "model": model, "ok": True, "content": content,
            "usage": usage, "cost_usd": round(cost, 6), "free": free}


def draft_fanout(messages: list[dict] | None = None, *, prompt: str | None = None,
                 n: int | None = None, max_tokens: int | None = None) -> dict[str, Any]:
    """Fan out a draft brief across the present parallel_draft pool, concurrently,
    respecting each provider's live RPM/RPD budget. Returns the raw candidates;
    selection is the verifier's job (mcp-search, Stage 4). Never raises."""
    if messages is None:
        messages = [{"role": "user", "content": prompt or ""}]
    cfg = reg.load_config()
    providers = cfg["providers"]
    caps = cfg["caps"]
    env = dict(os.environ)
    present = resolver.resolve_pool(cfg["draft_pool"], providers, env)
    cap_n = int(n or caps.get("draft_max_n", 5))

    # budget + USD-cap gate (paid anchor obeys the cap; free members obey RPM/RPD)
    runnable: list[dict[str, str]] = []
    skipped: list[dict[str, Any]] = []
    ledger = _load_ledger()
    usd_blocked = _cap_blocked(caps, ledger)
    for entry in present:
        if len(runnable) >= cap_n:
            break
        prov = providers[entry["provider"]]
        if not _is_free(prov, "draft") and usd_blocked:
            skipped.append({**entry, "skipped": "usd_cap"})
            continue
        if not _budget_allows(entry["provider"], prov, commit=True):
            skipped.append({**entry, "skipped": "rpm_rpd_exhausted"})
            continue
        runnable.append(entry)

    if not runnable:
        _trace("draft_fanout", n_present=len(present), n_runnable=0, degraded_local=True)
        return {"ok": False, "proceed_local": True, "candidates": [], "skipped": skipped,
                "n_present": len(present),
                "reason": "no pool member within budget -> degrade to N=1-local"}

    mt = max_tokens or MAX_TOKENS
    candidates: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(runnable)) as ex:
        futs = {ex.submit(_draft_one, e, providers[e["provider"]], messages, mt, env): e
                for e in runnable}
        for fut in concurrent.futures.as_completed(futs):
            candidates.append(fut.result())

    passed = [c for c in candidates if c.get("ok")]
    _trace("draft_fanout", n_present=len(present), n_runnable=len(runnable),
           n_passed=len(passed), n_skipped=len(skipped))
    return {"ok": bool(passed), "candidates": candidates, "skipped": skipped,
            "n_present": len(present), "n_runnable": len(runnable), "n_passed": len(passed),
            "reason": "fanned out across present free/cheap pool members for cross-family diversity"}


# ── status + cost report (Stage-5 surfaces) ──────────────────────────────────
def status() -> dict[str, Any]:
    cfg = reg.load_config()
    providers = cfg["providers"]
    env = dict(os.environ)
    roles = resolver.active_roles(cfg["role_chains"], providers, env)
    pool_present = resolver.resolve_pool(cfg["draft_pool"], providers, env)
    lg = _load_ledger()
    return {
        "roles_active": roles,
        "resolved_chains": {r: resolver.resolve_chain(c, providers, env)
                            for r, c in cfg["role_chains"].items()},
        "draft_pool_present": [f"{e['provider']}:{e['model']}" for e in pool_present],
        "caps": cfg["caps"],
        "spend_today_usd": round(lg["spend_today"], 6),
        "spend_month_usd": round(lg["spend_month"], 6),
        "config_applied": cfg["config_applied"],
        "recent_falls": [t for t in _TRACE if t["event"] == "rung_fell"][-10:],
    }


def cost_report() -> dict[str, Any]:
    lg = _load_ledger()
    return {"date": lg["date"], "month": lg["month"],
            "spend_today_usd": round(lg["spend_today"], 6),
            "spend_month_usd": round(lg["spend_month"], 6),
            "calls": lg.get("calls", 0),
            "by_provider": lg.get("by_provider", {}),
            "by_role": lg.get("by_role", {})}
