#!/usr/bin/env python3
"""Standalone smoke test for Stage 1 source fan-out (mcp-research/sources.py).

No live services required — the network primitives (_get_json / _get_text /
_post_json) are monkeypatched with canned API payloads, so this asserts:
  [A] each adapter PARSES its real API shape into the normalized item
  [B] presence-gating — github_search no-ops without GITHUB_TOKEN, runs with it
  [C] RRF fusion rewards cross-source agreement (pure arithmetic)
  [D] classifier-router maps a crypto query to the crypto source set (always searxng)
  [E] graceful degradation — a dead host returns an ERROR STRING, never raises;
      a fully-down structured layer => empty fused list, ok=True (web still answers)
  [F] semantic_scholar citation-graph traversal (references backward / citations forward)
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>A Seminal Result in Zero-Knowledge Proofs</title>
    <summary>We present a foundational construction.</summary>
    <published>2017-03-01T00:00:00Z</published>
    <author><name>Alice Researcher</name></author>
    <author><name>Bob Coauthor</name></author>
    <arxiv:primary_category term="cs.CR"/>
  </entry>
</feed>"""


def part_a() -> None:
    print("[A] adapter parsing (canned payloads)")
    import sources as s

    # arXiv: text/XML path
    s._get_text = lambda *a, **k: {"ok": True, "text": _ARXIV_XML}
    r = s.arxiv_search("zero knowledge", categories=["cs.CR"])
    if not (r["ok"] and len(r["results"]) == 1):
        _fail(f"arxiv parse: {r}")
    it = r["results"][0]
    if it["source_type"] != "arxiv" or "arxiv.org/abs/2401.00001" not in it["url"]:
        _fail(f"arxiv item shape: {it}")
    if it["authors"] != ["Alice Researcher", "Bob Coauthor"] or it["extra"]["category"] != "cs.CR":
        _fail(f"arxiv authors/category: {it}")
    _ok(f"arxiv -> 1 paper, authors={len(it['authors'])}, cat={it['extra']['category']}")

    # arXiv days_back filter drops a too-old paper client-side
    r2 = s.arxiv_search("zero knowledge", days_back=30)
    if r2["results"]:
        _fail(f"arxiv days_back=30 should drop the 2017 paper, got {len(r2['results'])}")
    _ok("arxiv days_back filters old papers; days_back=None keeps them")

    # Semantic Scholar relevance search
    s._get_json = lambda *a, **k: {"ok": True, "json": {"data": [{
        "paperId": "abc", "title": "Attention Is All You Need", "abstract": "Transformers.",
        "year": 2017, "citationCount": 99999, "url": "https://www.semanticscholar.org/paper/abc",
        "authors": [{"name": "Vaswani"}], "externalIds": {"ArXiv": "1706.03762"}}]}}
    r = s.semantic_scholar_search("transformers")
    if not (r["ok"] and r["results"][0]["citation_count"] == 99999):
        _fail(f"s2 parse: {r}")
    if r["results"][0]["source_type"] != "semantic_scholar" or "attribution" not in r["results"][0]["extra"]:
        _fail(f"s2 item shape/attribution: {r['results'][0]}")
    _ok(f"semantic_scholar -> citation_count={r['results'][0]['citation_count']}, attribution present")

    # GitHub repositories (with token)
    s.GITHUB_TOKEN = "ghp_fake"
    s._get_json = lambda *a, **k: {"ok": True, "json": {"items": [{
        "full_name": "ethereum/go-ethereum", "html_url": "https://github.com/ethereum/go-ethereum",
        "description": "Official Go impl", "owner": {"login": "ethereum"},
        "pushed_at": "2026-05-01T00:00:00Z", "stargazers_count": 47000}]}}
    r = s.github_search("ethereum client", "repositories")
    if not (r["ok"] and r["results"][0]["extra"]["stars"] == 47000):
        _fail(f"github parse: {r}")
    _ok(f"github repos -> {r['results'][0]['title']} ({r['results'][0]['extra']['stars']} stars)")

    # HN Algolia
    s._get_json = lambda *a, **k: {"ok": True, "json": {"hits": [{
        "objectID": "12345", "title": "Show HN: a thing", "url": "https://example.com/thing",
        "author": "pg", "points": 500, "num_comments": 42, "created_at": "2026-01-01T00:00:00Z"}]}}
    r = s.hn_search("rust async")
    if not (r["ok"] and r["results"][0]["extra"]["points"] == 500):
        _fail(f"hn parse: {r}")
    _ok(f"hn -> {r['results'][0]['title']} ({r['results'][0]['extra']['points']} pts)")

    # Stack Exchange
    s._get_json = lambda *a, **k: {"ok": True, "json": {"items": [{
        "title": "How to do X", "link": "https://stackoverflow.com/q/1", "score": 33,
        "body": "<p>Use <code>foo()</code></p>", "tags": ["python"], "is_answered": True,
        "owner": {"display_name": "user1"}}]}}
    r = s.stackexchange_search("how to do x")
    if not (r["ok"] and r["results"][0]["extra"]["score"] == 33 and "<" not in r["results"][0]["content"]):
        _fail(f"stackexchange parse / html-strip: {r}")
    _ok("stackexchange -> parsed, html stripped from body")


def part_b() -> None:
    print("[B] presence-gating (github_search needs GITHUB_TOKEN)")
    import sources as s

    s.GITHUB_TOKEN = ""  # kill the PAT
    r = s.github_search("anything")
    if not (r["ok"] and r.get("skipped") and r["results"] == []):
        _fail(f"github with no token should no-op (skipped): {r}")
    _ok("no GITHUB_TOKEN -> github_search skipped, ok=True, results=[] (web layer covers it)")

    s.GITHUB_TOKEN = "ghp_fake"
    s._get_json = lambda *a, **k: {"ok": True, "json": {"items": []}}
    r = s.github_search("anything")
    if not (r["ok"] and not r.get("skipped")):
        _fail(f"github with token should run: {r}")
    _ok("GITHUB_TOKEN present -> github_search runs (not skipped)")


def part_c() -> None:
    print("[C] RRF fusion rewards cross-source agreement")
    import sources as s

    arxiv_list = [{"url": "A", "source_type": "arxiv", "content": "x"},
                  {"url": "B", "source_type": "arxiv", "content": ""}]
    s2_list = [{"url": "B", "source_type": "semantic_scholar", "content": "rich"},
               {"url": "C", "source_type": "semantic_scholar", "content": "y"}]
    fused = s.rrf_fuse([arxiv_list, s2_list])
    urls = [f["url"] for f in fused]
    # B appears in BOTH lists near the top -> should rank first.
    if urls[0] != "B":
        _fail(f"RRF: cross-source item B should rank first, got {urls}")
    b = fused[0]
    if set(b["_rrf_sources"]) != {"arxiv", "semantic_scholar"}:
        _fail(f"RRF: B should credit both sources, got {b['_rrf_sources']}")
    if b["content"] != "rich":
        _fail(f"RRF: should keep the richer copy of B, got {b['content']!r}")
    _ok(f"RRF: B (in 2 sources) ranks #1, credits {b['_rrf_sources']}, keeps richer content")


def part_d() -> None:
    print("[D] classifier-router (crypto query -> crypto source set)")
    import sources as s

    c = s.classify_query("zk-SNARK proof systems for ethereum rollups and EVM verification")
    if c["category"] != "crypto":
        _fail(f"crypto query misclassified: {c}")
    for need in ("arxiv", "github", "ethresearch", "eip_erc", "searxng"):
        if need not in c["sources"]:
            _fail(f"crypto source set missing {need}: {c['sources']}")
    if "cs.CR" not in c["arxiv_categories"]:
        _fail(f"crypto should target cs.CR: {c['arxiv_categories']}")
    _ok(f"crypto -> sources={c['sources']}, arxiv={c['arxiv_categories']}")

    ml = s.classify_query("fine-tuning a transformer language model with RLHF on a new dataset")
    if ml["category"] != "applied_ml" or "semantic_scholar" not in ml["sources"]:
        _fail(f"ml query: {ml}")
    _ok(f"applied_ml -> {ml['sources']}")

    lib = s.classify_query("how to fix ImportError install python package version")
    if lib["category"] != "library" or "stackexchange" not in lib["sources"]:
        _fail(f"library query: {lib}")
    _ok(f"library -> {lib['sources']}")

    # invariant: searxng ALWAYS present, whatever the category
    for q in ("random unclassifiable noise", "weather tomorrow"):
        if "searxng" not in s.classify_query(q)["sources"]:
            _fail(f"searxng must always be a routed source: {q}")
    _ok("INVARIANT: searxng is always in the routed source set (web is the catch-all)")


def part_e() -> None:
    print("[E] graceful degradation (dead hosts -> error strings, not exceptions)")
    import sources as s

    # every network primitive 'down'
    s._get_json = lambda *a, **k: {"ok": False, "error": "ConnectError: refused"}
    s._get_text = lambda *a, **k: {"ok": False, "error": "ConnectError: refused"}
    s.GITHUB_TOKEN = "ghp_fake"

    for fn, args in [(s.arxiv_search, ("q",)), (s.semantic_scholar_search, ("q",)),
                     (s.github_search, ("q",)), (s.hn_search, ("q",)),
                     (s.stackexchange_search, ("q",)),
                     (s.semantic_scholar_citations, ("arXiv:1706.03762",))]:
        r = fn(*args)
        if not isinstance(r, dict) or "error" not in r or r.get("results") != []:
            _fail(f"{fn.__name__} dead-host should return error string + empty results: {r}")
        if not isinstance(r["error"], str):
            _fail(f"{fn.__name__} error should be a string: {r}")
    _ok("all adapters: dead host -> {ok:False, error:str, results:[]} (no exception)")

    # source_fanout with the whole structured layer down -> empty fused, ok=True
    fo = s.source_fanout("zk-SNARK ethereum")
    if not (fo["ok"] and fo["results"] == []):
        _fail(f"fanout with all sources down should be ok+empty: {fo}")
    if not fo["errors"]:
        _fail(f"fanout should collect per-source errors: {fo}")
    # ethresearch/eip_erc are not registered until Stage 2 -> reported as skipped
    if "ethresearch" not in fo["skipped"]:
        _fail(f"unregistered crypto sources should be skipped, not errored: {fo['skipped']}")
    _ok(f"source_fanout all-down -> ok=True, 0 results, {len(fo['errors'])} errors, "
        f"skipped={fo['skipped']} (web layer still answers)")


def part_f() -> None:
    print("[F] semantic_scholar citation-graph traversal")
    import sources as s

    # references = backward (papers THIS cites) -> nested under citedPaper
    s._get_json = lambda *a, **k: {"ok": True, "json": {"data": [
        {"citedPaper": {"paperId": "seminal1", "title": "The Original", "year": 1998,
                        "citationCount": 5000, "url": "https://s2/seminal1", "authors": []}}]}}
    r = s.semantic_scholar_citations("arXiv:1706.03762", direction="references")
    if not (r["ok"] and r["results"][0]["extra"]["edge"] == "cites"):
        _fail(f"references traversal: {r}")
    _ok(f"references (backward) -> {r['results'][0]['title']} edge={r['results'][0]['extra']['edge']}")

    # citations = forward (papers citing THIS) -> nested under citingPaper
    s._get_json = lambda *a, **k: {"ok": True, "json": {"data": [
        {"citingPaper": {"paperId": "frontier1", "title": "The Frontier", "year": 2025,
                         "citationCount": 12, "url": "https://s2/frontier1", "authors": []}}]}}
    r = s.semantic_scholar_citations("arXiv:1706.03762", direction="citations")
    if not (r["ok"] and r["results"][0]["extra"]["edge"] == "cited_by"):
        _fail(f"citations traversal: {r}")
    _ok(f"citations (forward) -> {r['results'][0]['title']} edge={r['results'][0]['extra']['edge']}")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    print("mcp-research sources (Stage 1) smoke test PASSED")
