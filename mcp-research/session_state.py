"""Per-session research rationing state — the mechanical budget the skill can't
enforce on its own (SWE-agent-style per-task budget + cooldown). File-based, keyed
by the session/task id, no LLM. Tracks:

  • deep_research cumulative wall-time + last-call timestamp + call count
    (R-Stage 2: budget RESEARCH_BUDGET_S + cooldown RESEARCH_COOLDOWN_S);
  • lighter-tool attempts with their query text (R-Stage 3: the exhaustion-first
    precondition + semantic-relatedness check);
  • whether the corpus was checked for a question (R-Stage 1/3 precondition).

Gates on EXTERNAL signals (elapsed budget, time since last call, cheaper-tool
attempts), never the model's self-confidence — per the adaptive-retrieval research.
Never raises: a broken state file degrades to "allow" so rationing never wedges a
task, but every decision is logged so the operator sees what happened.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.path.expanduser(os.environ.get("RESEARCH_STATE_DIR", "~/.hermes-max/research")))
SESSION_DIR = STATE_DIR / "sessions"

RESEARCH_BUDGET_S = float(os.environ.get("RESEARCH_BUDGET_S", "900"))
RESEARCH_COOLDOWN_S = float(os.environ.get("RESEARCH_COOLDOWN_S", "1800"))


def session_id() -> str:
    return (os.environ.get("WATCHDOG_TASK_ID")
            or os.environ.get("HERMES_TASK_ID")
            or "default")


def _path(sid: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (sid or "default"))
    return SESSION_DIR / f"{safe}.json"


def load(sid: str | None = None) -> dict[str, Any]:
    sid = sid or session_id()
    try:
        with open(_path(sid)) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        return {}


def save(sid: str, st: dict[str, Any]) -> None:
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        p = _path(sid)
        tmp = str(p) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 - best-effort
        pass


def _dr(st: dict[str, Any]) -> dict[str, Any]:
    dr = st.get("deep_research")
    if not isinstance(dr, dict):
        dr = {"last_ts": 0.0, "cumulative_s": 0.0, "calls": 0}
    return dr


def record_research(elapsed_s: float, sid: str | None = None) -> None:
    """Record a completed deep_research call: bump last_ts, cumulative time, count."""
    sid = sid or session_id()
    st = load(sid)
    dr = _dr(st)
    dr["last_ts"] = time.time()
    dr["cumulative_s"] = float(dr.get("cumulative_s", 0.0)) + max(0.0, float(elapsed_s))
    dr["calls"] = int(dr.get("calls", 0)) + 1
    st["deep_research"] = dr
    save(sid, st)


def mark_corpus_checked(sid: str | None = None) -> None:
    sid = sid or session_id()
    st = load(sid)
    st["corpus_checked_ts"] = time.time()
    save(sid, st)


def research_gate(est_s: float = 0.0, sid: str | None = None) -> dict[str, Any]:
    """Budget + cooldown gate for deep_research (R-Stage 2). Returns
    {allowed, reason, cooldown_remaining_s, cumulative_s, budget_s, calls}.
    allowed=False when a call fired < RESEARCH_COOLDOWN_S ago, or the cumulative
    research time this session would exceed RESEARCH_BUDGET_S."""
    sid = sid or session_id()
    dr = _dr(load(sid))
    now = time.time()
    last = float(dr.get("last_ts", 0.0))
    cum = float(dr.get("cumulative_s", 0.0))
    calls = int(dr.get("calls", 0))
    since = now - last if last else None
    cooldown_remaining = max(0.0, RESEARCH_COOLDOWN_S - since) if since is not None else 0.0

    if since is not None and since < RESEARCH_COOLDOWN_S:
        return {"allowed": False, "reason": "cooldown",
                "cooldown_remaining_s": round(cooldown_remaining, 1),
                "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
    if cum + max(0.0, est_s) > RESEARCH_BUDGET_S:
        return {"allowed": False, "reason": "budget_exhausted",
                "cooldown_remaining_s": 0.0,
                "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
    return {"allowed": True, "reason": "ok", "cooldown_remaining_s": 0.0,
            "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
