#!/usr/bin/env python3
"""Standalone smoke test for mcp-search.

Boots a throwaway mcp-verify (the sibling server) so selection runs over the REAL
verify boundary — the thing that makes selection lossless. No model is needed:
candidates are supplied directly (the always-available selector path).

Part A (core): given 3 candidate `add` functions (1 correct, 1 wrong-answer,
  1 syntactically broken) + a shared test, the SELECTOR picks the correct one.
  Plus: no candidate green -> selected None (never a red pick); and the
  generation path with no $VLLM_BASE_URL returns a clean disabled marker.
Part B (server): /health + generate_and_select(candidates=...) over real MCP
  selects the correct candidate end-to-end.
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
REPO_ROOT = HERE.parent
VERIFY_DIR = REPO_ROOT / "mcp-verify"
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19108"))
VERIFY_PORT = int(os.environ.get("SMOKE_VERIFY_PORT", "19181"))

TESTS = {"test_solution.py": "from solution import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}
CANDIDATES = [
    {"id": "correct", "files": {"solution.py": "def add(a, b):\n    return a + b\n"}},
    {"id": "wrong", "files": {"solution.py": "def add(a, b):\n    return a - b\n"}},
    {"id": "broken", "files": {"solution.py": "def add(a, b)\n    return a + b\n"}},
]


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def _verify_python() -> str:
    py = VERIFY_DIR / ".venv" / "bin" / "python"
    if py.exists():
        return str(py)
    print("  [setup] mcp-verify venv missing — creating it for the verify boundary")
    subprocess.run([sys.executable, "-m", "venv", str(VERIFY_DIR / ".venv")], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(VERIFY_DIR / "requirements.txt")],
                   check=True)
    return str(py)


def _wait_health(port: int, name: str, timeout: float = 40.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    _ok(f"{name} /health up on :{port}")
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.4)
    _fail(f"{name} health never came up on :{port} ({last})")


def _start_verify() -> subprocess.Popen:
    env = dict(os.environ, MCP_VERIFY_PORT=str(VERIFY_PORT), MCP_BIND_HOST="127.0.0.1")
    proc = subprocess.Popen([_verify_python(), str(VERIFY_DIR / "server.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    _wait_health(VERIFY_PORT, "mcp-verify")
    return proc


def part_a() -> None:
    print("[A] core selector against a real verify server")
    os.environ["MCP_VERIFY_PORT"] = str(VERIFY_PORT)
    os.environ["MCP_BIND_HOST"] = "127.0.0.1"
    os.environ.pop("VLLM_BASE_URL", None)  # prove the generation path degrades cleanly
    import search_core

    r = search_core.select_from_candidates(CANDIDATES, tests=TESTS, language="python")
    if not r.get("ok") or r.get("selected") != "correct":
        _fail(f"selector did not pick the correct candidate: {r}")
    _ok(f"selected '{r['selected']}' of {r['n']} ({r['reason']})")
    by_id = {vd["id"]: vd for vd in r["verdicts"]}
    if by_id["wrong"]["green"] or by_id["broken"]["green"]:
        _fail(f"a non-green candidate was marked green: {by_id}")
    _ok("wrong-answer and broken candidates correctly RED (selection is execution-based)")

    # no candidate green -> selected None, never a red pick
    none_green = search_core.select_from_candidates(
        [CANDIDATES[1], CANDIDATES[2]], tests=TESTS, language="python")
    if none_green.get("selected") is not None:
        _fail(f"with no green candidate, selected must be None: {none_green}")
    _ok("no green candidate -> selected=None (never returns a red patch)")

    # generation path with no $VLLM_BASE_URL -> clean disabled marker
    gen = search_core.generate_and_select("write add(a,b)", n=3, tests=TESTS, candidates=None)
    if gen.get("ok") or not gen.get("disabled"):
        _fail(f"generation should report disabled without VLLM_BASE_URL: {gen}")
    _ok("generation path without $VLLM_BASE_URL -> clean disabled marker")

    # selector still works through generate_and_select when candidates supplied
    g2 = search_core.generate_and_select("add", candidates=CANDIDATES, tests=TESTS)
    if g2.get("selected") != "correct":
        _fail(f"generate_and_select(candidates=...) selection wrong: {g2}")
    _ok("generate_and_select with supplied candidates selects correctly")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            if "generate_and_select" not in names:
                _fail(f"generate_and_select not advertised; got {sorted(names)}")
            _ok(f"tools advertised: {sorted(names)}")
            res = await session.call_tool("generate_and_select",
                                          {"task_spec": "add", "candidates": CANDIDATES, "tests": TESTS})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "selected" not in data:
                data = data["result"]
            if data.get("selected") != "correct":
                _fail(f"select over MCP did not pick correct: {data}")
            _ok(f"generate_and_select over MCP -> selected '{data['selected']}'")


def part_b(port: int) -> None:
    print(f"[B] server over MCP streamable-http (:{port})")
    env = dict(os.environ, MCP_SEARCH_PORT=str(port), MCP_BIND_HOST="127.0.0.1",
               MCP_VERIFY_PORT=str(VERIFY_PORT))
    proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_health(port, "mcp-search")
        asyncio.run(_mcp_check(port))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    verify_proc = _start_verify()
    try:
        part_a()
        part_b(TEST_PORT)
    finally:
        verify_proc.terminate()
        try:
            verify_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            verify_proc.kill()
    print("mcp-search smoke test PASSED")
