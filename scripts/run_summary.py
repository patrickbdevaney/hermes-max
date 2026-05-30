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


# ── bottleneck-analysis buckets (Stage 7c) ────────────────────────────────────
# Every second of wall-clock falls into exactly one bucket so the operator can SEE
# whether the advanced features earn their latency:
#   inference  — local model thinking/generation (irreducible real work)
#   tool-work  — tool execution doing real work (crawl, tests, indexing, retrieval)
#   artificial — waiting on rate-limited APIs, 429/5xx backoff+retries, redundant
#                sequential calls that could be concurrent, MCP overhead
_INFERENCE_TOOLS = {
    "synth", "steer", "escalate", "parallel_draft", "draft", "plan", "plan_research",
    "develop_queries", "synthesize", "verify_claims", "llm", "_llm", "classify",
    "best_of_n", "select",
}
_TOOLWORK_TOOLS = {
    "index_repo", "scan_repo", "search_code", "rag_query", "find_similar",
    "get_symbol_context", "repo_map", "kg_record", "kg_recall", "kg_query",
    "fetch_clean", "crawl", "verify", "checkpoint", "revert_to_last_green",
    "deep_research", "ingest_doc", "record_relation", "record_entity",
}
# A reason/fallback string matching any of these marks the time as ARTIFICIAL.
_ARTIFICIAL_PAT = ("429", "rate", "backoff", "retry", "5xx", "503", "502",
                   "too many requests", "quota", "throttle", "tpm")


def _base_tool(name: str) -> str:
    # index_repo[sample] -> index_repo ; kg_recall -> kg_recall
    return name.split("[", 1)[0]


def classify_bucket(tool: str, reason: str | None = None,
                    explicit: str | None = None) -> str:
    if explicit in ("inference", "tool-work", "artificial"):
        return explicit
    r = (reason or "").lower()
    if any(p in r for p in _ARTIFICIAL_PAT):
        return "artificial"
    b = _base_tool(tool)
    if b in _INFERENCE_TOOLS:
        return "inference"
    if b in _TOOLWORK_TOOLS:
        return "tool-work"
    return "tool-work"  # unknown real work defaults to tool-work, not free


def aggregate(rows: list[dict]) -> dict:
    tools: dict[str, dict] = {}

    def t(name: str) -> dict:
        return tools.setdefault(name, {"calls": 0, "total_s": 0.0, "fails": 0,
                                       "fallbacks": 0, "est_s": 0.0, "est_n": 0,
                                       "act_s": 0.0, "act_n": 0, "heartbeats": 0})
    decisions = []
    buckets = {"inference": 0.0, "tool-work": 0.0, "artificial": 0.0}
    artificial_by_cause: dict[str, dict] = {}

    def _bucket(name: str, secs: float, reason: str | None, explicit: str | None) -> None:
        b = classify_bucket(name, reason, explicit)
        buckets[b] += max(0.0, secs)
        if b == "artificial":
            cause = "rate-limit/backoff"
            rl = (reason or "").lower()
            for p in _ARTIFICIAL_PAT:
                if p in rl:
                    cause = reason or p
                    break
            c = artificial_by_cause.setdefault(cause, {"count": 0, "secs": 0.0})
            c["count"] += 1
            c["secs"] += max(0.0, secs)

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
                _bucket(name, float(r["secs"]), None, r.get("bucket"))
        elif kind == "fail":
            d = t(name); d["fails"] += 1
            if r.get("fallback"):
                d["fallbacks"] += 1
            secs = float(r["secs"]) if r.get("secs") is not None else 0.0
            d["total_s"] += secs
            # a failure/fallback is classified by its reason (rate-limit -> artificial)
            _bucket(name, secs, r.get("reason") or r.get("fallback"), r.get("bucket"))
        elif kind == "heartbeat":
            t(name)["heartbeats"] += 1
        elif kind == "decision":
            decisions.append(r)
            # a kill/backoff decision with a duration contributes to artificial
            if r.get("secs") is not None:
                _bucket(r.get("decision", "?"), float(r["secs"]),
                        r.get("reason"), r.get("bucket"))
    return {"tools": tools, "decisions": decisions, "events": len(rows),
            "buckets": buckets, "artificial_by_cause": artificial_by_cause}


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
    # ── bottleneck timing split (Stage 7c) ───────────────────────────────────
    b = agg.get("buckets", {})
    btot = sum(b.values())
    out.append("")
    if btot > 0:
        def pct(x: float) -> float:
            return 100 * x / btot
        out.append("  bottleneck split (where wall-clock went):")
        out.append(f"    inference {b.get('inference',0):.1f}s ({pct(b.get('inference',0)):.0f}%) · "
                   f"tool-work {b.get('tool-work',0):.1f}s ({pct(b.get('tool-work',0)):.0f}%) · "
                   f"artificial {b.get('artificial',0):.1f}s ({pct(b.get('artificial',0)):.0f}%)")
        # name the dominant artificial cost when it's a meaningful fraction
        if btot and pct(b.get("artificial", 0)) >= 15:
            causes = agg.get("artificial_by_cause", {})
            if causes:
                top = max(causes.items(), key=lambda kv: kv[1]["secs"])
                out.append(f"    ⚠ artificial dominated by {top[0]} — "
                           f"{top[1]['count']} occurrence(s), {top[1]['secs']:.0f}s "
                           "(a feature is wasting the agent's time — gate it more conservatively)")
        elif b.get("artificial", 0) == 0:
            out.append("    ✓ no artificial cost (no rate-limit backoff / redundant waiting)")

    dec = agg["decisions"]
    if dec:
        out.append("")
        out.append(f"  decisions ({len(dec)}):")
        for r in dec[-12:]:
            mark = "✗" if r.get("error") else "•"
            out.append(f"    {mark} {r.get('decision')} → {r.get('choice')} | {r.get('reason')}")
    return "\n".join(out)


def main() -> None:
    argv = [a for a in sys.argv if a != "--json"]
    as_json = "--json" in sys.argv
    path = _log_path(argv)
    agg = aggregate(load(path))
    if as_json:
        b = agg.get("buckets", {})
        print(json.dumps({
            "events": agg["events"],
            "tools": len(agg["tools"]),
            "wall_s": round(sum(t["total_s"] for t in agg["tools"].values()), 2),
            "buckets": {k: round(v, 2) for k, v in b.items()},
            "artificial_by_cause": agg.get("artificial_by_cause", {}),
            "calls": sum(t["calls"] for t in agg["tools"].values()),
        }))
    else:
        print(fmt(agg))


if __name__ == "__main__":
    main()
