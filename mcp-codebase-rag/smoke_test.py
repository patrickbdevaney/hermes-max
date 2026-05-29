#!/usr/bin/env python3
"""Standalone smoke test for mcp-codebase-rag. No other component required.

Determinism: forces a throwaway index DB and disables embeddings, so the test
exercises the always-available BM25 path and never depends on a live embed
endpoint. (If EMBED_BASE_URL is set in your env, the server still works in
hybrid mode — this test just pins the deterministic floor.)

Part A: index the bundled sample repo, assert relevant symbols are retrieved.
Part B: boot the server, /health, and call search_code over real MCP transport.
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
SAMPLE = HERE / "sample_repo"
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19102"))
_TMP = tempfile.mkdtemp(prefix="rag-smoke-")
TEST_DB = os.path.join(_TMP, "index.db")

# Pin determinism BEFORE importing rag_core (it reads env at import time).
os.environ["RAG_INDEX_PATH"] = TEST_DB
os.environ["EMBED_BASE_URL"] = ""


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def part_a() -> None:
    print("[A] core logic (BM25, throwaway DB)")
    import rag_core

    res = rag_core.index_repo(str(SAMPLE))
    if not res.get("ok") or res.get("chunks_indexed", 0) < 4:
        _fail(f"index_repo did not index the sample: {res}")
    _ok(f"indexed: {res['files_indexed']} files, {res['chunks_indexed']} chunks, mode={res['mode']}")

    sc = rag_core.search_code("fibonacci sequence number", k=5)
    syms = [r["symbol"] for r in sc["results"]]
    if "fibonacci" not in syms:
        _fail(f"search_code('fibonacci') missed it; got {syms}")
    _ok(f"search_code -> {syms} (mode={sc['mode']})")

    ctx = rag_core.get_symbol_context("BankAccount")
    if not any(r["symbol"] == "BankAccount" for r in ctx["results"]):
        _fail(f"get_symbol_context('BankAccount') missed it; got {ctx}")
    _ok("get_symbol_context('BankAccount') returned the class")

    withdraw = rag_core.search_code("withdraw money insufficient funds", k=5)
    wsyms = [r["symbol"] for r in withdraw["results"]]
    if "withdraw" not in wsyms:
        _fail(f"search_code('withdraw') missed the method; got {wsyms}")
    _ok(f"method-level chunk retrieved: {wsyms}")

    sim = rag_core.find_similar("def gcd(a, b): return a", k=3)
    if not sim["results"]:
        _fail("find_similar returned nothing")
    _ok(f"find_similar -> {[r['symbol'] for r in sim['results']]}")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            expected = {"index_repo", "search_code", "get_symbol_context", "find_similar"}
            if not expected.issubset(names):
                _fail(f"missing tools; got {names}")
            _ok(f"tools advertised: {sorted(names)}")
            res = await session.call_tool("search_code", {"query": "fibonacci", "k": 3})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "results" not in data:
                data = data["result"]
            syms = [r["symbol"] for r in data.get("results", [])]
            if "fibonacci" not in syms:
                _fail(f"search_code over MCP missed fibonacci; got {syms}")
            _ok(f"search_code over MCP -> {syms}")


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


def part_b() -> None:
    print(f"[B] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_RAG_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1",
               RAG_INDEX_PATH=TEST_DB, EMBED_BASE_URL="")
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
    print("mcp-codebase-rag smoke test PASSED")
