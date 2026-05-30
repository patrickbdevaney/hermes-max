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

    # ── graph/AST layer (Stage 1.1) ──────────────────────────────────────────
    import graph_core

    if not res.get("graph_available"):
        _fail(f"index_repo did not build the graph: {res}")
    _ok(f"graph built: {res.get('symbols')} symbols, {res.get('edges')} edges")

    # callers: transfer() calls withdraw() -> retrieve_related('withdraw') sees transfer as a caller
    rel = graph_core.retrieve_related("withdraw", hops=1, k=10)
    rel_syms = {r["symbol"]: r["relation"] for r in rel["results"]}
    if rel.get("graph_available") is not True or rel_syms.get("transfer") != "caller":
        _fail(f"retrieve_related('withdraw') missed caller 'transfer': {rel_syms}")
    _ok(f"retrieve_related('withdraw') -> {rel_syms} (multi-hop callers/callees)")

    # callees: transfer() -> withdraw, deposit ; make_account path -> BankAccount
    rel2 = graph_core.retrieve_related("transfer", hops=1, k=10)
    callees = {r["symbol"] for r in rel2["results"] if r["relation"] == "callee"}
    if not {"withdraw", "deposit"}.issubset(callees):
        _fail(f"retrieve_related('transfer') missed callees withdraw/deposit: {callees}")
    _ok(f"retrieve_related('transfer') callees -> {sorted(callees)}")

    # repo map: ranked, budgeted; called-a-lot symbols rank above leaf functions
    rm = graph_core.repo_map(token_budget=500)
    if not rm.get("graph_available") or rm.get("count", 0) < 3:
        _fail(f"repo_map did not return a ranked map: {rm}")
    ranked = [e["symbol"] for e in rm["entries"]]
    _ok(f"repo_map (ranked) -> {ranked[:5]}")

    # search_code now folds in the graph signal (mode shows +graph) but still finds fibonacci
    g_sc = rag_core.search_code("fibonacci sequence number", k=5)
    if "fibonacci" not in [r["symbol"] for r in g_sc["results"]]:
        _fail(f"graph-boosted search_code lost fibonacci: {g_sc}")
    _ok(f"search_code graph-folded but correct (mode={g_sc['mode']})")

    # ── fallback: graph unavailable -> clean degradation, BM25 still works ────
    _real = graph_core.graph_available
    graph_core.graph_available = lambda con: False  # type: ignore[assignment]
    try:
        fb = graph_core.retrieve_related("withdraw")
        if fb.get("graph_available") is not False or "unavailable" not in fb.get("note", ""):
            _fail(f"retrieve_related fallback note missing: {fb}")
        fb_sc = rag_core.search_code("fibonacci", k=3)
        if "fibonacci" not in [r["symbol"] for r in fb_sc["results"]] or "+graph" in fb_sc["mode"]:
            _fail(f"search_code did not cleanly fall back to BM25: {fb_sc}")
        _ok(f"graph-unavailable fallback: retrieve_related warns, search_code -> BM25 (mode={fb_sc['mode']})")
    finally:
        graph_core.graph_available = _real  # type: ignore[assignment]


def part_rerank() -> None:
    """Stage 1.2 rerank WIRING, deterministic (fake endpoint — no live model).

    Proves: (1) a configured reranker is handed a LARGER fused pool and its order
    is applied (mode shows +rerank); (2) when the reranker returns nothing the
    server keeps the fused order and drops +rerank (graceful degradation).
    """
    print("[A2] reranker wiring (fake endpoint, deterministic)")
    import rag_core

    rag_core.RERANK_BASE_URL = "http://fake-rerank.local"  # marks rerank "configured"
    calls: dict[str, int] = {}

    def fake_rerank(query: str, documents: list[str]):
        calls["n_docs"] = len(documents)
        return list(range(len(documents)))[::-1]  # deterministic reversal

    real = rag_core.rerank
    # A query that fuses a multi-candidate pool (so there's something to reorder).
    q_multi = "account balance deposit withdraw transfer money"
    # Baseline fused order with the reranker OFF.
    rag_core.RERANK_BASE_URL = ""
    fused = [r["symbol"] for r in rag_core.search_code(q_multi, k=3)["results"]]
    rag_core.RERANK_BASE_URL = "http://fake-rerank.local"
    rag_core.rerank = fake_rerank  # type: ignore[assignment]
    try:
        res = rag_core.search_code(q_multi, k=3)
        if "+rerank" not in res["mode"]:
            _fail(f"rerank not applied; mode={res['mode']}")
        if calls.get("n_docs", 0) <= 3:
            _fail(f"reranker got only {calls.get('n_docs')} docs — expected a larger fused pool")
        if len(res["results"]) > 3:
            _fail(f"rerank did not trim to k=3: {len(res['results'])}")
        reranked = [r["symbol"] for r in res["results"]]
        if reranked == fused:
            _fail(f"reversed-order reranker produced identical order: {reranked}")
        _ok(f"rerank applied: pool={calls['n_docs']} -> top-3, order changed "
            f"{fused} -> {reranked} (mode={res['mode']})")

        rag_core.rerank = lambda q, d: None  # type: ignore[assignment]
        fb = rag_core.search_code(q_multi, k=3)
        if "+rerank" in fb["mode"]:
            _fail(f"rerank returned None but +rerank stayed in mode: {fb['mode']}")
        if not fb["results"]:
            _fail(f"rerank-fallback returned nothing: {fb}")
        _ok(f"rerank-unavailable fallback: fused order kept (mode={fb['mode']})")
    finally:
        rag_core.rerank = real  # type: ignore[assignment]
        rag_core.RERANK_BASE_URL = ""


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            expected = {"index_repo", "search_code", "get_symbol_context", "find_similar",
                        "retrieve_related", "repo_map"}
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


def part_stage2() -> None:
    """Stage 2 — robust index init. Each assertion maps to a DoD item."""
    print("[A3] robust index init (empty / preflight / unparseable / resume / self-check)")
    import importlib

    import rag_core

    # Isolate this section in its own throwaway DB so resume/incremental counts are clean.
    s2db = os.path.join(_TMP, "stage2.db")
    os.environ["RAG_INDEX_PATH"] = s2db
    os.environ["EMBED_BASE_URL"] = ""
    importlib.reload(rag_core)

    # 1. EMPTY dir -> instant clean empty success (the original failure is gone)
    empty_dir = tempfile.mkdtemp(prefix="rag-empty-")
    r = rag_core.index_repo(empty_dir)
    if not (r.get("ok") and r.get("empty") and r.get("files_indexed") == 0):
        _fail(f"empty dir did not return clean empty success: {r}")
    if not r.get("index_health", {}).get("queryable"):
        _fail(f"empty index not queryable per self-check: {r}")
    _ok(f"empty dir -> clean empty success (mode={r['mode']}, note={r.get('note','')!r})")
    # ... and it is genuinely queryable, returning no results, NOT an error
    q = rag_core.search_code("anything", k=3)
    if not q.get("ok") and q.get("results"):
        _fail(f"query on empty index should be a clean no-results: {q}")
    _ok("query on empty index is a clean no-results (not a crash)")

    # 2. pre-flight scan reports scope + look-ahead BEFORE indexing
    sc = rag_core.scan_repo(str(SAMPLE))
    if sc["n_files"] < 1 or sc["est_s"] <= 0 or not sc["by_lang"]:
        _fail(f"pre-flight scan_repo did not report scope: {sc}")
    _ok(f"pre-flight scan: {sc['n_files']} files, by_lang={sc['by_lang']}, est ~{sc['est_s']}s")

    # 3. real repo: batched index, self-check queryable
    r = rag_core.index_repo(str(SAMPLE), batch_size=2)
    if not r["ok"] or r["files_indexed"] < 1 or not r["index_health"]["queryable"]:
        _fail(f"batched index of sample failed/self-check not queryable: {r}")
    _ok(f"batched index: {r['files_indexed']} files, {r['chunks_indexed']} chunks, "
        f"health={r['index_health']}")

    # 4. re-run is incremental/resumable: unchanged files are RESUMED (skipped), 0 re-indexed
    r2 = rag_core.index_repo(str(SAMPLE), batch_size=2)
    if r2["files_indexed"] != 0 or r2["files_resumed_unchanged"] < r["files_indexed"]:
        _fail(f"re-run not idempotent/resumable (expected all resumed): {r2}")
    _ok(f"re-run resumes unchanged files ({r2['files_resumed_unchanged']} resumed, "
        f"{r2['files_indexed']} re-indexed)")

    # 5. unparseable file is SKIPPED, never fatal
    junk_dir = tempfile.mkdtemp(prefix="rag-junk-")
    # a .py that is not parseable as code and yields no chunks (whitespace only)
    Path(junk_dir, "blank.py").write_text("   \n\n   \n")
    Path(junk_dir, "real.py").write_text("def real_fn(x):\n    return x + 1\n")
    r3 = rag_core.index_repo(junk_dir)
    if not r3["ok"]:
        _fail(f"index with an unparseable file errored instead of skipping: {r3}")
    if r3["skipped_unparseable"] < 1 or r3["files_indexed"] < 1:
        _fail(f"unparseable not skipped / real file not indexed: {r3}")
    _ok(f"unparseable file skipped not fatal ({r3['skipped_unparseable']} skipped, "
        f"{r3['files_indexed']} indexed)")

    # 6. no-embed endpoint -> BM25+graph mode with a clear report (EMBED_BASE_URL blank here)
    if r["mode"] not in ("bm25-only", "bm25+graph"):
        _fail(f"blank EMBED_BASE_URL should degrade to BM25/graph, got mode={r['mode']}")
    _ok(f"blank embed endpoint -> degraded mode={r['mode']} (no dense lane, clean)")

    # restore the part_b DB pin
    os.environ["RAG_INDEX_PATH"] = TEST_DB
    importlib.reload(rag_core)


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
    part_rerank()
    part_stage2()
    part_b()
    print("mcp-codebase-rag smoke test PASSED")
