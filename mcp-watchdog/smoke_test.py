#!/usr/bin/env python3
"""Standalone smoke test for mcp-watchdog. No other component, no network.

Part A (core logic): assert the four detection invariants —
  * a repeated-n-gram string TRIPS check_spiral; varied prose does NOT;
  * a zero-delta sequence TRIPS check_progress after N; real progress does NOT;
  * a HEARTBEATING long call does NOT trip check_stall (no false-kill);
  * a SILENT over-budget call DOES trip check_stall;
  * an exceeded per-task budget DOES trip check_budget.
Part B (server): the five tools are advertised over real MCP transport and
check_spiral round-trips end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19107"))
_TMP = tempfile.mkdtemp(prefix="wd-smoke-")
os.environ["WATCHDOG_STATE_DIR"] = _TMP


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def part_a() -> None:
    print("[A] core logic (deterministic, no network)")
    import watchdog_core as wc

    # 1. spiral: a repeated-n-gram string trips
    spiral_text = ("I will retry the failing import. " * 30)
    r = wc.check_spiral(spiral_text)
    if not r["spiral_detected"]:
        _fail(f"repeated text not flagged as spiral: {r['metrics']}")
    _ok(f"repeated-n-gram string trips check_spiral ({r['reason']})")

    # ... and varied prose does NOT
    prose = (
        "The task tracker stores rows in SQLite and exposes CRUD endpoints. "
        "First I localize the failing module by reading db.py and its callers. "
        "The bug is an off-by-one in the pagination offset; fixing it requires "
        "adjusting the LIMIT clause and updating one regression test. "
        "After that I will run the verifier and checkpoint the green state."
    )
    r = wc.check_spiral(prose)
    if r["spiral_detected"]:
        _fail(f"normal prose false-flagged as spiral: {r['metrics']}")
    _ok("varied prose does NOT trip check_spiral")

    # short text never spirals
    if wc.check_spiral("retry retry retry")["spiral_detected"]:
        _fail("very short text should not be judged")
    _ok("too-short text is not judged a spiral")

    # 2. progress: zero-delta sequence trips after N
    flat = {"files_touched": 5, "tests_passing": 3, "checkpoints": 2, "turn": 10}
    res = None
    for i in range(4):
        sig = dict(flat, turn=10 + i)  # only turn advances (no real progress)
        res = wc.check_progress("flat-task", signals=sig, n=3)
    if not res["no_progress"]:
        _fail(f"zero-delta sequence did not trip no_progress: {res}")
    _ok(f"zero-delta sequence trips check_progress (count={res['no_progress_count']}, "
        f"turns_since_green={res['turns_since_last_green']})")

    # real progress does NOT trip
    for i in range(4):
        sig = {"files_touched": 5 + i, "tests_passing": 3 + i, "checkpoints": 2 + i, "turn": 20 + i}
        res = wc.check_progress("moving-task", signals=sig, n=3)
    if res["no_progress"]:
        _fail(f"forward progress wrongly flagged no_progress: {res}")
    _ok("steady forward progress does NOT trip check_progress")

    # 3. stall: heartbeating long call is NOT hung (no false-kill)
    r = wc.check_stall("uvicorn", elapsed_s=600, expecting_heartbeat=True,
                       last_heartbeat_age_s=2, per_tool_budget_s=120)
    if r["hung"] or not r["waiting"]:
        _fail(f"heartbeating process wrongly killed: {r}")
    _ok("heartbeating over-budget call is WAITING, not hung (no false-kill)")

    # ... silent over-budget call IS hung
    r = wc.check_stall("curl", elapsed_s=600, expecting_heartbeat=False, per_tool_budget_s=120)
    if not r["hung"]:
        _fail(f"silent over-budget call not flagged hung: {r}")
    _ok(f"silent over-budget call trips check_stall ({r['reason']})")

    # ... a process that stopped heartbeating (stale beat) IS hung
    r = wc.check_stall("daemon", elapsed_s=600, expecting_heartbeat=True,
                       last_heartbeat_age_s=400, per_tool_budget_s=120)
    if not r["hung"]:
        _fail(f"stale-heartbeat process not flagged hung: {r}")
    _ok("stale-heartbeat (went silent) call trips check_stall")

    # within budget is neither
    r = wc.check_stall("ruff", elapsed_s=5, per_tool_budget_s=120)
    if r["hung"] or r["waiting"]:
        _fail(f"within-budget call mis-flagged: {r}")
    _ok("within-budget call is neither hung nor waiting")

    # 4. budget: exceeded trips
    wc.start_task_budget("bt", wall_clock_s=300, max_turns=50, usd_cap=1.0)
    r = wc.check_budget("bt", turns_used=10, usd_spent=0.1, elapsed_s_override=10)
    if r["budget_exceeded"]:
        _fail(f"budget wrongly exceeded under limits: {r}")
    _ok("under-budget check passes")
    r = wc.check_budget("bt", turns_used=60, usd_spent=0.1, elapsed_s_override=10)
    if not r["budget_exceeded"] or "max_turns" not in r["exceeded"]:
        _fail(f"turns budget not enforced: {r}")
    _ok("max_turns budget trips check_budget")
    r = wc.check_budget("bt", turns_used=1, usd_spent=0.1, elapsed_s_override=999)
    if not r["budget_exceeded"] or "wall_clock" not in r["exceeded"]:
        _fail(f"wall-clock budget not enforced: {r}")
    _ok("wall_clock budget trips check_budget")
    r = wc.check_budget("never-started")
    if r.get("ok"):
        _fail(f"check_budget should error when no budget started: {r}")
    _ok("check_budget on unstarted task returns a clean error (no crash)")

    # 5. per-tool budget registry (Stage 1)
    b = wc.tool_budget("index_repo")
    if not b["known"] or b["ceiling_s"] != 1800:
        _fail(f"index_repo registry ceiling wrong: {b}")
    _ok(f"index_repo has a per-tool hard ceiling ({b['ceiling_s']}s, expected '{b['expected']}')")
    b = wc.tool_budget("totally_unknown_tool")
    if b["known"] or b["ceiling_s"] is not None:
        _fail(f"unknown tool should have no hard ceiling: {b}")
    _ok("unknown tool falls back to global budget with NO hard ceiling")
    # env override of a ceiling
    os.environ["BUDGET_DEEP_RESEARCH_S"] = "1234"
    b = wc.tool_budget("deep_research")
    if b["ceiling_s"] != 1234:
        _fail(f"BUDGET_DEEP_RESEARCH_S override not honored: {b}")
    _ok("BUDGET_<TOOL>_S overrides a per-tool ceiling")
    del os.environ["BUDGET_DEEP_RESEARCH_S"]

    # 6. look-ahead estimation (Stage 1)
    e = wc.estimate_duration("index_repo", file_count=1240, total_bytes=1240 * 4096)
    if not (50 < e["est_s"] < 1800) or e["exceeds_ceiling"]:
        _fail(f"index_repo look-ahead estimate implausible: {e}")
    _ok(f"index_repo look-ahead: {e['basis']} (ceiling {e['ceiling_s']}s)")
    e = wc.estimate_duration("deep_research", query_count=12, per_source_s=30)
    if abs(e["est_s"] - 360) > 1 or e["exceeds_ceiling"]:
        _fail(f"deep_research look-ahead wrong: {e}")
    _ok(f"deep_research look-ahead: {e['basis']}")
    # a doomed run: estimate alone blows past the ceiling
    e = wc.estimate_duration("deep_research", query_count=1000, per_source_s=30)
    if not e["exceeds_ceiling"]:
        _fail(f"a 1000-query research run should exceed the ceiling: {e}")
    _ok("a look-ahead estimate exceeding the ceiling is flagged (chunk/raise, not doomed-run)")

    # 7. hard ceiling kills even a heartbeating tool; heartbeat-via-state liveness
    r = wc.check_stall("index_repo", elapsed_s=2000, expecting_heartbeat=True,
                       last_heartbeat_age_s=1)
    if not r["hung"] or r.get("cause") != "ceiling":
        _fail(f"hard-ceiling runaway not killed despite heartbeat: {r}")
    _ok("a heartbeating tool past its HARD ceiling IS killed (runaway guard)")
    # within ceiling, over budget, heartbeating -> slow-but-alive
    r = wc.check_stall("index_repo", elapsed_s=200, expecting_heartbeat=True,
                       last_heartbeat_age_s=3)
    if r["hung"] or not r["waiting"]:
        _fail(f"over-budget heartbeating index wrongly killed: {r}")
    _ok("over-budget but heartbeating (within ceiling) is slow-but-alive, not killed")
    # heartbeat resolved from watchdog state by task_id (no caller-tracked age)
    wc.record_heartbeat("hb-task", "index_repo", progress="400/1240", done=400, total=1240)
    r = wc.check_stall("index_repo", elapsed_s=200, task_id="hb-task")
    if r["hung"] or not r["waiting"]:
        _fail(f"state-stamped heartbeat not honored by check_stall: {r}")
    _ok("record_heartbeat + check_stall(task_id=...) resolves liveness from state")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            expected = {"check_spiral", "check_stall", "check_progress",
                        "start_task_budget", "check_budget",
                        "tool_budget", "estimate_duration", "record_heartbeat"}
            if not expected.issubset(names):
                _fail(f"missing tools; got {sorted(names)}")
            _ok(f"tools advertised: {sorted(names)}")
            res = await session.call_tool("check_spiral",
                                          {"recent_thinking_text": "loop loop loop " * 40})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "spiral_detected" not in data:
                data = data["result"]
            if not data.get("spiral_detected"):
                _fail(f"check_spiral over MCP did not detect repetition: {data}")
            _ok("check_spiral over MCP detects a spiral end-to-end")


def _wait_health(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                body = json.loads(r.read())
                if body.get("status") == "ok":
                    _ok(f"/health -> tool_budget_s={body.get('tool_budget_s')}")
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.4)
    _fail(f"server health never came up on :{port} ({last})")


def part_b() -> None:
    print(f"[B] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_WATCHDOG_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
    proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_health(TEST_PORT)
        asyncio.run(_mcp_check(TEST_PORT))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    part_a()
    part_b()
    print("mcp-watchdog smoke test PASSED")
