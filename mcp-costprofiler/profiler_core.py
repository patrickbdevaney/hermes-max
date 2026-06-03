"""profiler_core.py — Phase 1: per-step latency/cost attribution (the measurement substrate).

The cost-asymmetry thesis cannot be proven, and no ratio-risky toggle can be justified,
without honest per-call accounting of dollars, tokens, wall-clock, and WHICH BACKEND ran.
This module consumes the accounting the system ALREADY emits (the lib/inference ledger for
cost-bearing conductor/MCP calls) and adds an executor-call log for the local single-stream
calls that don't hit that ledger — then rolls both up BY BACKEND so the parallelism
dispatcher (Phase 3) can reason about the serial-local penalty, and the bandit (Phase 2) can
compute uplift-per-dollar.

Three named backends (the dispatcher's world model):
  local-serial  — Qwen3 on the Thor vLLM: single-stream, ~$0, N branches = N×wall-clock.
  fabric        — Groq/Cerebras free tiers: parallel to rate limit, $0.
  cloud-deepseek/cloud-frontier — DeepInfra/DeepSeek (and spare frontier): parallel, real $.

Deterministic, no LLM. Never raises. Backed by two JSONL files under ~/.hermes-max/.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

BACKENDS = ("local-serial", "fabric", "cloud-deepseek", "cloud-frontier")

# provider-name → backend (substring match, case-insensitive). config.tier is the fallback.
_BACKEND_MAP = [
    (("local_vllm", "vllm", "local", "thor", "qwen"), "local-serial"),
    (("groq", "cerebras"), "fabric"),
    (("deepinfra", "deepseek", "fireworks", "together"), "cloud-deepseek"),
    (("anthropic", "openai", "gemini", "google"), "cloud-frontier"),
]


def _calls_path() -> str:
    return os.path.expanduser(os.environ.get(
        "PROFILER_CALLS_PATH", "~/.hermes-max/profiler/calls.jsonl"))


def _ledger_path() -> str:
    return os.path.expanduser(os.environ.get(
        "INFERENCE_LEDGER_PATH", "~/.hermes-max/inference/ledger.jsonl"))


def outcomes_path() -> str:
    """Shared with the bandit router (Phase 2) + regression corpus (Phase 6)."""
    return os.path.expanduser(os.environ.get(
        "ROUTER_OUTCOMES_PATH", "~/.hermes-max/router/outcomes.jsonl"))


def backend_of(provider: str, model: str = "") -> str:
    """Classify a provider/model into one of the three (+frontier) named backends.
    Deterministic name map first; lib/inference config.tier as a guarded fallback."""
    p = (provider or "").lower()
    for needles, backend in _BACKEND_MAP:
        if any(n in p for n in needles):
            return backend
    try:
        from lib.inference import config
        return {"local": "local-serial", "free": "fabric",
                "paid": "cloud-deepseek", "frontier": "cloud-frontier"}.get(config.tier(provider), "fabric")
    except Exception:  # noqa: BLE001
        return "fabric"  # unknown paid-ish default; openrouter free still maps via name above


def _append(path: str, row: dict[str, Any]) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def log_call(backend: str, task_class: str = "", in_tok: int = 0, out_tok: int = 0,
             cost_usd: float = 0.0, wall_ms: int = 0, provider: str = "", model: str = "",
             source: str = "executor", ts: Optional[float] = None) -> dict[str, Any]:
    """Record one LLM/tool call. Used for the local EXECUTOR calls that bypass the
    lib/inference ledger (the external hermes loop); cost-bearing conductor/MCP calls are
    read straight from that ledger by report(), so they need no double-logging here."""
    row = {"ts": ts if ts is not None else time.time(), "backend": backend,
           "task_class": task_class, "in_tok": int(in_tok), "out_tok": int(out_tok),
           "cost_usd": round(float(cost_usd), 6), "wall_ms": int(wall_ms),
           "provider": provider, "model": model, "source": source}
    _append(_calls_path(), row)
    return row


def span_attrs(backend: str, task_class: str, in_tok: int, out_tok: int,
               cost_usd: float, wall_ms: int) -> dict[str, Any]:
    """The attributes a hook attaches to the OTel span so the UI swimlane shows routing,
    backend, cost, and latency for every call."""
    return {"backend": backend, "task_class": task_class or None, "in_tok": in_tok,
            "out_tok": out_tok, "cost_usd": round(float(cost_usd), 6), "wall_ms": wall_ms}


def _window_floor(window: str) -> float:
    now = time.time()
    return {"today": now - 86400, "7d": now - 7 * 86400, "all": 0.0}.get(window, 0.0)


def _iter_rows(window: str):
    """Yield unified call rows from BOTH the profiler executor log and the inference ledger
    (the latter tagged with its backend), filtered to the window."""
    floor = _window_floor(window)
    # profiler's own executor calls
    try:
        with open(_calls_path()) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("ts", 0) >= floor:
                    yield r
    except OSError:
        pass
    # the lib/inference ledger (conductor/planner/escalation/MCP cheap calls — cost-bearing)
    try:
        with open(_ledger_path()) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("ts", 0) < floor:
                    continue
                yield {"ts": r.get("ts", 0), "backend": backend_of(r.get("provider", ""), r.get("model", "")),
                       "task_class": r.get("role", ""), "in_tok": r.get("in_tok", 0),
                       "out_tok": r.get("out_tok", 0), "cost_usd": r.get("cost_usd", 0.0),
                       "wall_ms": r.get("wall_ms", 0), "provider": r.get("provider", ""),
                       "model": r.get("model", ""), "source": "ledger"}
    except OSError:
        pass


def _pctl(xs: list[int], p: float) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    return int(s[min(len(s) - 1, int(p * len(s)))])


def report(window: str = "today", task_class: Optional[str] = None) -> dict[str, Any]:
    """Roll up cost/tokens/wall-clock BY BACKEND over the window. The per-backend wall-clock
    is what lets the dispatcher quantify the serial-local penalty."""
    by: dict[str, dict[str, Any]] = {b: {"calls": 0, "in_tok": 0, "out_tok": 0,
                                         "cost_usd": 0.0, "_walls": []} for b in BACKENDS}
    total_usd = 0.0
    for r in _iter_rows(window):
        if task_class and r.get("task_class") != task_class:
            continue
        b = r.get("backend") if r.get("backend") in by else "fabric"
        d = by[b]
        d["calls"] += 1
        d["in_tok"] += int(r.get("in_tok", 0))
        d["out_tok"] += int(r.get("out_tok", 0))
        d["cost_usd"] = round(d["cost_usd"] + float(r.get("cost_usd", 0.0)), 6)
        if r.get("wall_ms"):
            d["_walls"].append(int(r["wall_ms"]))
        total_usd += float(r.get("cost_usd", 0.0))
    for b, d in by.items():
        walls = d.pop("_walls")
        d["wall_ms_p50"] = _pctl(walls, 0.5)
        d["wall_ms_p95"] = _pctl(walls, 0.95)
        d["cost_usd"] = round(d["cost_usd"], 6)
    return {"window": window, "task_class": task_class, "by_backend": by,
            "total_usd": round(total_usd, 6),
            "total_calls": sum(d["calls"] for d in by.values())}


# ── outcome-joined queries (outcomes written by Phase 2 bandit / Phase 6 corpus) ──
def _iter_outcomes(window: str = "all"):
    floor = _window_floor(window)
    try:
        with open(outcomes_path()) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("ts", 0) >= floor:
                    yield r
    except OSError:
        return


def cost_per_solved_task(task_class: Optional[str] = None, window: str = "all") -> dict[str, Any]:
    """Total spend / number of SOLVED tasks, per task class. The honest denominator for
    'is this toggle worth it'. Returns {task_class: {solved, attempts, total_cost,
    cost_per_solved}}; empty/`no_outcomes` until Phase 2 logs outcomes."""
    agg: dict[str, dict[str, Any]] = {}
    n = 0
    for o in _iter_outcomes(window):
        n += 1
        tc = o.get("task_class", "?")
        if task_class and tc != task_class:
            continue
        a = agg.setdefault(tc, {"solved": 0, "attempts": 0, "total_cost": 0.0})
        a["attempts"] += 1
        a["total_cost"] = round(a["total_cost"] + float(o.get("cost_usd", 0.0)), 6)
        if o.get("solved"):
            a["solved"] += 1
    for tc, a in agg.items():
        a["cost_per_solved"] = round(a["total_cost"] / a["solved"], 6) if a["solved"] else None
    if not n:
        return {"status": "no_outcomes", "note": "Phase-2 bandit has not logged outcomes yet",
                "by_task_class": {}}
    return {"status": "ok", "window": window, "by_task_class": agg}


def uplift_per_dollar(task_class: str, window: str = "all") -> dict[str, Any]:
    """Per-backend pass-rate and $/attempt for a task class, with uplift-per-dollar vs the
    local-serial baseline: (pass_rate_b − pass_rate_local) / (cost_b − cost_local). This is
    the number the bandit gate reads to decide whether a paid escalation pays."""
    stat: dict[str, dict[str, Any]] = {}
    for o in _iter_outcomes(window):
        if o.get("task_class") != task_class:
            continue
        b = o.get("backend", "fabric")
        s = stat.setdefault(b, {"attempts": 0, "solved": 0, "cost": 0.0})
        s["attempts"] += 1
        s["solved"] += 1 if o.get("solved") else 0
        s["cost"] = round(s["cost"] + float(o.get("cost_usd", 0.0)), 6)
    for b, s in stat.items():
        s["pass_rate"] = round(s["solved"] / s["attempts"], 4) if s["attempts"] else 0.0
        s["cost_per_attempt"] = round(s["cost"] / s["attempts"], 6) if s["attempts"] else 0.0
    base = stat.get("local-serial")
    if base:
        for b, s in stat.items():
            dcost = s["cost_per_attempt"] - base["cost_per_attempt"]
            dpass = s["pass_rate"] - base["pass_rate"]
            s["uplift_per_dollar"] = round(dpass / dcost, 4) if dcost > 1e-9 else (
                float("inf") if dpass > 0 else 0.0)
    return {"task_class": task_class, "by_backend": stat,
            "baseline": "local-serial" if base else None,
            "status": "ok" if stat else "no_outcomes"}


def profiler_stats() -> dict[str, Any]:
    return {"calls_log": _calls_path(), "ledger": _ledger_path(),
            "outcomes": outcomes_path(), "backends": list(BACKENDS)}
