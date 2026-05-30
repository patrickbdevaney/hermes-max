#!/usr/bin/env python3
"""run_summary.py — per-task tool-call summary table (Stage 3).

Reads the live tool-call log (live.jsonl) and prints a table of every tool called
this task: count, total time, failures, fallbacks, and est-vs-actual duration — so
after a run the operator sees exactly where time went and what fell back.

Stdlib-only (runs on a freshly-cloned machine, any venv). Best-effort: a missing
or partial log prints an empty table, never an error.

Usage:
  run_summary.py [path/to/live.jsonl]      # defaults to $HERMES_MAX_LOG_DIR/live.jsonl

Stage 7 extends this with the inference / tool-work / artificial timing split.
"""

from __future__ import annotations

import json
import os
import sys


def _log_path(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1]:
        return os.path.expanduser(argv[1])
    d = os.path.expanduser(os.environ.get(
        "HERMES_MAX_LOG_DIR", os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
    return os.path.join(d, "live.jsonl")


def load(path: str) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception:  # noqa: BLE001 - skip a torn line
                    continue
    except FileNotFoundError:
        pass
    return rows


def aggregate(rows: list[dict]) -> dict:
    tools: dict[str, dict] = {}

    def t(name: str) -> dict:
        return tools.setdefault(name, {"calls": 0, "total_s": 0.0, "fails": 0,
                                       "fallbacks": 0, "est_s": 0.0, "est_n": 0,
                                       "act_s": 0.0, "act_n": 0, "heartbeats": 0})
    decisions = []
    for r in rows:
        kind = r.get("kind")
        name = r.get("tool") or "?"
        if kind == "start":
            d = t(name); d["calls"] += 1
            if r.get("est_s"):
                d["est_s"] += float(r["est_s"]); d["est_n"] += 1
        elif kind == "end":
            d = t(name)
            if r.get("secs") is not None:
                d["total_s"] += float(r["secs"]); d["act_s"] += float(r["secs"]); d["act_n"] += 1
            if r.get("est_s") and not r.get("_counted_est"):
                pass
        elif kind == "fail":
            d = t(name); d["fails"] += 1
            if r.get("fallback"):
                d["fallbacks"] += 1
            if r.get("secs") is not None:
                d["total_s"] += float(r["secs"])
        elif kind == "heartbeat":
            t(name)["heartbeats"] += 1
        elif kind == "decision":
            decisions.append(r)
    return {"tools": tools, "decisions": decisions, "events": len(rows)}


def fmt(agg: dict) -> str:
    tools = agg["tools"]
    out = []
    out.append("═══ per-task tool-call summary ═══")
    if not tools:
        out.append("  (no tool calls recorded yet)")
        return "\n".join(out)
    hdr = f"  {'tool':<18} {'calls':>5} {'total_s':>8} {'fails':>5} {'fallbk':>6} {'est~':>7} {'act~':>7} {'hb':>4}"
    out.append(hdr)
    out.append("  " + "─" * (len(hdr) - 2))
    tot = {"calls": 0, "total_s": 0.0, "fails": 0, "fallbacks": 0, "hb": 0}
    for name in sorted(tools, key=lambda n: -tools[n]["total_s"]):
        d = tools[name]
        est = (d["est_s"] / d["est_n"]) if d["est_n"] else 0.0
        act = (d["act_s"] / d["act_n"]) if d["act_n"] else 0.0
        out.append(f"  {name:<18} {d['calls']:>5} {d['total_s']:>8.1f} {d['fails']:>5} "
                   f"{d['fallbacks']:>6} {est:>6.0f}s {act:>6.1f}s {d['heartbeats']:>4}")
        tot["calls"] += d["calls"]; tot["total_s"] += d["total_s"]
        tot["fails"] += d["fails"]; tot["fallbacks"] += d["fallbacks"]; tot["hb"] += d["heartbeats"]
    out.append("  " + "─" * (len(hdr) - 2))
    out.append(f"  {'TOTAL':<18} {tot['calls']:>5} {tot['total_s']:>8.1f} {tot['fails']:>5} "
               f"{tot['fallbacks']:>6} {'':>7} {'':>7} {tot['hb']:>4}")
    dec = agg["decisions"]
    if dec:
        out.append("")
        out.append(f"  decisions ({len(dec)}):")
        for r in dec[-12:]:
            mark = "✗" if r.get("error") else "•"
            out.append(f"    {mark} {r.get('decision')} → {r.get('choice')} | {r.get('reason')}")
    return "\n".join(out)


def main() -> None:
    path = _log_path(sys.argv)
    agg = aggregate(load(path))
    print(fmt(agg))


if __name__ == "__main__":
    main()
