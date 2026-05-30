#!/usr/bin/env python3
"""Standalone smoke test for the Stage-3 live observability layer. No network.

Asserts:
  * livelog writes both sinks (live.jsonl machine + live.log pretty);
  * verbosity gates the pretty line (quiet hides heartbeats; verbose shows them)
    while the JSONL keeps them for the summary;
  * the otel_emit -> livelog BRIDGE turns a server's span event into a live line
    (so wiring it into otel_emit gives broad coverage for free);
  * decision transparency: a routing/kill DECISION logs choice + reason;
  * run_summary aggregates calls / time / fails / fallbacks / est-vs-actual;
  * a logging failure (unwritable dir) NEVER raises — it degrades silently.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "scripts"))


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _fail(m: str) -> None:
    print(f"  FAIL: {m}")
    sys.exit(1)


def _read(p: Path) -> str:
    return p.read_text() if p.exists() else ""


def main() -> None:
    print("[observability] live tool-call log + bridge + summary")
    tmp = tempfile.mkdtemp(prefix="hmx-livelog-")
    os.environ["HERMES_MAX_LOG_DIR"] = tmp
    os.environ["HERMES_MAX_VERBOSITY"] = "verbose"

    import livelog
    importlib.reload(livelog)

    jsonl = Path(tmp) / "live.jsonl"
    pretty = Path(tmp) / "live.log"

    # 1. a full tool lifecycle
    livelog.tool_start("index_repo", server="rag:9102", inp={"path": "/repo"}, est_s=98)
    livelog.heartbeat("index_repo", done=400, total=1240, elapsed_s=45)
    livelog.tool_ok("index_repo", secs=92.3, ret={"files": 1240, "mode": "hybrid"}, est_s=98)
    j = _read(jsonl)
    p = _read(pretty)
    for needle in ("→ TOOL index_repo", "rag:9102", "est: ~98s"):
        if needle not in p:
            _fail(f"tool_start pretty line missing {needle!r}: {p!r}")
    if '"kind": "start"' not in j or '"kind": "end"' not in j:
        _fail(f"jsonl missing start/end records: {j!r}")
    if "⟳ index_repo" not in p or "400/1240" not in p:
        _fail(f"verbose heartbeat not shown: {p!r}")
    _ok("tool_start/heartbeat/tool_ok write both sinks with input/est/progress")

    # 2. verbosity gating: quiet hides heartbeats from the pretty log, keeps JSONL
    os.environ["HERMES_MAX_VERBOSITY"] = "quiet"
    before_p = len(_read(pretty))
    before_j = len(_read(jsonl))
    livelog.heartbeat("deep_research", done=2, total=12, note="arxiv source")
    if len(_read(pretty)) != before_p:
        _fail("quiet verbosity should NOT write a heartbeat to the pretty log")
    if len(_read(jsonl)) <= before_j:
        _fail("JSONL should still record the heartbeat for the summary")
    _ok("verbosity gates the pretty line but the JSONL stays complete")
    os.environ["HERMES_MAX_VERBOSITY"] = "verbose"

    # 3. decision transparency
    livelog.decision("route", "synth (DeepInfra)", "task is generative, not steering")
    if "DECISION route → synth (DeepInfra)" not in _read(pretty):
        _fail("decision line missing")
    _ok("a routing DECISION logs choice + reason")

    # 4. otel_emit -> livelog bridge (load a real server's otel_emit by path)
    spec = importlib.util.spec_from_file_location(
        "wd_otel_emit", str(REPO / "mcp-watchdog" / "otel_emit.py"))
    wd_otel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd_otel)  # type: ignore[union-attr]
    wd_otel.record("tool_estimate", {"tool": "deep_research", "est_s": 360,
                                     "ceiling_s": 900, "exceeds_ceiling": False,
                                     "basis": "12 queries x 30s = est ~360s"})
    wd_otel.record("tool_killed_hung", {"tool": "curl", "elapsed_s": 600,
                                        "reason": "silent past budget"}, status="error")
    p = _read(pretty)
    if "deep_research look-ahead" not in p:
        _fail(f"bridge did not forward tool_estimate: {p!r}")
    if "DECISION kill → curl killed" not in p:
        _fail(f"bridge did not forward a kill decision: {p!r}")
    _ok("otel_emit.record bridges span events into the live stream (estimate + kill)")

    # 5. run_summary aggregation
    import run_summary
    importlib.reload(run_summary)
    agg = run_summary.aggregate(run_summary.load(str(jsonl)))
    if "index_repo" not in agg["tools"] or agg["tools"]["index_repo"]["calls"] < 1:
        _fail(f"summary did not count index_repo: {agg['tools']}")
    table = run_summary.fmt(agg)
    if "per-task tool-call summary" not in table or "index_repo" not in table:
        _fail(f"summary table malformed: {table}")
    if "decisions" not in table:
        _fail("summary should list decisions")
    _ok("run_summary aggregates calls/time/fails and lists decisions")
    print(table)

    # 5b. Stage 7c — bottleneck timing split + artificial detection
    os.environ["HERMES_MAX_VERBOSITY"] = "verbose"
    jsonl.write_text("")  # isolate this sub-test's split from earlier events
    pretty.write_text("")
    livelog.tool_ok("synthesize", secs=4.0)              # inference bucket
    livelog.tool_ok("index_repo", secs=2.0)              # tool-work bucket
    livelog.tool_fail("groq", reason="429 rate limit — backing off", secs=3.0,
                      fallback="deepinfra")               # artificial bucket
    import run_summary as rs2
    importlib.reload(rs2)
    if rs2.classify_bucket("synthesize") != "inference":
        _fail("synthesize should classify as inference")
    if rs2.classify_bucket("index_repo") != "tool-work":
        _fail("index_repo should classify as tool-work")
    if rs2.classify_bucket("groq", "429 rate limit") != "artificial":
        _fail("a 429 reason should classify as artificial")
    agg2 = rs2.aggregate(rs2.load(jsonl))
    b = agg2["buckets"]
    if not (b["inference"] >= 4 and b["tool-work"] >= 2 and b["artificial"] >= 3):
        _fail(f"bucket split wrong: {b}")
    tbl2 = rs2.fmt(agg2)
    if "bottleneck split" not in tbl2 or "artificial" not in tbl2:
        _fail("summary missing bottleneck split")
    if "artificial dominated by" not in tbl2:
        _fail(f"summary should name the dominant artificial cause: {tbl2}")
    _ok(f"3-bucket split: inference {b['inference']:.0f}s · tool-work {b['tool-work']:.0f}s · "
        f"artificial {b['artificial']:.0f}s, dominant cause named")

    # 5c. Stage 7a — tqdm-style progress (item N/total, per-item, ETA)
    before = len(_read(pretty))
    livelog.heartbeat("deep_research", done=4, total=12, elapsed_s=47,
                      item="arxiv.org/abs/2401.x", per_item="crawl 3.2s · distil 8.1s")
    pln = _read(pretty)[before:]
    if "[4/12]" not in pln or "ETA ~" not in pln or "arxiv.org" not in pln:
        _fail(f"tqdm progress line missing item/ETA: {pln!r}")
    _ok(f"tqdm progress: {pln.strip().split('] ',1)[-1]}")

    # 6. logging failure never raises (unwritable dir)
    os.environ["HERMES_MAX_LOG_DIR"] = "/proc/nonexistent/cannot/write"
    importlib.reload(livelog)
    try:
        livelog.tool_start("x")
        livelog.tool_ok("x", secs=1)
        livelog.forward("tool_heartbeat", {"tool": "x", "done": 1, "total": 2})
    except Exception as e:  # noqa: BLE001
        _fail(f"logging to an unwritable dir raised: {e!r}")
    _ok("a logging failure degrades silently — never breaks a tool")

    print("observability smoke test PASSED")


if __name__ == "__main__":
    main()
