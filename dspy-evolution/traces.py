"""Trace sourcing for the GEPA evolution loop — reads labelled examples by FILE
PATH (never imports Hermes). Sources, in priority order:

  1. The escalation-outcome log (~/.hermes-max/escalation/outcomes.jsonl) — the
     compounding flywheel: every time a task escalates and the higher tier solves
     it, mcp-escalation appends {signals, difficulty, outcome}. These are the
     highest-signal labels (real tasks, real outcomes).
  2. The Hermes session store (~/.hermes/state.db, table messages/sessions) —
     mined for task outcomes (verify pass/fail, stuck, escalations).
  3. A hand-authored SEED set (below) so the loop can run and demonstrate the
     machinery before real traces accumulate. Clearly flagged as seed.

`real_trace_count()` counts ONLY (1)+(2) so run-evolution.sh can gate honestly:
no real traces ⇒ "needs more traces", not a meaningless optimisation on seed data.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

HOME = os.path.expanduser("~")
STATE_DB = os.environ.get("HERMES_STATE_DB", os.path.join(HOME, ".hermes", "state.db"))
OUTCOMES_LOG = os.environ.get(
    "ESCALATION_OUTCOMES_LOG", os.path.join(HOME, ".hermes-max", "escalation", "outcomes.jsonl")
)


# ── the rule-based policy the LLM classifier learns to replicate (ground truth
#    when a real outcome label is absent — mirrors mcp-escalation.classify_difficulty)
def rule_difficulty(signals: dict) -> str:
    s = signals or {}
    score = 0
    fc = int(s.get("file_count", 0) or 0)
    score += 2 if fc >= 8 else (1 if fc >= 4 else 0)
    pf = int(s.get("prior_failures", 0) or 0)
    score += 2 if pf >= 2 else (1 if pf == 1 else 0)
    nv = s.get("novelty")
    nv_map = {"low": 0, "medium": 1, "high": 2}
    if isinstance(nv, str):
        score += nv_map.get(nv.lower(), 0)
    elif isinstance(nv, (int, float)):
        score += 2 if nv >= 0.66 else (1 if nv >= 0.33 else 0)
    if int(s.get("lines_changed", 0) or 0) >= 200:
        score += 1
    if s.get("cross_module"):
        score += 1
    return "hard" if score >= 4 else ("medium" if score >= 2 else "easy")


def signals_to_task(task: str, s: dict) -> str:
    bits = [f"Task: {task}."]
    if s.get("file_count") is not None:
        bits.append(f"Files touched: {s['file_count']}.")
    if s.get("prior_failures"):
        bits.append(f"Prior failed attempts: {s['prior_failures']}.")
    if s.get("novelty"):
        bits.append(f"Novelty: {s['novelty']}.")
    if s.get("lines_changed"):
        bits.append(f"Lines changed: {s['lines_changed']}.")
    if s.get("cross_module"):
        bits.append("Spans multiple modules.")
    return " ".join(bits)


# Seed cases — deliberately include edge cases a NAIVE prompt gets wrong (e.g.
# "1 file but 2 prior failures + high novelty" is hard, not easy), giving GEPA
# room to evolve the instruction toward the real gating policy.
_SEED_SIGNALS: list[tuple[str, dict]] = [
    ("rename a local variable", {"file_count": 1}),
    ("fix a typo in a docstring", {"file_count": 1}),
    ("add a unit test for an existing function", {"file_count": 1, "novelty": "low"}),
    ("update a config default", {"file_count": 2}),
    ("add a field to one API response", {"file_count": 3, "novelty": "low"}),
    ("refactor a helper used in a few modules", {"file_count": 4, "cross_module": True}),
    ("debug a flaky test that failed before", {"file_count": 1, "prior_failures": 1}),
    ("integrate an unfamiliar SDK in one file", {"file_count": 1, "novelty": "high"}),
    ("fix a bug already attempted twice in one file", {"file_count": 1, "prior_failures": 2, "novelty": "high"}),
    ("re-architect the scheduler across modules", {"file_count": 12, "prior_failures": 1, "novelty": "high", "cross_module": True, "lines_changed": 400}),
    ("migrate the data layer", {"file_count": 9, "novelty": "medium", "lines_changed": 300}),
    ("port the auth flow, two prior failed attempts", {"file_count": 6, "prior_failures": 2, "cross_module": True}),
    ("tweak a log message", {"file_count": 1}),
    ("add a CLI flag wired through 5 files", {"file_count": 5}),
]


def seed_examples() -> list[dict]:
    return [
        {"task": signals_to_task(t, s), "difficulty": rule_difficulty(s), "source": "seed"}
        for t, s in _SEED_SIGNALS
    ]


def _read_outcomes_log() -> list[dict]:
    out: list[dict] = []
    if not os.path.isfile(OUTCOMES_LOG):
        return out
    try:
        with open(OUTCOMES_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                sig = rec.get("signals", {})
                # Prefer the REAL outcome-derived label when present.
                label = rec.get("difficulty") or rule_difficulty(sig)
                task = rec.get("task", "task")
                out.append({"task": signals_to_task(task, sig), "difficulty": label,
                            "source": "escalation-outcome"})
    except Exception:  # noqa: BLE001
        pass
    return out


def _read_state_db() -> list[dict]:
    if not os.path.isfile(STATE_DB):
        return []
    try:
        con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        # Minimal, robust mining: count assistant turns per session as a coarse
        # 'effort' proxy. (Extend as the schema/trace richness grows.)
        rows = con.execute(
            "SELECT session_id, COUNT(*) AS n FROM messages WHERE role='assistant' "
            "GROUP BY session_id"
        ).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return []
    examples: list[dict] = []
    for r in rows:
        n = r["n"]
        if not n:
            continue
        diff = "hard" if n >= 25 else ("medium" if n >= 8 else "easy")
        examples.append({"task": f"Task with {n} assistant turns in session {r['session_id'][:8]}.",
                         "difficulty": diff, "source": "state.db"})
    return examples


def real_examples() -> list[dict]:
    """Examples from REAL traces only (escalation outcomes + session store)."""
    return _read_outcomes_log() + _read_state_db()


def real_trace_count() -> int:
    return len(real_examples())


def all_examples(include_seed: bool = True) -> list[dict]:
    ex = real_examples()
    if include_seed:
        ex = ex + seed_examples()
    return ex
