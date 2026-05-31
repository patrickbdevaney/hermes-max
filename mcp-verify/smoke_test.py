#!/usr/bin/env python3
"""Standalone smoke test for mcp-verify. No other component required.

Part A (logic): verify() is green on known-good code and red on known-broken.
Part B (server): the process boots, /health responds, and the `verify` tool is
reachable + correct over the real MCP streamable-http transport.

Exits non-zero on any failure so scripts/smoke-test.sh can gate on it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOOD = HERE / "samples" / "good"
BAD = HERE / "samples" / "bad"
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19101"))


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def part_a() -> None:
    print("[A] core logic (no server)")
    import verify_core

    good = verify_core.verify(str(GOOD), "python")
    if not good["passed"]:
        _fail(f"good sample should pass but did not: {good['summary']}\n{json.dumps(good, indent=2)}")
    _ok(f"good sample green: {good['summary']}")

    bad = verify_core.verify(str(BAD), "python")
    if bad["passed"]:
        _fail(f"broken sample should fail but passed: {json.dumps(bad, indent=2)}")
    _ok(f"broken sample red: {bad['summary']}")

    # quick_check (Stage 1.3): lint+type only, never runs the test stage
    qc = verify_core.quick_check(str(GOOD), "python")
    if not qc["passed"] or any(s.get("name") == "tests" for s in qc["stages"]):
        _fail(f"quick_check should be green and skip tests: {qc}")
    _ok(f"quick_check (incremental) green, no test stage: {qc['summary']}")
    qcb = verify_core.quick_check(str(BAD), "python")
    if qcb["passed"]:
        _fail(f"quick_check on broken sample should be red: {qcb}")
    _ok(f"quick_check catches a malformed edit fast: {qcb['summary']}")

    # deep_verify (Stage 2.1): difficulty-gated, skippable, advisory layers
    easy = verify_core.deep_verify(str(GOOD), "python", difficulty="easy")
    if not easy["passed"] or any(s["name"] in ("property", "mutation", "fuzz") for s in easy["stages"]):
        _fail(f"deep_verify(easy) should run base only: {easy}")
    _ok(f"deep_verify(easy) base-only green: {easy['summary']}")

    hard = verify_core.deep_verify(str(GOOD), "python", difficulty="hard")
    deep_names = {s["name"]: s["status"] for s in hard["stages"]}
    if not {"property", "mutation", "fuzz"}.issubset(deep_names):
        _fail(f"deep_verify(hard) should request property/mutation/fuzz: {deep_names}")
    if not hard["passed"]:
        _fail(f"deep_verify(hard) should stay green (advisory layers don't fail it): {hard}")
    # every extra layer either ran or skipped-with-warning — never a hard error
    for name in ("property", "mutation", "fuzz"):
        if deep_names[name] not in ("passed", "skipped", "failed"):
            _fail(f"layer {name} errored instead of skipping cleanly: {deep_names}")
    _ok(f"deep_verify(hard) layers {deep_names}; warnings={len(hard['warnings'])} (advisory, gate green)")

    # quality_check (plan/execute Stage 4): advisory texture pass, never a gate
    import quality_core
    import tempfile

    gap = Path(tempfile.mkdtemp(prefix="qual-")) / "gap.py"
    gap.write_text(
        "import os\n\n\n"
        "def bad_fn(x):\n"
        "    # TODO: handle negatives\n"
        "    try:\n"
        "        return 1 / x\n"
        "    except:\n"
        "        return 0\n\n\n"
        "class Thing:\n"
        "    def public_method(self, y):\n"
        "        return y\n"
    )
    q = quality_core.quality_check(str(gap))
    if q.get("status") != "advisory":
        _fail(f"quality_check must be advisory: {q}")
    if not (q["annotations_missing"] and q["docstrings_missing"]
            and q["placeholders"] and q["bare_excepts"]):
        _fail(f"quality_check should flag all four buckets on the gap file: {q}")
    if q["clean"]:
        _fail(f"gap file should not be clean: {q}")
    _ok(f"quality_check flags all four buckets (advisory): {q['summary']}")

    clean = Path(gap.parent) / "clean.py"
    clean.write_text('"""A clean module."""\n\n\n'
                     "def add(a: int, b: int) -> int:\n"
                     '    """Return the sum of a and b."""\n'
                     "    return a + b\n")
    qc = quality_core.quality_check(str(clean))
    if not qc["clean"] or qc["status"] != "advisory":
        _fail(f"clean file should be quality-clean + advisory: {qc}")
    _ok("quality_check: a clean file reports clean=True (advisory, gate untouched)")


def _wait_health(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                body = json.loads(r.read())
                if body.get("status") == "ok":
                    _ok(f"/health -> {body}")
                    return
        except Exception as e:  # noqa: BLE001 - polling, any error means not-up-yet
            last = str(e)
        time.sleep(0.4)
    _fail(f"server health never came up on :{port} ({last})")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://127.0.0.1:{port}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            if not {"verify", "quick_check", "deep_verify", "quality_check"}.issubset(names):
                _fail(f"verify/quick_check/deep_verify/quality_check tools not advertised; got {names}")
            _ok(f"tools advertised: {sorted(names)}")

            res = await session.call_tool("verify", {"path": str(GOOD), "language": "python"})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            # FastMCP wraps non-dict returns; our tool returns a dict so it lands as-is.
            if isinstance(data, dict) and "result" in data and "passed" not in data:
                data = data["result"]
            if not data.get("passed"):
                _fail(f"verify(good) over MCP not green: {data}")
            _ok(f"verify(good) over MCP green: {data.get('summary')}")


def part_b() -> None:
    print(f"[B] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_VERIFY_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
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
    print("mcp-verify smoke test PASSED")
