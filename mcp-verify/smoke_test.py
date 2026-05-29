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
            if "verify" not in names:
                _fail(f"verify tool not advertised; got {names}")
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
