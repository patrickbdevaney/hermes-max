#!/usr/bin/env python3
"""Standalone smoke test for the plan/execute split (CLAUDE_plan_execute.md).

No network: the single call seam conductor_core._post_chat is stubbed where the
synth (planner) path is exercised. Asserts the DoD across the stages:

  Stage 1 — classify_plan_need (NEEDS_PLAN vs NO_PLAN) + plan_route advice
            (PLAN -> synth/V4-Pro, EXECUTE -> local).
  Stage 2 — plan_lint: a complete plan passes; a thin plan (missing FILE SPEC /
            non-absolute WORKING_DIRECTORY) is caught; rounds are bounded; a
            missing file degrades gracefully.
  Stage 3 — request_plan_revision: routes a gap to synth, appends to PLAN.md,
            bounds at PLAN_REVISION_MAX, degrades to proceed_local when synth OFF.
  Server  — the escalation server advertises every new tool over MCP.

Run: python smoke_planexec.py   (or call run_all() from smoke_test.py so `hm
smoke` covers it).
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
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19125"))
_TMP = tempfile.mkdtemp(prefix="planexec-smoke-")

# Route the conductor ledger/budget to temp BEFORE importing the core (paths read
# at import) so a real spend file is never touched.
os.environ["CONDUCTOR_LEDGER_PATH"] = os.path.join(_TMP, "ledger.json")
os.environ["CONDUCTOR_BUDGET_PATH"] = os.path.join(_TMP, "budget.json")
for _k in ("DEEPINFRA_API_KEY", "FIREWORKS_API_KEY", "TOGETHER_API_KEY", "DEEPSEEK_API_KEY",
           "MOONSHOT_API_KEY", "CEREBRAS_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
           "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)

import conductor_core as cc  # noqa: E402
import escalation_core as ec  # noqa: E402
import plan_split as ps  # noqa: E402

_COMPLETE_PLAN = """# PLAN

## TASK
Implement a Bloom filter with a configurable false-positive rate.

## WORKING_DIRECTORY
{wd}

## FILES
- bloom.py — the BloomFilter class
- test_bloom.py — the test suite

## FILE SPEC: bloom.py
class BloomFilter:
    def __init__(self, capacity: int, error_rate: float) -> None
        Compute m = -(capacity * ln(error_rate)) / (ln(2)^2) and k = (m/capacity)*ln(2),
        rounding up; allocate a bytearray bit array of m bits and store k hash seeds.
    def add(self, item: str) -> None
        Hash item with k seeds (mmh3 or hashlib-derived), set each bit modulo m.
    def __contains__(self, item: str) -> bool
        Return True iff all k bits are set.
    Edge cases: error_rate must be in (0, 1) — raise ValueError("error_rate must be in (0,1)");
    capacity must be >= 1 — raise ValueError.

## FILE SPEC: test_bloom.py
    def test_no_false_negatives — every added item is reported present.
    def test_fpr_within_tolerance — measured FPR is within 10% of the configured rate at capacity.
    def test_invalid_args_raise — error_rate=0 and capacity=0 raise ValueError.

## DONE_CONDITION (Definition of Done)
verify green; property_test passes; 12+ tests; measured FPR within 10% of theoretical at capacity.

## RISKS
Hash correlation inflates FPR — detect via the test_fpr_within_tolerance test.
"""

_THIN_PLAN = """# PLAN

## TASK
Implement a Bloom filter.

## WORKING_DIRECTORY
{wd}

## FILES
- bloom.py — the BloomFilter class
- test_bloom.py — the test suite

## FILE SPEC: bloom.py
class BloomFilter:
    def __init__(self, capacity: int, error_rate: float) -> None
        Allocate the bit array.

## DONE_CONDITION (Definition of Done)
verify green; 12+ tests pass.
"""


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _fail(m: str) -> None:
    print(f"  FAIL: {m}")
    sys.exit(1)


def _fake_post_factory(fail: bool = False,
                       content: str = "Use signature: def add(self, item: str) -> None; "
                                       "hash with k seeds, set each bit modulo m."):
    """Canned (json, headers) response for conductor_core._post_chat; optionally raise."""
    def fake(base_url, api_key, model, messages, max_tokens):
        if fail:
            raise RuntimeError("stub: planner unreachable")
        return ({"choices": [{"message": {"content": content}}],
                 "usage": {"prompt_tokens": 200, "completion_tokens": 120}}, {})
    return fake


# ── Stage 1 — classifier + routing ────────────────────────────────────────────
def stage1() -> None:
    print("[1] plan-need classifier + plan_route")
    assert ec.classify_plan_need("Implement a Bloom filter with tests")["plan_required"] is True
    assert ec.classify_plan_need("what does this function do")["plan_required"] is False
    assert ec.classify_plan_need("fix the typo in README")["plan_required"] is False
    assert ec.classify_plan_need("Build a REST API across multiple modules")["plan_required"] is True
    assert ec.classify_plan_need("Add a feature",
                                 signals={"file_count": 5, "mentions_tests": True})["plan_required"] is True
    assert ec.classify_plan_need("Implement X", signals={"single_file": True})["plan_required"] is False
    _ok("classify_plan_need: NEEDS_PLAN vs NO_PLAN correct (string + signals)")

    rp = ps.plan_route(task="Implement a Bloom filter with tests", phase="auto")
    if not (rp["phase"] == "plan" and rp["tier"] == "synth" and "V4-Pro" in rp["model_id"]):
        _fail(f"NEEDS_PLAN task should route to plan/synth/V4-Pro: {rp}")
    rpf = ps.plan_route(task="Implement X with tests", phase="plan")
    if not (rpf["phase"] == "plan" and rpf["tier"] == "synth"):
        _fail(f"phase=plan should force plan/synth: {rpf}")
    rex = ps.plan_route(task="anything", phase="execute")
    if not (rex["phase"] == "execute" and rex["tier"] == "local"):
        _fail(f"phase=execute should be local: {rex}")
    rq = ps.plan_route(task="what does foo do", phase="auto")
    if not (rq["phase"] == "execute" and rq["tier"] == "local"):
        _fail(f"NO_PLAN task should route to execute/local: {rq}")
    _ok(f"plan_route: plan->synth({rp['model_id']}), execute->local, NO_PLAN->local")


# ── Stage 2 — plan_lint ───────────────────────────────────────────────────────
def stage2() -> None:
    print("[2] plan_lint (PLAN.md document gate)")
    good = ps.plan_lint(plan_text=_COMPLETE_PLAN.format(wd=_TMP))
    if not good["complete"]:
        _fail(f"complete plan should pass plan_lint: {good}")
    _ok(f"complete plan -> complete=True, missing={good['missing']}")

    thin = ps.plan_lint(plan_text=_THIN_PLAN.format(wd=_TMP))
    if thin["complete"]:
        _fail(f"thin plan (missing test_bloom.py FILE SPEC) should fail: {thin}")
    if not any("test_bloom.py" in m for m in thin["missing"]):
        _fail(f"plan_lint should flag the missing test_bloom.py FILE SPEC: {thin['missing']}")
    _ok(f"thin plan -> complete=False; flagged: {thin['missing']}")

    rel = ps.plan_lint(plan_text=_COMPLETE_PLAN.format(wd="./bloom"))
    if rel["complete"] or not any("WORKING_DIRECTORY" in m for m in rel["missing"]):
        _fail(f"non-absolute WORKING_DIRECTORY should be flagged: {rel['missing']}")
    _ok("non-absolute WORKING_DIRECTORY flagged")

    bounded = ps.plan_lint(plan_text=_THIN_PLAN.format(wd=_TMP), revision_round=ps.PLAN_LINT_MAX_ROUNDS)
    if not bounded["bounded"] or not bounded["proceed_flagged"]:
        _fail(f"revision_round>=max should set bounded+proceed_flagged: {bounded}")
    _ok(f"revision rounds bounded at {ps.PLAN_LINT_MAX_ROUNDS} -> proceed_flagged")

    missing = ps.plan_lint(plan_path=os.path.join(_TMP, "does-not-exist.md"))
    if missing["complete"] or "PLAN.md not found" not in " ".join(missing["missing"]):
        _fail(f"missing plan file should degrade to not-found: {missing}")
    _ok("missing PLAN.md -> graceful not-found (no raise)")


# ── Stage 3 — request_plan_revision ───────────────────────────────────────────
def stage3() -> None:
    print("[3] request_plan_revision (gap -> synth/V4-Pro -> append PLAN.md)")
    repo = tempfile.mkdtemp(prefix="planexec-revrepo-")
    Path(repo, "PLAN.md").write_text(_COMPLETE_PLAN.format(wd=repo))

    cc._post_chat = _fake_post_factory()
    os.environ["DEEPINFRA_API_KEY"] = "test"
    try:
        r = ps.request_plan_revision("what exact signature for add()?", repo=repo)
        if not (r["resolved"] and r["answer"] and r["appended"]):
            _fail(f"revision should resolve via synth and append to PLAN.md: {r}")
        if "PLAN REVISION" not in Path(repo, "PLAN.md").read_text():
            _fail("the answer should be appended under a PLAN REVISION header")
        _ok(f"revision resolved via synth ({r.get('model')}), appended to PLAN.md")

        # bound: at the cap, NO call fires — stub set to RAISE proves it
        cc._post_chat = _fake_post_factory(fail=True)
        rb = ps.request_plan_revision("another?", repo=repo, request_index=ps.PLAN_REVISION_MAX)
        if rb["resolved"] or not rb["bounded"] or not rb["proceed_flagged"]:
            _fail(f"at the revision cap, no call should fire and bounded=True: {rb}")
        _ok(f"revision bounded at {ps.PLAN_REVISION_MAX} -> no call fired, proceed_flagged")
    finally:
        os.environ.pop("DEEPINFRA_API_KEY", None)

    # synth OFF (no key) -> proceed_local, never invents
    cc._post_chat = _fake_post_factory()
    off = ps.request_plan_revision("gap with no planner available?", repo=repo)
    if off["resolved"] or not off.get("proceed_local"):
        _fail(f"with synth OFF the revision must proceed_local (never invent): {off}")
    _ok("synth OFF -> proceed_local (executor falls to workflow-stuck, never invents)")


# ── Server — tools advertised over MCP ────────────────────────────────────────
async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            want = {"classify_plan_need", "plan_route", "plan_lint", "request_plan_revision"}
            if not want.issubset(names):
                _fail(f"plan/execute tools not advertised; want {want}, got {sorted(names)}")
            _ok(f"plan/execute tools advertised over MCP: {sorted(want)}")
            res = await session.call_tool("plan_route",
                                          {"task": "Implement a parser with tests", "phase": "plan"})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "phase" not in data:
                data = data["result"]
            if data.get("phase") != "plan" or data.get("tier") != "synth":
                _fail(f"plan_route over MCP unexpected: {data}")
            _ok(f"plan_route over MCP -> phase={data['phase']} tier={data['tier']} model={data['model_id']}")


def _wait_health(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    _ok(f"/health up on :{port}")
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.4)
    _fail(f"server health never came up on :{port} ({last})")


def stage_server() -> None:
    print(f"[server] MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_ESCALATION_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
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


def run_all() -> None:
    """Entry point smoke_test.py calls so `hm smoke` covers the plan/execute split."""
    stage1()
    stage2()
    stage3()
    stage_server()


if __name__ == "__main__":
    run_all()
    print("plan/execute smoke test PASSED")
