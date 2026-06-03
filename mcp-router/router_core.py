"""router_core.py — Phase 2: outcome-memory + bandit routing (the cost-asymmetry engine).

The single highest-ROI uplift, because it is the only one that IMPROVES the cheap/utility
ratio rather than spending against it: route the easy/most work to the FREE local-serial
executor with one good attempt, and reserve paid cloud-parallel escalation for where the
numbers say it pays.

Three named backend arms (shared world model with the profiler): local-serial (free, serial),
fabric (free, parallel-to-rate-limit), cloud-deepseek (real $, parallel). A per-(task-class,
backend) UCB1 bandit tracks pass-rate; the routing policy defaults to local-serial and
escalates local→fabric→cloud-deepseek ONLY on a positive predicted uplift-per-dollar from the
Phase-1 profiler. All mechanisms degrade to deterministic heuristics with no cheap inference.

Outcomes are logged to the SAME file the profiler reads (cost_per_solved / uplift_per_dollar),
keyed by task class, with a failure class for the ceiling signal:
  route-fixable · sample-fixable · tool-fixable · trajectory-fixable
No fine-tuning anywhere. Deterministic-first; never raises.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Optional

BACKENDS = ("local-serial", "fabric", "cloud-deepseek")
UCB_C = float(os.environ.get("ROUTER_UCB_C", "1.414"))
# escalate to a PAID backend only when predicted uplift-per-dollar clears this floor.
UPLIFT_FLOOR = float(os.environ.get("ROUTER_UPLIFT_FLOOR", "1.0"))


def _bandit_path() -> str:
    return os.path.expanduser(os.environ.get("ROUTER_BANDIT_PATH", "~/.hermes-max/router/bandit.json"))


def _notes_path() -> str:
    return os.path.expanduser(os.environ.get("ROUTER_NOTES_PATH", "~/.hermes-max/router/notes.jsonl"))


def _outcomes_path() -> str:
    # shared with the profiler (cost_per_solved / uplift_per_dollar read this)
    return os.path.expanduser(os.environ.get("ROUTER_OUTCOMES_PATH", "~/.hermes-max/router/outcomes.jsonl"))


def _profiler():
    try:
        import profiler_core
        return profiler_core
    except Exception:  # noqa: BLE001
        return None


# ── task-class + failure-class taxonomy (deterministic) ───────────────────────
_CLASS_SIGNALS = [
    ("research", ("research", "find out", "investigate", "novel", "state of the art", "compare approaches")),
    ("plan", ("design", "architect", "plan", "decompose", "strategy")),
    ("refactor", ("refactor", "rename", "restructure", "migrate", "move ")),
    ("bugfix", ("bug", "fix", "broken", "fails", "error", "regression", "crash")),
    ("feature", ("implement", "add", "build", "create", "feature", "support")),
]


def task_class_of(text: str) -> str:
    t = (text or "").lower()
    for name, kws in _CLASS_SIGNALS:
        if any(k in t for k in kws):
            return name
    return "code_execute"


def classify_failure(signals: dict[str, Any]) -> str:
    """Map a failed attempt to a fixable class (the ceiling signal the bandit learns from)."""
    if signals.get("needs_research") or signals.get("missing_api"):
        return "tool-fixable"
    if signals.get("replan") or signals.get("wrong_approach"):
        return "trajectory-fixable"
    if signals.get("verify_failed") or signals.get("flaky"):
        return "sample-fixable"   # another sample / best-of-N might land it
    return "route-fixable"        # a different backend might do better


# ── UCB1 bandit over (task_class, backend) ────────────────────────────────────
def _load_bandit() -> dict[str, Any]:
    try:
        with open(_bandit_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_bandit(state: dict[str, Any]) -> None:
    try:
        Path(_bandit_path()).parent.mkdir(parents=True, exist_ok=True)
        tmp = _bandit_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, _bandit_path())
    except OSError:
        pass


def _arm(state: dict, tc: str, b: str) -> dict:
    return state.setdefault(tc, {}).setdefault(b, {"n": 0, "reward_sum": 0.0})


def bandit_scores(task_class: str) -> dict[str, float]:
    """UCB1 score per backend for a task class. Unvisited arms get +inf (forced explore)."""
    state = _load_bandit()
    arms = {b: _arm(state, task_class, b) for b in BACKENDS}
    total = sum(a["n"] for a in arms.values())
    scores: dict[str, float] = {}
    for b, a in arms.items():
        if a["n"] == 0:
            scores[b] = float("inf")
        else:
            mean = a["reward_sum"] / a["n"]
            scores[b] = mean + UCB_C * math.sqrt(math.log(max(1, total)) / a["n"])
    return scores


def bandit_update(task_class: str, backend: str, reward: float) -> dict[str, Any]:
    """Record a reward (1.0 solved, 0.0 not; optionally penalised by latency/$) for an arm."""
    state = _load_bandit()
    a = _arm(state, task_class, backend)
    a["n"] += 1
    a["reward_sum"] = round(a["reward_sum"] + float(reward), 4)
    _save_bandit(state)
    return {"task_class": task_class, "backend": backend, "n": a["n"],
            "mean": round(a["reward_sum"] / a["n"], 4)}


# ── the accuracy+cost table (from outcomes) ───────────────────────────────────
def accuracy_cost_table(task_class: Optional[str] = None) -> dict[str, Any]:
    """Per-(task-class, backend) pass-rate + avg cost from the outcome log."""
    table: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        with open(_outcomes_path()) as f:
            for ln in f:
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                tc = o.get("task_class", "?")
                if task_class and tc != task_class:
                    continue
                b = o.get("backend", "local-serial")
                cell = table.setdefault(tc, {}).setdefault(b, {"attempts": 0, "solved": 0, "cost": 0.0})
                cell["attempts"] += 1
                cell["solved"] += 1 if o.get("solved") else 0
                cell["cost"] = round(cell["cost"] + float(o.get("cost_usd", 0.0)), 6)
    except OSError:
        pass
    for tc, byb in table.items():
        for b, c in byb.items():
            c["pass_rate"] = round(c["solved"] / c["attempts"], 4) if c["attempts"] else 0.0
            c["avg_cost"] = round(c["cost"] / c["attempts"], 6) if c["attempts"] else 0.0
    return {"by_task_class": table}


# ── reflexion-style episodic notes ────────────────────────────────────────────
def add_note(task_class: str, note: str, worked: bool = True) -> None:
    try:
        Path(_notes_path()).parent.mkdir(parents=True, exist_ok=True)
        with open(_notes_path(), "a") as f:
            f.write(json.dumps({"ts": time.time(), "task_class": task_class,
                                "note": note[:500], "worked": bool(worked)}) + "\n")
    except OSError:
        pass


def recall_notes(task_class: str, n: int = 3) -> list[str]:
    out: list[str] = []
    try:
        with open(_notes_path()) as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
        for r in reversed(rows):
            if r.get("task_class") == task_class and r.get("note"):
                out.append(r["note"])
            if len(out) >= n:
                break
    except (OSError, ValueError):
        pass
    return out


# ── criticality / difficulty classifier ──────────────────────────────────────
def classify(task_text: str, path: Optional[str] = None) -> dict[str, Any]:
    """{critical, predicted_difficulty, recommended_backend, escalate, task_class}.
    Deterministic rules (criticality.py + length/novelty signals) first; a single cheap-LLM
    call is the fallback (not wired to a model here → degrades to rules). The recommended
    backend defaults to local-serial; escalation is only ADVISED, gated later by uplift."""
    tc = task_class_of(task_text)
    critical = False
    dims: list[str] = []
    try:
        import criticality
        c = criticality.criticality_classify(path or task_text, "python")
        critical, dims = bool(c.get("critical")), c.get("dimensions", [])
    except Exception:  # noqa: BLE001
        pass
    novel = False
    try:
        import research_core
        novel = research_core.classify_research_need(task_text).get("class") == "synthesis"
    except Exception:  # noqa: BLE001
        pass
    length = len((task_text or "").split())
    hard = critical or novel or length > 80 or tc in ("plan", "refactor")
    difficulty = "hard" if hard else "standard"
    # recommended backend is advisory; the default executor is always local-serial.
    rec = "local-serial"
    return {"task_class": tc, "critical": critical, "dimensions": dims,
            "predicted_difficulty": difficulty, "recommended_backend": rec,
            "escalate": bool(hard)}


# ── the routing policy ────────────────────────────────────────────────────────
def _fabric_available() -> bool:
    try:
        import pool
        return pool.available()
    except Exception:  # noqa: BLE001
        return False


def route(task_text: str, attempt: int = 0, verify_failed: bool = False,
          task_class: Optional[str] = None) -> dict[str, Any]:
    """Pick a backend + escalation decision. Default local-serial-free, one attempt. Escalate
    local→fabric→cloud-deepseek ONLY when (a) this isn't the cheap default attempt or a verify
    failed, and (b) the predicted uplift-per-dollar at the paid tier clears the floor (Phase-1
    numbers) or there is no paid history yet to refute it. Fabric (free) is preferred before
    cloud. Never blindly fans onto local. Deterministic fallback when the profiler is down."""
    tc = task_class or task_class_of(task_text)
    cls = classify(task_text)
    # default: cheap, free, one attempt
    decision = {"task_class": tc, "backend": "local-serial", "escalate": False,
                "reason": "default cheap local-serial single attempt",
                "difficulty": cls["predicted_difficulty"], "critical": cls["critical"]}
    escalate_warranted = verify_failed or (attempt > 0) or cls["escalate"]
    if not escalate_warranted:
        return decision

    # prefer free fabric first (parallel, $0 to rate limit)
    if _fabric_available():
        return {**decision, "backend": "fabric", "escalate": True,
                "reason": "escalate to free fabric (parallel) before spending on cloud"}

    # fabric exhausted/unavailable → consider PAID cloud, gated by uplift-per-dollar
    prof = _profiler()
    uplift_ok = True  # optimistic when there is no history to refute it
    upd = None
    if prof is not None:
        u = prof.uplift_per_dollar(tc)
        cell = (u.get("by_backend") or {}).get("cloud-deepseek")
        if cell and "uplift_per_dollar" in cell:
            upd = cell["uplift_per_dollar"]
            uplift_ok = (upd == float("inf")) or (upd >= UPLIFT_FLOOR)
    if uplift_ok:
        return {**decision, "backend": "cloud-deepseek", "escalate": True,
                "reason": f"fabric unavailable; cloud uplift-per-dollar={upd} ≥ {UPLIFT_FLOOR} "
                          "(or no history) → escalate paid", "uplift_per_dollar": upd}
    return {**decision, "backend": "local-serial", "escalate": False,
            "reason": f"fabric unavailable; cloud uplift-per-dollar={upd} < {UPLIFT_FLOOR} "
                      "→ stay local (paid escalation does not pay here)", "uplift_per_dollar": upd}


# ── outcome logging (the enforced write) ──────────────────────────────────────
def log_outcome(task_class: str, backend: str, solved: bool, cost_usd: float = 0.0,
                wall_ms: int = 0, failure_class: str = "", note: str = "") -> dict[str, Any]:
    """Append an outcome (profiler reads this) and update the bandit. reward = 1.0 solved else
    0.0. Optionally record a reflexion note. The closed loop between logged outcomes and
    routing."""
    row = {"ts": time.time(), "task_class": task_class, "backend": backend,
           "solved": bool(solved), "cost_usd": round(float(cost_usd), 6),
           "wall_ms": int(wall_ms), "failure_class": (failure_class if not solved else "")}
    try:
        Path(_outcomes_path()).parent.mkdir(parents=True, exist_ok=True)
        with open(_outcomes_path(), "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass
    bandit_update(task_class, backend, 1.0 if solved else 0.0)
    if note:
        add_note(task_class, note, worked=solved)
    return row


def router_stats() -> dict[str, Any]:
    return {"backends": list(BACKENDS), "ucb_c": UCB_C, "uplift_floor": UPLIFT_FLOOR,
            "bandit": _bandit_path(), "outcomes": _outcomes_path(),
            "fabric_available": _fabric_available()}
