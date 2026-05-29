#!/usr/bin/env python3
"""Standalone smoke test for mcp-observability. No other component required.

Part A: emit spans via the core with an in-memory exporter attached; assert the
spans are produced with correct names/attributes (deterministic — does not
require Phoenix). Best-effort: also force_flush to the real Phoenix endpoint.
Part B: boot the server, /health, and call record_task_metrics over real MCP.
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
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19104"))


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def part_a() -> None:
    print("[A] core logic (in-memory exporter)")
    import observability_core as oc

    mem = oc.enable_inmemory()
    r1 = oc.record_trace("unit-test-trace", {"feature": "x", "n": 3})
    if not r1["ok"] or not r1["trace_id"]:
        _fail(f"record_trace did not return a trace id: {r1}")
    r2 = oc.record_task_metrics("task-1", tokens=1234, duration_ms=42.0, verify_passed=True,
                                skill_reused=True)
    oc.record_metric("verify_pass_rate", 0.95, unit="ratio")
    oc.force_flush()

    spans = {s.name: s for s in mem.get_finished_spans()}
    for needed in ("unit-test-trace", "task:task-1", "metric:verify_pass_rate"):
        if needed not in spans:
            _fail(f"missing span '{needed}'; got {sorted(spans)}")
    if spans["task:task-1"].attributes.get("tokens") != 1234:
        _fail(f"task span missing tokens attr: {dict(spans['task:task-1'].attributes)}")
    _ok(f"emitted spans: {sorted(spans)}")
    _ok(f"phoenix status: {oc.status()}")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            expected = {"record_trace", "record_metric", "record_task_metrics"}
            if not expected.issubset(names):
                _fail(f"missing tools; got {names}")
            _ok(f"tools advertised: {sorted(names)}")
            res = await session.call_tool("record_task_metrics",
                                          {"task_id": "smoke", "tokens": 10, "verify_passed": True})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "trace_id" not in data:
                data = data["result"]
            if not data.get("ok") or not data.get("trace_id"):
                _fail(f"record_task_metrics over MCP failed: {data}")
            _ok(f"record_task_metrics over MCP -> trace_id={data['trace_id'][:12]}...")


def _wait_health(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                body = json.loads(r.read())
                if body.get("status") == "ok":
                    _ok(f"/health -> phoenix_reachable={body.get('phoenix_reachable')}")
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.4)
    _fail(f"server health never came up on :{port} ({last})")


def part_b() -> None:
    print(f"[B] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_OBSERVABILITY_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
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
    print("mcp-observability smoke test PASSED")
