#!/usr/bin/env python3
"""Standalone smoke test for mcp-docs. No other component strictly required.

Part A: pure helpers (api extraction, url detection) — deterministic.
Part B: graceful degradation — point backends at dead ports, assert every tool
        returns ok=False with a hint and never raises.
Part C: live backends IF up (SearXNG/Crawl4AI) — skipped cleanly when down.
Part D: server boots, /health, tools advertised over real MCP transport.
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
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19109"))


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def part_a() -> None:
    print("[A] pure helpers")
    import docs_core

    md = (
        "# FastAPI\n\nUse `FastAPI` and `APIRouter`.\n\n"
        "```python\ndef create_app():\n    ...\nclass Settings:\n    pass\n```\n"
        "Call `app.include_router(r)` then `uvicorn.run`.\n"
    )
    apis = docs_core.extract_apis(md)
    if "create_app" not in apis or "Settings" not in apis:
        _fail(f"extract_apis missed code-fence defs: {apis}")
    _ok(f"extract_apis -> {apis}")
    if not docs_core._looks_like_url("https://x.com") or docs_core._looks_like_url("not a url"):
        _fail("_looks_like_url wrong")
    _ok("_looks_like_url correct")


def part_b() -> None:
    print("[B] graceful degradation (backends pointed at dead ports)")
    import docs_core

    docs_core.SEARXNG_URL = "http://127.0.0.1:0"
    docs_core.CRAWL4AI_URL = "http://127.0.0.1:0"
    docs_core.VLLM_BASE_URL = ""  # distil disabled -> stores raw

    s = docs_core.search_docs("anything")
    if s.get("ok") or "results" not in s:
        _fail(f"search_docs should fail-soft: {s}")
    _ok(f"search_docs down -> ok=False with hint, results=[] ({s.get('error','')[:40]})")

    f = docs_core.fetch_clean("https://example.com")
    # trafilatura fallback may or may not be installed/reachable; either way no raise
    if f.get("ok"):
        _ok("fetch_clean fell back to trafilatura (live network)")
    else:
        _ok(f"fetch_clean down -> ok=False with hint ({f.get('error','')[:40]})")

    # distil with no model -> stores raw, never raises
    d = docs_core.distill("# Title\nbody", "topic")
    if not d.get("ok") or d.get("distilled"):
        _fail(f"distill with no model should store raw: {d}")
    _ok("distill with no model -> raw passthrough (no crash)")


def part_c() -> None:
    print("[C] live backends (skipped cleanly if down)")
    import docs_core

    docs_core.SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080").rstrip("/")
    docs_core.CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://localhost:11235").rstrip("/")

    s = docs_core.search_docs("python asyncio", limit=5)
    if s.get("ok") and s.get("results"):
        _ok(f"SearXNG live -> {len(s['results'])} results (e.g. {s['results'][0]['url'][:50]})")
        f = docs_core.fetch_clean(s["results"][0]["url"])
        if f.get("ok"):
            _ok(f"Crawl4AI/{f['backend']} live -> {f['chars']} chars markdown")
        else:
            _ok("Crawl4AI down -> fetch_clean fell back / reported (informational)")
    else:
        _ok("SearXNG not live -> skipped (informational)")


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            expected = {"search_docs", "fetch_clean", "ingest_doc", "research_topic"}
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


def part_d() -> None:
    print(f"[D] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_DOCS_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
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
    print("mcp-docs smoke test PASSED")
