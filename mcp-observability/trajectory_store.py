"""trajectory_store.py — append-only structured store of completed agent tasks
(Phase 4.1). This is the training signal for the self-improvement loop (GEPA skill
optimizer 4.2 + Trace2Skill distillation 4.3) and failure-localization (6.2).

One JSON object per completed task: {ts, task, plan, tool_calls, outcome, success,
verify_green, wall_time_s, skills_used, failure_mode}. JSONL (append-only, greppable,
no schema migration). Never raises.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

STORE_DIR = Path(os.path.expanduser(os.environ.get("TRAJECTORY_DIR", "~/.hermes-max/trajectories")))
STORE = STORE_DIR / "trajectories.jsonl"


def record(task: str, success: bool, tool_calls: list | None = None, plan: str = "",
           verify_green: bool | None = None, wall_time_s: float | None = None,
           skills_used: list | None = None, failure_mode: str = "", outcome: str = "",
           ts: float | None = None) -> dict[str, Any]:
    """Append one completed-task trajectory. Returns the stored record (+ ok)."""
    rec = {"ts": ts if ts is not None else time.time(),
           "task": task, "plan": plan, "tool_calls": tool_calls or [],
           "outcome": outcome, "success": bool(success), "verify_green": verify_green,
           "wall_time_s": wall_time_s, "skills_used": skills_used or [],
           "failure_mode": failure_mode}
    try:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STORE, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        rec["ok"] = True
    except Exception as e:  # noqa: BLE001
        rec["ok"] = False
        rec["error"] = str(e)[:200]
    return rec


def load(limit: int = 0, success: bool | None = None) -> list[dict[str, Any]]:
    """Load trajectories (newest last). Optional filter by success."""
    out: list[dict[str, Any]] = []
    try:
        with open(STORE) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                if success is None or bool(r.get("success")) == success:
                    out.append(r)
    except FileNotFoundError:
        return []
    return out[-limit:] if limit else out


def stats() -> dict[str, Any]:
    rows = load()
    n = len(rows)
    s = sum(1 for r in rows if r.get("success"))
    return {"trajectories": n, "successes": s, "failures": n - s, "store": str(STORE)}
