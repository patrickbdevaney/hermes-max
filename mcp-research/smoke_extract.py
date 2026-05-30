#!/usr/bin/env python3
"""Standalone smoke test for Stage 4 — extraction ladder + dedup/authority/citation.

No live services (rungs / embedding monkeypatched). Asserts:
  [A] extraction ladder falls through Trafilatura -> Crawl4AI -> Jina on failure;
      PDFs reorder to Jina-first; all-fail returns ok=False (never raises)
  [B] authority_rank surfaces an arXiv primary (cited) over a blog summary
  [C] semantic_dedup collapses a near-duplicate (embedding path), keeping the most
      authoritative instance; degrades to n-gram when embeddings are down
  [D] citation_edges builds correct cites/cited_by directions with provenance
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def part_a() -> None:
    print("[A] extraction ladder fall-through")
    import extract as e

    # trafilatura fails -> crawl4ai succeeds
    e._RUNGS = {
        "trafilatura": lambda url: None,
        "crawl4ai": lambda url: "crawl4ai markdown body",
        "jina": lambda url: "jina markdown body",
    }
    r = e.extract_url("https://example.com/article")
    if not (r["ok"] and r["method"] == "crawl4ai"):
        _fail(f"should fall trafilatura->crawl4ai: {r}")
    tried = [a["rung"] for a in r["attempts"]]
    if tried != ["trafilatura", "crawl4ai"]:
        _fail(f"attempts order wrong: {tried}")
    _ok(f"static page: trafilatura(fail) -> crawl4ai(ok), tried={tried}")

    # PDF reorders to jina first
    e._RUNGS = {"trafilatura": lambda url: "should-not-run",
                "crawl4ai": lambda url: None,
                "jina": lambda url: "jina pdf text"}
    r = e.extract_url("https://arxiv.org/pdf/2106.01345.pdf")
    if not (r["ok"] and r["method"] == "jina" and r["attempts"][0]["rung"] == "jina"):
        _fail(f"PDF should try jina first: {r}")
    _ok(f"PDF: jina-first ladder, method={r['method']}")

    # all rungs fail -> ok=False, no exception
    e._RUNGS = {"trafilatura": lambda url: None, "crawl4ai": lambda url: None,
                "jina": lambda url: None}
    r = e.extract_url("https://blocked.example/x")
    if r["ok"] or r["markdown"]:
        _fail(f"all-fail should be ok=False/empty: {r}")
    _ok(f"all rungs fail -> ok=False ({len(r['attempts'])} tried), no crash")

    # a rung that RAISES is caught and the ladder continues
    def _boom(url): raise RuntimeError("kaboom")
    e._RUNGS = {"trafilatura": _boom, "crawl4ai": lambda url: "recovered", "jina": lambda url: None}
    r = e.extract_url("https://example.com/x")
    if not (r["ok"] and r["method"] == "crawl4ai"):
        _fail(f"raising rung should be caught, ladder continues: {r}")
    _ok("a rung raising is caught; ladder continues to next rung")


def part_b() -> None:
    print("[B] authority_rank (primary > blog)")
    import rank as rk

    items = [
        {"url": "https://someblog.example/summary-of-paper", "title": "blog", "date": "2025"},
        {"url": "https://arxiv.org/abs/2106.01345", "title": "primary", "date": "2021",
         "citation_count": 4000},
        {"url": "https://w3schools.com/x", "title": "farm", "date": "2026"},
    ]
    ranked = rk.authority_rank(items)
    if ranked[0]["url"] != "https://arxiv.org/abs/2106.01345":
        _fail(f"cited arXiv primary should rank #1: {[(r['title'], r['_authority_composite']) for r in ranked]}")
    if ranked[-1]["title"] != "farm":
        _fail(f"content farm should rank last: {[r['title'] for r in ranked]}")
    _ok(f"ranked: {[ (r['title'], r['_authority_composite']) for r in ranked]}")


def part_c() -> None:
    print("[C] semantic_dedup (embedding + n-gram fallback)")
    import rank as rk

    arxiv = {"url": "https://arxiv.org/abs/x", "content": "zk proofs use polynomial commitments",
             "citation_count": 500, "date": "2022"}
    mirror = {"url": "https://contentfarm.example/zk", "content": "zk proofs use polynomial commitments (mirrored)"}
    distinct = {"url": "https://arxiv.org/abs/y", "content": "completely unrelated topic about databases"}

    # embedding path: arxiv & mirror near-identical, distinct far
    def _fake_embed(texts):
        # map by content keyword so order-independent
        out = []
        for t in texts:
            out.append([1.0, 0.0] if "zk proofs" in t else [0.0, 1.0])
        return out
    rk._embed = _fake_embed
    d = rk.semantic_dedup([mirror, arxiv, distinct], threshold=0.95)
    if d["method"] != "embedding" or d["collapsed"] != 1 or len(d["kept"]) != 2:
        _fail(f"embedding dedup should collapse the mirror: {d}")
    kept_urls = {k["url"] for k in d["kept"]}
    if "https://arxiv.org/abs/x" not in kept_urls or "https://contentfarm.example/zk" in kept_urls:
        _fail(f"dedup should keep the authoritative arXiv, drop the farm mirror: {kept_urls}")
    _ok(f"embedding dedup: 3 -> {len(d['kept'])} (collapsed {d['collapsed']}, kept arXiv over mirror)")

    # n-gram fallback when embeddings down
    rk._embed = lambda texts: None
    dupA = {"url": "https://arxiv.org/abs/x", "content": "the quick brown fox jumps over the lazy dog repeatedly today", "citation_count": 10}
    dupB = {"url": "https://blog.example/x", "content": "the quick brown fox jumps over the lazy dog repeatedly today"}
    d = rk.semantic_dedup([dupB, dupA], threshold=0.8)
    if d["method"] != "ngram" or d["collapsed"] != 1:
        _fail(f"n-gram fallback should collapse identical content: {d}")
    if d["kept"][0]["url"] != "https://arxiv.org/abs/x":
        _fail(f"n-gram dedup should keep the more authoritative (arXiv): {d['kept'][0]['url']}")
    _ok(f"embeddings down -> n-gram dedup collapses identical content (kept arXiv)")


def part_d() -> None:
    print("[D] citation_edges (cites / cited_by directions)")
    import rank as rk

    paper = {"title": "Decision Transformer", "url": "https://s2/dt", "extra": {"paper_id": "DT"}}
    refs = [{"title": "Seminal RL", "url": "https://s2/rl", "extra": {"paper_id": "RL"}}]   # backward
    cites = [{"title": "Newer Work", "url": "https://s2/new", "extra": {"paper_id": "NEW"}}]  # forward
    r = rk.citation_edges(paper, refs, cites)
    edges = r["edges"]
    fwd = [e for e in edges if e["src"] == "DT" and e["rel"] == "cites" and e["dst"] == "RL"]
    bwd = [e for e in edges if e["src"] == "NEW" and e["rel"] == "cites" and e["dst"] == "DT"]
    if not fwd or not bwd:
        _fail(f"edge directions wrong: {edges}")
    if fwd[0]["dst_url"] != "https://s2/rl":
        _fail(f"edge should carry provenance url: {fwd[0]}")
    _ok(f"edges: DT--cites-->RL (backward) and NEW--cites-->DT (forward), {len(edges)} total")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    part_a()
    part_b()
    part_c()
    part_d()
    print("mcp-research extract/rank (Stage 4) smoke test PASSED")
