#!/usr/bin/env python3
"""Standalone smoke test for mcp-escalation. No other component, no network.

Part A (stubbed endpoint): assert routing, cost accounting, the hard daily USD
cap, Tier-3 rejection, and default-OFF behavior.
Part B (server): with escalation OFF (the default), escalate returns a disabled
marker over real MCP transport — proving the safe default end-to-end.
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
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19105"))
_TMP = tempfile.mkdtemp(prefix="esc-smoke-")

# Configure a tier via env BEFORE importing the core (tiers are read live, but
# set early to be safe). No real network: _post_chat is stubbed below.
os.environ["ESCALATION_BASE_URL"] = "http://stub.invalid/v1"
os.environ["ESCALATION_API_KEY"] = "test"
os.environ["ESCALATION_MODEL"] = "stub-cheap"
os.environ["ESCALATION_STATE_PATH"] = os.path.join(_TMP, "spend.json")


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def part_a() -> None:
    print("[A] core logic (stubbed endpoint)")
    import escalation_core as ec

    # Stub the network seam with a canned OpenAI-style response.
    def fake_post(cfg, task, max_tokens):
        return {
            "choices": [{"message": {"content": f"[stub:{cfg['model']}] solved: {task[:20]}"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

    ec._post_chat = fake_post

    # 1. default OFF
    ec.ENABLED = False
    r = ec.escalate("hard problem")
    if r.get("ok") or not r.get("disabled"):
        _fail(f"disabled-by-default not honored: {r}")
    _ok("OFF by default -> escalate returns disabled")

    # 2. Tier-3 rejection
    ec.ENABLED = True
    ec.DAILY_USD_CAP = 1.0
    r = ec.escalate("x", tier="opus")
    if r.get("ok") or "not routable" not in r.get("error", ""):
        _fail(f"Tier-3 not rejected: {r}")
    _ok("Tier-3 ('opus') rejected")

    # 3. routing + cost accounting
    r = ec.escalate("genuinely hard, well-scoped subproblem", tier="cheap")
    if not r.get("ok") or "[stub:" not in r.get("content", ""):
        _fail(f"routing failed: {r}")
    expected = 100 / 1e6 * 0.14 + 50 / 1e6 * 0.28
    if abs(r["cost_usd"] - expected) > 1e-9:
        _fail(f"cost accounting wrong: {r['cost_usd']} != {expected}")
    _ok(f"routed cheap tier; cost=${r['cost_usd']:.6f}, spend=${r['spend_today_usd']:.6f}")

    # 4. hard cap enforcement — set cap below current spend, next call refused
    ec.DAILY_USD_CAP = r["spend_today_usd"] / 2
    r2 = ec.escalate("another hard problem")
    if r2.get("ok") or not r2.get("cap_reached"):
        _fail(f"cap not enforced: {r2}")
    _ok(f"daily USD cap enforced -> {r2['reason']}")

    # 5. pre-call cap (cap 0 refuses before any call)
    ec.DAILY_USD_CAP = 0.0
    ec.STATE_PATH = os.path.join(_TMP, "spend2.json")  # fresh state, spend 0
    r3 = ec.escalate("x")
    if r3.get("ok") or not r3.get("cap_reached"):
        _fail(f"zero-cap pre-check failed: {r3}")
    _ok("zero cap refuses before spending")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            if "escalate" not in names:
                _fail(f"escalate tool not advertised; got {names}")
            _ok(f"tools advertised: {sorted(names)}")
            res = await session.call_tool("escalate", {"task": "x"})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "disabled" not in data:
                data = data["result"]
            if data.get("ok") or not data.get("disabled"):
                _fail(f"server should be OFF by default; got {data}")
            _ok("escalate over MCP returns disabled (safe default end-to-end)")


def _wait_health(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                body = json.loads(r.read())
                if body.get("status") == "ok":
                    _ok(f"/health -> enabled={body.get('enabled')}, cap=${body.get('daily_cap_usd')}")
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.4)
    _fail(f"server health never came up on :{port} ({last})")


def part_b() -> None:
    print(f"[B] server over MCP streamable-http (:{TEST_PORT}) — OFF by default")
    env = dict(os.environ, MCP_ESCALATION_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
    env.pop("ESCALATION_ENABLED", None)  # ensure default OFF
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
    print("mcp-escalation smoke test PASSED")
