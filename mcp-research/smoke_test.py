#!/usr/bin/env python3
"""Standalone smoke test for mcp-research. No live services required.

Part A: pure helpers (authority, shingles/jaccard, url-normalize, json-from-llm).
Part B: graceful degradation — no chat model / dead backends, every tool ok-soft.
Part C: the FOUR engineered failure-mode invariants (deterministic, monkeypatched):
        1. echo-chamber retrieval  -> URL/n-gram dedup
        2. source-quality bias     -> authority-aware ranking
        3. planning hallucination  -> verify_claims flags weak claims pre-synthesis
        4. overspawning            -> hard source caps
Part D: deep_research end-to-end with fully monkeypatched backends (no network).
Part E: server boots, /health, tools advertised over real MCP transport.
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
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19110"))


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def part_a() -> None:
    print("[A] pure helpers")
    import research_core as rc

    assert rc.authority_score("https://docs.python.org/3/library/asyncio.html") == 3
    assert rc.authority_score("https://example.gov/report") == 3
    assert rc.authority_score("https://w3schools.com/x") == 0
    assert rc.authority_score("https://some-random-blog.example/post") == 1
    _ok("authority_score ranks primary(3) > neutral(1) > farm(0)")

    a = rc._shingles("the quick brown fox jumps", 3)
    b = rc._shingles("the quick brown fox jumps", 3)
    if rc._jaccard(a, b) != 1.0:
        _fail("jaccard of identical texts != 1.0")
    if rc._jaccard(a, rc._shingles("completely different words here now", 3)) > 0.1:
        _fail("jaccard of disjoint texts too high")
    _ok("shingle jaccard sane")

    if rc._normalize_url("https://www.Example.com/a/") != rc._normalize_url("http://example.com/a"):
        _fail("url normalize should collapse www/scheme/trailing-slash")
    _ok("url normalize collapses www/scheme/trailing slash")

    if rc._json_from_llm('```json\n[{"claim":"x","source_urls":["u"]}]\n```')[0]["claim"] != "x":
        _fail("json_from_llm failed on fenced array")
    _ok("json_from_llm tolerant of fences")


def part_b() -> None:
    print("[B] graceful degradation (no chat model, dead backends)")
    import research_core as rc

    rc.VLLM_BASE_URL = ""  # no LLM -> deterministic paths
    rc._search = lambda *a, **k: []        # SearXNG 'down'
    rc._fetch = lambda url: {"ok": False, "url": url, "error": "down"}

    p = rc.plan_research("how does X work")
    if not p.get("ok") or not p["subgoals"]:
        _fail(f"plan_research should degrade to 1 subgoal: {p}")
    _ok(f"plan_research no-LLM -> {len(p['subgoals'])} subgoal(s)")

    q = rc.develop_queries("X internals")
    if not q.get("ok") or not q["queries"]:
        _fail(f"develop_queries should degrade to variants: {q}")
    _ok(f"develop_queries no-LLM -> {len(q['queries'])} deterministic variants")

    e = rc.explore(["anything"])
    if not e.get("ok") or e["sources"]:
        _fail(f"explore with dead search should be ok+empty: {e}")
    _ok("explore with SearXNG down -> ok=True, sources=[] (no crash)")

    s = rc.synthesize("Q", [{"claim": "c", "status": "single-sourced", "sources": ["https://a.com/x"]}])
    if not s.get("ok") or "a.com" not in s["report_md"]:
        _fail(f"synthesize no-LLM should produce a cited fallback: {s}")
    _ok("synthesize no-LLM -> deterministic cited brief")


def part_c() -> None:
    print("[C] FOUR failure-mode invariants")
    import research_core as rc
    rc.VLLM_BASE_URL = ""

    # ---- 1. echo-chamber: repeated similar queries do NOT re-ingest same URL ----
    SAME = "https://primary.dev/guide"
    rc._search = lambda q, **k: [{"title": "Guide", "url": SAME, "content": "alpha beta gamma delta"}]
    rc._fetch = lambda url: {"ok": True, "url": url, "backend": "stub",
                             "markdown": "alpha beta gamma delta epsilon zeta"}
    e = rc.explore(["x guide", "x guide tutorial", "x guide reference"], max_total=8)
    if len(e["sources"]) != 1:
        _fail(f"echo-chamber: expected 1 unique source, got {len(e['sources'])}")
    if e["echo_chamber_blocked"] < 1:
        _fail("echo-chamber: blocked counter should be >=1")
    _ok(f"INVARIANT 1 echo-chamber: 3 similar queries -> 1 source, {e['echo_chamber_blocked']} blocked")

    # ---- 2. source-quality: a primary doc outranks an SEO farm for one query ----
    def _search_mixed(q, **k):
        return [
            {"title": "farm", "url": "https://w3schools.com/x", "content": "c1"},
            {"title": "primary", "url": "https://docs.python.org/3/x", "content": "c2"},
            {"title": "blog", "url": "https://random.example/x", "content": "c3"},
        ]
    rc._search = _search_mixed
    rc._fetch = lambda url: {"ok": True, "url": url, "backend": "stub", "markdown": f"body of {url}"}
    e = rc.explore(["x"], max_total=1)  # cap to 1 -> must pick the PRIMARY
    if not e["sources"] or e["sources"][0]["domain"] != "docs.python.org":
        _fail(f"source-quality: primary should be picked first, got {e['sources']}")
    _ok("INVARIANT 2 source-quality: primary doc outranks SEO farm (picked first)")

    # ---- 3. planning hallucination: a weak/unsupported claim is FLAGGED, not asserted ----
    v = rc.verify_claims([
        {"claim": "well-supported fact", "sources": [
            {"url": "https://docs.python.org/a", "snippet": "s"},
            {"url": "https://arxiv.org/b", "snippet": "s"}]},
        {"claim": "hallucinated plan step", "sources": [
            {"url": "https://random.example/a", "snippet": "s"}]},
    ])
    by = {x["claim"]: x for x in v["verified"]}
    if by["well-supported fact"]["status"] != "well-supported":
        _fail(f"verify: 2 independent domains should be well-supported: {by}")
    if by["hallucinated plan step"]["status"] != "single-sourced":
        _fail(f"verify: single domain should be single-sourced (flagged): {by}")
    # and synthesis must route the flagged claim into GAPS, not assert it
    s = rc.synthesize("Q", v["verified"])
    if "hallucinated plan step" not in s["gaps"]:
        _fail(f"synthesis must list the weak claim in gaps: {s['gaps']}")
    _ok("INVARIANT 3 planning-hallucination: weak claim flagged single-sourced -> lands in gaps, not asserted")

    # ---- 4. overspawning: a query does NOT fan out past the cap ----
    fetches = {"n": 0}
    def _search_many(q, **k):
        return [{"title": f"t{i}", "url": f"https://uniq{i}.example/p", "content": f"c{i}"} for i in range(50)]
    def _count_fetch(url):
        fetches["n"] += 1
        return {"ok": True, "url": url, "backend": "stub", "markdown": f"m {url}"}
    rc._search = _search_many
    rc._fetch = _count_fetch
    e = rc.explore(["broad q"], max_sources_per_query=3, max_total=2)
    if len(e["sources"]) != 2:
        _fail(f"overspawning: total cap not honored, got {len(e['sources'])}")
    if fetches["n"] > 4:  # bounded by caps, not 50
        _fail(f"overspawning: too many fetches ({fetches['n']}) for a capped query")
    _ok(f"INVARIANT 4 overspawning: 50 candidates -> {len(e['sources'])} sources, {fetches['n']} fetches (capped)")


def part_d() -> None:
    print("[D] deep_research end-to-end (monkeypatched, no network)")
    import research_core as rc
    rc.VLLM_BASE_URL = ""
    rc._search = lambda q, **k: [{"title": "Doc", "url": "https://docs.python.org/3/asyncio",
                                  "content": "asyncio event loop coroutines"}]
    rc._fetch = lambda url: {"ok": True, "url": url, "backend": "stub",
                             "markdown": "asyncio provides an event loop and coroutines"}
    r = rc.deep_research("what is asyncio", max_loops=2, max_total_sources=2, compound=False)
    if not r.get("ok") or not r["report_md"]:
        _fail(f"deep_research should produce a report: {r}")
    if "docs.python.org" not in "".join(r["citations"]):
        _fail(f"deep_research report should cite the source: {r['citations']}")
    if r["sovereign"] is not True:
        _fail("deep_research should be sovereign")
    _ok(f"deep_research e2e -> {r['sources_explored']} sources, confidence={r['confidence']}, "
        f"{len(r['citations'])} citations, stop={r['stop_reason']}")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            expected = {"plan_research", "develop_queries", "explore", "verify_claims",
                        "synthesize", "deep_research"}
            if not expected.issubset(names):
                _fail(f"missing tools; got {names}")
            _ok(f"tools advertised: {sorted(names)}")


def _wait_health(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    _ok(f"/health up on :{port}")
                    return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    _fail(f"health never came up on :{port}")


def part_e() -> None:
    print(f"[E] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_RESEARCH_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
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
    sys.path.insert(0, str(HERE))
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    print("mcp-research smoke test PASSED")
