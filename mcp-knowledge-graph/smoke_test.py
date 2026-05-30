#!/usr/bin/env python3
"""Standalone smoke test for mcp-knowledge-graph. No other component required.

Part A: record entities + relations, query them back, recall_about.
Part B: boot the server, /health, exercise the tools over real MCP transport.
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
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19103"))
_TMP = tempfile.mkdtemp(prefix="kg-smoke-")
TEST_DB = os.path.join(_TMP, "graph.db")
os.environ["KG_DB_PATH"] = TEST_DB


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def part_a() -> None:
    print("[A] core logic (throwaway DB)")
    import kg_core

    kg_core.record_entity("decision", "use-sqlite-vec",
                          {"why": "zero-infra vector store", "date": "2026-05-29"})
    kg_core.record_entity("file", "rag_core.py", {"lang": "python"})
    kg_core.record_relation("use-sqlite-vec", "implemented_in", "rag_core.py")
    # merge props on existing entity
    kg_core.record_entity("decision", "use-sqlite-vec", {"status": "active"})

    q = kg_core.query_graph(subject="use-sqlite-vec")
    if not any(r["dst"] == "rag_core.py" for r in q["relations"]):
        _fail(f"relation not found via query_graph: {q}")
    _ok(f"query_graph(subject) -> {[ (r['rel'], r['dst']) for r in q['relations'] ]}")

    qt = kg_core.query_graph(type="decision")
    if not any(e["name"] == "use-sqlite-vec" and e["props"].get("status") == "active"
               for e in qt["entities"]):
        _fail(f"prop merge / type query failed: {qt}")
    _ok("query_graph(type='decision') returns merged props")

    rec = kg_core.recall_about("rag_core.py")
    if not rec["found"] or not any(r["src"] == "use-sqlite-vec" for r in rec["incoming"]):
        _fail(f"recall_about missing incoming relation: {rec}")
    _ok(f"recall_about('rag_core.py') incoming -> {[r['src'] for r in rec['incoming']]}")

    # ── self-editing core memory (Stage 4) — throwaway MEMORY.md, tiny bound ──
    import tempfile

    kg_core.HERMES_MEMORY_PATH = os.path.join(tempfile.mkdtemp(prefix="cm-"), "MEMORY.md")
    kg_core.CORE_MEMORY_CHAR_LIMIT = 120
    if kg_core.core_memory_get()["chars"] != 0:
        _fail("core memory should start empty")
    if not kg_core.core_memory_append("Use ruff + black")["ok"]:
        _fail("core_memory_append failed")
    kg_core.core_memory_append("Arch: 9 MCP servers")
    if not kg_core.core_memory_replace(old="ruff + black", new="ruff")["ok"]:
        _fail("core_memory_replace(old,new) failed")
    # size bound: a fact past the limit must be REJECTED (protect the window)
    if kg_core.core_memory_append("x" * 200)["ok"]:
        _fail("overflow append should be rejected (size bound)")
    # block-replace curation pass round-trips
    kg_core.core_memory_replace(block="- Arch: 9 servers, two-axis stack")
    g = kg_core.core_memory_get()
    if "two-axis" not in g["content"] or g["chars"] > g["limit"]:
        _fail(f"core memory curation round-trip wrong: {g}")
    _ok(f"core memory: append/replace/block + size-bound enforced ({g['chars']}/{g['limit']} chars)")

    # ── backend seam (Stage 4): default embedded; neo4j is OPTIONAL + degrades ──
    if kg_core.stats().get("backend") != "embedded":
        _fail("default backend must be embedded")
    # request neo4j with the driver absent -> must resolve to embedded, never raise
    kg_core.KG_BACKEND = "neo4j"
    kg_core._backend_resolved = None  # re-resolve under the new flag
    resolved = kg_core._backend()
    if resolved != "embedded":
        _fail(f"neo4j requested but driver absent must fall back to embedded, got {resolved}")
    # and the store still works through the fallback (no caller change)
    if not kg_core.record_entity("decision", "stage4-seam", {"backend": "embedded-fallback"})["ok"]:
        _fail("ops must still work after neo4j->embedded fallback")
    kg_core.KG_BACKEND = "embedded"
    kg_core._backend_resolved = None
    _ok("backend seam: default=embedded; KG_BACKEND=neo4j w/o driver -> graceful embedded fallback")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            expected = {"record_entity", "record_relation", "query_graph", "recall_about"}
            if not expected.issubset(names):
                _fail(f"missing tools; got {names}")
            _ok(f"tools advertised: {sorted(names)}")
            await session.call_tool("record_entity", {"type": "bug", "name": "bug-42"})
            await session.call_tool("record_relation",
                                    {"a": "bug-42", "rel": "fixed_in", "b": "commit-abc"})
            res = await session.call_tool("recall_about", {"name": "bug-42"})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "outgoing" not in data:
                data = data["result"]
            outs = [r["dst"] for r in data.get("outgoing", [])]
            if "commit-abc" not in outs:
                _fail(f"recall_about over MCP missing relation; got {outs}")
            _ok(f"recall_about('bug-42') over MCP -> fixed_in {outs}")


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
    env = dict(os.environ, MCP_KG_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1",
               KG_DB_PATH=TEST_DB)
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
    print("mcp-knowledge-graph smoke test PASSED")
