"""livelog — the operator-facing LIVE tool-call stream (Stage 3 observability).

A single tailable, structured, human-readable log of every MCP tool call: which
tool, what input, how long, what came back, every heartbeat, every fallback, and
every routing/kill DECISION with its reason. This is the REAL-TIME clarity view
(`scripts/watch.sh` tails it); Phoenix/OTel remains the post-hoc analysis view.
Both are fed by the same events — livelog NEVER replaces the spans, it emits
alongside them, and it degrades silently (a logging failure never breaks a tool).

Two sinks, written best-effort:
  * live.jsonl — one JSON object per event (the machine source for run-summary).
  * live.log   — the pretty, coloured-by-watch.sh human line.

Verbosity (HERMES_MAX_VERBOSITY, default `verbose`):
  quiet   — errors / kills / fallbacks only
  normal  — + tool start / finish
  verbose — + heartbeats, look-ahead estimates, input/output summaries  (DEFAULT)
  debug   — + full payloads
The JSONL sink always records start/heartbeat/end/decision so the per-task summary
stays complete regardless of the console verbosity.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

_LEVELS = {"quiet": 0, "normal": 1, "verbose": 2, "debug": 3}


def _verbosity() -> int:
    return _LEVELS.get(os.environ.get("HERMES_MAX_VERBOSITY", "verbose").strip().lower(), 2)


def _log_dir() -> str:
    d = os.path.expanduser(os.environ.get(
        "HERMES_MAX_LOG_DIR", os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
    return d


def _now_hms() -> str:
    return time.strftime("%H:%M:%S")


def _brief(obj: Any, limit: int = 160) -> str:
    """One-line summary of an input/output value (full payload only at debug)."""
    if obj is None:
        return ""
    try:
        if _verbosity() >= _LEVELS["debug"]:
            s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
        elif isinstance(obj, dict):
            parts = []
            for k, v in obj.items():
                vs = v if isinstance(v, (str, int, float, bool)) else type(v).__name__
                parts.append(f"{k}={vs}")
            s = ", ".join(parts)
        elif isinstance(obj, (list, tuple)):
            s = f"[{len(obj)} items]"
        else:
            s = str(obj)
    except Exception:  # noqa: BLE001
        s = str(obj)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _write(record: dict, pretty: str, min_level: int) -> None:
    """Best-effort dual-sink write. Never raises."""
    record = {"ts": time.time(), "hms": _now_hms(), **record}
    d = _log_dir()
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "live.jsonl"), "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:  # noqa: BLE001 - observability is best-effort, always
        pass
    if _verbosity() >= min_level and pretty:
        line = f"[{record['hms']}] {pretty}"
        try:
            with open(os.path.join(d, "live.log"), "a") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass
        if os.environ.get("HERMES_MAX_LOG_STDOUT", "").lower() in ("1", "true", "yes"):
            try:
                print(line, flush=True)
            except Exception:  # noqa: BLE001
                pass


# ── public emit API ──────────────────────────────────────────────────────────
def tool_start(tool: str, server: str | None = None, inp: Any = None,
               est_s: float | None = None, task_id: str | None = None) -> None:
    loc = f" ({server})" if server else ""
    est = f" | est: ~{est_s:.0f}s" if est_s else ""
    ins = f" | input: {{{_brief(inp)}}}" if inp is not None else ""
    _write({"kind": "start", "tool": tool, "server": server, "input": _brief(inp, 400),
            "est_s": est_s, "task_id": task_id},
           f"→ TOOL {tool}{loc}{ins}{est}", _LEVELS["normal"])


def heartbeat(tool: str, done: int | None = None, total: int | None = None,
              elapsed_s: float | None = None, note: str | None = None,
              item: str | None = None, per_item: str | None = None,
              eta_s: float | None = None) -> None:
    """tqdm-style progress (Stage 7a): current item N/total, per-item timing, a
    running ETA + elapsed — so the operator sees real movement and can tell instantly
    whether it's progressing or stuck on one slow item. ETA is derived from
    done/total/elapsed when not supplied."""
    if eta_s is None and done and total and elapsed_s and done > 0:
        eta_s = max(0.0, elapsed_s * (total - done) / done)
    prog = ""
    if done is not None and total:
        prog = f" [{done}/{total}] {100*done/total:.0f}%"
    it = f" | {item}" if item else ""
    pit = f" | {per_item}" if per_item else (f" | {note}" if note and not item else "")
    el = f" | elapsed {elapsed_s:.0f}s" if elapsed_s else ""
    eta = f" · ETA ~{eta_s:.0f}s" if eta_s is not None else ""
    _write({"kind": "heartbeat", "tool": tool, "done": done, "total": total,
            "elapsed_s": elapsed_s, "note": note, "item": item,
            "per_item": per_item, "eta_s": (round(eta_s, 1) if eta_s is not None else None)},
           f"⟳ {tool}{prog}{it}{pit}{el}{eta}", _LEVELS["verbose"])


def tool_ok(tool: str, secs: float | None = None, ret: Any = None,
            est_s: float | None = None) -> None:
    t = f" | {secs:.1f}s" if secs is not None else ""
    ev = ""
    if est_s and secs is not None:
        ev = f" (est ~{est_s:.0f}s)"
    rs = f" | returned: {{{_brief(ret)}}}" if ret is not None else ""
    _write({"kind": "end", "tool": tool, "ok": True, "secs": secs, "est_s": est_s,
            "returned": _brief(ret, 400)},
           f"✓ {tool} OK{t}{ev}{rs}", _LEVELS["normal"])


def tool_fail(tool: str, reason: str | None = None, fallback: str | None = None,
              secs: float | None = None) -> None:
    fb = f" | falling back to: {fallback}" if fallback else ""
    _write({"kind": "fail", "tool": tool, "ok": False, "reason": reason,
            "fallback": fallback, "secs": secs},
           f"✗ {tool} FAILED | reason: {reason or 'unknown'}{fb}", _LEVELS["quiet"])


def tool_slow(tool: str, elapsed_s: float, est_s: float | None = None) -> None:
    ev = f", est was {est_s:.0f}s" if est_s else ""
    _write({"kind": "slow", "tool": tool, "elapsed_s": elapsed_s, "est_s": est_s},
           f"⚠ {tool} SLOW | {elapsed_s:.0f}s elapsed{ev}, still heartbeating (not killed)",
           _LEVELS["verbose"])


def decision(kind: str, choice: str, reason: str, error: bool = False) -> None:
    """Routing / fallback / kill DECISION with its REASON — so the operator never
    wonders 'why did it do that' (conductor route, RAG->BM25, source skip, kill)."""
    mark = "✗" if error else "•"
    _write({"kind": "decision", "decision": kind, "choice": choice, "reason": reason,
            "error": error},
           f"{mark} DECISION {kind} → {choice} | reason: {reason}",
           _LEVELS["quiet"] if error else _LEVELS["normal"])


# ── span forwarder: piggyback on every server's otel_emit.record() call ───────
# Maps the OTel span events servers already emit into the right live-log lines, so
# wiring livelog into otel_emit gives broad coverage without touching every tool.
def forward(span_name: str, attrs: dict | None = None, status: str = "ok") -> None:
    a = attrs or {}
    tool = a.get("tool") or a.get("task_id") or span_name
    try:
        if span_name == "tool_estimate":
            est = a.get("est_s")
            base = f"⊙ {tool} look-ahead est ~{est}s (ceiling {a.get('ceiling_s')}s)"
            if a.get("exceeds_ceiling"):
                decision("look-ahead", f"{tool} est exceeds ceiling",
                         a.get("basis", "estimate exceeds hard ceiling"), error=True)
            else:
                _write({"kind": "estimate", "tool": tool, **a}, base + f" — {a.get('basis','')}",
                       _LEVELS["verbose"])
        elif span_name in ("tool_heartbeat", "index_progress", "research_progress"):
            heartbeat(tool, done=a.get("done"), total=a.get("total"), note=a.get("note"),
                      elapsed_s=a.get("elapsed_s"), item=a.get("item"),
                      per_item=a.get("per_item"), eta_s=a.get("eta_s"))
        elif span_name == "tool_slow_but_alive":
            tool_slow(tool, float(a.get("elapsed_s", 0) or 0), a.get("budget_s"))
        elif span_name in ("tool_killed_hung", "poll_hang_caught"):
            decision("kill", f"{tool} killed", a.get("reason", "hung"), error=True)
        elif span_name == "spiral_detected":
            decision("spiral", "stop & replan", a.get("reason", "reasoning spiral"), error=True)
        elif span_name == "no_progress":
            decision("no-progress", "revert & retry",
                     f"no forward progress x{a.get('count')}", error=True)
        elif span_name == "budget_exceeded":
            decision("budget", "checkpoint & stop",
                     f"limits: {a.get('limits')}", error=True)
        elif span_name == "index_repo_done":
            tool_ok("index_repo", ret={k: a.get(k) for k in ("files", "chunks", "mode")})
        else:
            # generic span -> debug-level line only (full visibility without noise)
            _write({"kind": "span", "span": span_name, **a},
                   f"· span {span_name} {_brief(a)}", _LEVELS["debug"])
    except Exception:  # noqa: BLE001 - never let the live log break a tool
        pass
