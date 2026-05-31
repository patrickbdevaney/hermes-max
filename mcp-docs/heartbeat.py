"""heartbeat.py — stamp a watchdog liveness heartbeat from a long-running MCP
server WITHOUT importing mcp-watchdog (each server stays an independent process
with its own venv; this mirrors the self-contained otel_emit pattern).

WHY THIS EXISTS — the finish-line killer:
A long blocking local-model inference call (deep_research synthesis, per-source
distillation, claim verification, docs distill) emits NO progress signal on its
own. The watchdog then sees silence past heartbeat_timeout_s and kills the tool
right at the finish line — after every source was gathered and the only thing
left was the single synthesis inference. Calling ``beat(tool)`` immediately BEFORE
and immediately AFTER every such inference closes that gap: the tool is provably
"slow-but-alive", not hung.

It reproduces watchdog_core.record_heartbeat's two effects without the import:
  1. write heartbeats[tool] = {ts, progress, done, total} into the SHARED watchdog
     state file ($WATCHDOG_STATE_DIR/<task_id>.json) — the same file check_stall
     reads to resolve hung-vs-waiting, so a heartbeating tool is never killed for
     being slow;
  2. emit a tool_heartbeat span via the local otel_emit, which the live tool-call
     log bridges to the operator stream (so synthesis shows heartbeats, not 130s
     of silence).

task_id: resolved from $WATCHDOG_TASK_ID, then $HERMES_TASK_ID, else "default" —
matching whatever the agent passes to check_stall(task_id=...). The otel/live-log
path does not depend on task_id, so progress is always visible regardless.

Never raises: a broken heartbeat must never take down the server or the inference.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import otel_emit  # self-contained per-server span emitter
except Exception:  # noqa: BLE001 - otel optional; live-log path simply degrades
    otel_emit = None  # type: ignore

_STATE_DIR = Path(os.path.expanduser(
    os.environ.get("WATCHDOG_STATE_DIR", "~/.hermes-max/watchdog")))


def _task_id() -> str:
    return (os.environ.get("WATCHDOG_TASK_ID")
            or os.environ.get("HERMES_TASK_ID")
            or "default")


def _state_path(task_id: str) -> Path:
    # MUST match watchdog_core._state_path's sanitization exactly so the file the
    # research process writes is the one the watchdog process reads.
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (task_id or "default"))
    return _STATE_DIR / f"{safe}.json"


def beat(tool_name: str, progress: str | None = None,
         done: int | None = None, total: int | None = None) -> None:
    """Stamp one liveness heartbeat for ``tool_name``. Call BEFORE and AFTER every
    blocking local-model inference call inside a long MCP tool. Best-effort."""
    now = time.time()
    task_id = _task_id()
    # 1. shared state file — preserve every other key (budget/progress that the
    #    watchdog process owns); only mutate heartbeats[tool]. Atomic replace.
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        p = _state_path(task_id)
        try:
            with open(p) as f:
                st = json.load(f)
        except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
            st = {}
        if not isinstance(st, dict):
            st = {}
        hb = st.get("heartbeats")
        if not isinstance(hb, dict):
            hb = {}
        hb[tool_name] = {"ts": now, "progress": progress, "done": done, "total": total}
        st["heartbeats"] = hb
        tmp = str(p) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 - liveness write is best-effort, always
        pass
    # 2. live-log / otel span (process-independent; no task_id match needed)
    if otel_emit is not None:
        pct = (round(100 * done / total, 1) if (done is not None and total) else None)
        try:
            otel_emit.record("tool_heartbeat", {"task_id": task_id, "tool": tool_name,
                                                "progress": progress, "done": done,
                                                "total": total, "pct": pct})
        except Exception:  # noqa: BLE001
            pass
