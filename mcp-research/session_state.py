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
# Relatedness threshold for "a lighter tool was attempted on a RELATED query".
# Embeddings (cosine > 0.6) are the ideal signal per the research, but this
# deployment has no embed endpoint (EMBED_BASE_URL blank), so we fall back to
# lexical token-set Jaccard with its own (lower) threshold. Both env-overridable.
RESEARCH_LIGHTER_SIM = float(os.environ.get("RESEARCH_LIGHTER_SIM", "0.6"))            # embedding cosine
RESEARCH_LIGHTER_LEXICAL_SIM = float(os.environ.get("RESEARCH_LIGHTER_LEXICAL_SIM", "0.2"))  # Jaccard fallback
RESEARCH_LIGHTER_MAX_AGE_S = float(os.environ.get("RESEARCH_LIGHTER_MAX_AGE_S", "3600"))


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


import re as _re


def _tokens(text: str) -> set[str]:
    return {t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _lexical_sim(a: str, b: str) -> float:
    """Token-set Jaccard — the embedding-free relatedness fallback."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def record_lighter_tool(tool: str, query: str, sid: str | None = None) -> None:
    """Record an AGENT-initiated lighter-tool call (search_code / fetch_clean /
    research_topic) with its query. Called from each of those tools' servers so the
    exhaustion-first gate can see that cheaper tools were tried first (R-Stage 3).
    Keeps the most recent 40 entries."""
    sid = sid or session_id()
    st = load(sid)
    lt = st.get("lighter_tools")
    if not isinstance(lt, list):
        lt = []
    lt.append({"tool": tool, "query": (query or "")[:300], "ts": time.time()})
    st["lighter_tools"] = lt[-40:]
    save(sid, st)


def lighter_tools_attempted(question: str, sid: str | None = None) -> dict[str, Any]:
    """Has a lighter tool been attempted on a RELATED query this session? The
    exhaustion-first precondition for escalating to deep_research (R-Stage 3).
    Relatedness: embedding cosine > RESEARCH_LIGHTER_SIM if an embedder is wired in
    (none here), else lexical Jaccard > RESEARCH_LIGHTER_LEXICAL_SIM. Returns
    {attempted, best_sim, best_tool, best_query, method, considered}."""
    sid = sid or session_id()
    lt = load(sid).get("lighter_tools") or []
    now = time.time()
    best = {"sim": 0.0, "tool": None, "query": None}
    considered = 0
    for e in lt:
        if not isinstance(e, dict):
            continue
        if now - float(e.get("ts", 0)) > RESEARCH_LIGHTER_MAX_AGE_S:
            continue
        considered += 1
        s = _lexical_sim(question, e.get("query", ""))
        if s > best["sim"]:
            best = {"sim": s, "tool": e.get("tool"), "query": e.get("query")}
    attempted = best["sim"] >= RESEARCH_LIGHTER_LEXICAL_SIM
    return {"attempted": attempted, "best_sim": round(best["sim"], 3),
            "best_tool": best["tool"], "best_query": best["query"],
            "method": "lexical-jaccard", "considered": considered,
            "threshold": RESEARCH_LIGHTER_LEXICAL_SIM}


def note_lighter_tools_attempted(query: str, sid: str | None = None) -> None:
    """Explicit agent assertion that it tried lighter tools and found them
    insufficient for `query` — recorded as a synthetic lighter-tool attempt so the
    exhaustion gate is satisfied (the directive's explicit-precondition path)."""
    record_lighter_tool("explicit", query, sid)


def research_gate(est_s: float = 0.0, sid: str | None = None) -> dict[str, Any]:
    """Budget + cooldown gate for deep_research (R-Stage 2). Returns
    {allowed, reason, cooldown_remaining_s, cumulative_s, budget_s, calls}.
    allowed=False when a call fired < RESEARCH_COOLDOWN_S ago, or the cumulative
    research time this session has already reached RESEARCH_BUDGET_S. (est_s is
    accepted for call-site compatibility but no longer pre-charged — see below.)"""
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
    # Block only once the budget is ACTUALLY spent — do NOT pre-charge est_s.
    # Pre-charging the (full) wall budget meant any prior call leaving cumulative_s
    # > 0 made `cum + est_s > budget` true forever, permanently locking out
    # deep_research after the very first call (est_s defaults to the whole
    # WALL_BUDGET_S == RESEARCH_BUDGET_S). Per-call wall time is independently
    # capped inside deep_research; here we only enforce the cumulative-spend cap.
    # Cooldown (above) handles spacing/re-fire; this handles total time.
    if cum >= RESEARCH_BUDGET_S:
        return {"allowed": False, "reason": "budget_exhausted",
                "cooldown_remaining_s": 0.0,
                "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
    return {"allowed": True, "reason": "ok", "cooldown_remaining_s": 0.0,
            "cumulative_s": round(cum, 1), "budget_s": RESEARCH_BUDGET_S, "calls": calls}
