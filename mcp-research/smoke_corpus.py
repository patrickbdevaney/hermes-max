#!/usr/bin/env python3
"""Standalone smoke test for Stage 3 — on-disk corpus + provenance + lazy distill.

No live services: RAG / LLM / conductor backends are monkeypatched, the corpus is
redirected to a temp dir. Asserts:
  [A] write_corpus_doc -> FULL untruncated .md with YAML front-matter; idempotent
  [B] ingest_research -> disk write + RAG index of the FULL content, chunk `source`
      = corpus relpath (resolvable)
  [C] distill_for_query is LAZY (query-time) and routes by density:
      cloud flag OFF => local; local unavailable => raw; flag ON + dense => cloud
  [D] resolve_source round-trips a chunk's source -> backing doc + provenance
      (the resolvability the Stage-5 verify gate needs)
  [E] a long (>30K char) arXiv paper is stored UNTRUNCATED on disk
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def main() -> None:
    sys.path.insert(0, str(HERE))
    import corpus as c

    tmp = tempfile.mkdtemp(prefix="corpus_smoke_")
    c.CORPUS_DIR = tmp

    # ---- [A] write_corpus_doc: full content + front-matter, idempotent ----
    print("[A] write_corpus_doc")
    meta = {"source_url": "https://arxiv.org/abs/2106.01345", "title": "Decision Transformer",
            "authors": ["Chen", "Lu"], "date": "2021-06-02", "retrieval_query": "offline RL",
            "citation_count": 1234, "session_id": "s1"}
    body = "## Abstract\nWe cast RL as sequence modeling.\n\ncode: model.forward(x)\n"
    r = c.write_corpus_doc("research/offline-rl", "arxiv", body, meta)
    if not r["ok"] or not Path(r["path"]).exists():
        _fail(f"write_corpus_doc: {r}")
    raw = Path(r["path"]).read_text()
    if not raw.startswith("---") or "arxiv.org/abs/2106.01345" not in raw:
        _fail(f"front-matter missing: {raw[:200]}")
    if 'authors: ["Chen", "Lu"]' not in raw or "model.forward(x)" not in raw:
        _fail(f"content/authors not preserved: {raw[:300]}")
    # provenance round-trips through resolve_source (parses the quoted YAML back)
    chk = c.resolve_source(r["relpath"])
    if chk["front_matter"].get("source_url") != "https://arxiv.org/abs/2106.01345":
        _fail(f"front-matter url should round-trip unquoted: {chk['front_matter']}")
    _ok(f"wrote {r['relpath']} ({r['chars']} chars) with YAML front-matter")

    r2 = c.write_corpus_doc("research/offline-rl", "arxiv", body + "\nappended", meta)
    if r2["path"] != r["path"]:
        _fail("idempotent write should reuse the same slug path")
    if "appended" not in Path(r2["path"]).read_text():
        _fail("re-ingest should overwrite the same file")
    _ok("idempotent: same (title) -> same path, overwritten")

    # ---- [B] ingest_research: disk + RAG index of FULL content, resolvable source ----
    print("[B] ingest_research -> disk + RAG (full chunks, resolvable source)")
    captured = {}
    def _fake_rag(text, namespace, source, title):
        captured.update(text=text, namespace=namespace, source=source, title=title)
        return {"ok": True, "result": {"ok": True, "chunks_indexed": 3, "dense_embedded": True}}
    c._rag_index = _fake_rag
    big = "TECHNICAL DETAIL " * 500  # ~8500 chars, must be passed in FULL
    ir = c.ingest_research("research/offline-rl", "arxiv", big,
                           {"title": "Big Paper", "source_url": "https://arxiv.org/abs/x"})
    if not ir["ok"] or not ir["rag"]["rag_stored"]:
        _fail(f"ingest_research: {ir}")
    if len(captured["text"]) != len(big):
        _fail(f"RAG must receive FULL content, got {len(captured['text'])} vs {len(big)}")
    if captured["source"] != ir["resolvable_via"] or not captured["source"].endswith(".md"):
        _fail(f"RAG source should be the resolvable corpus relpath: {captured['source']}")
    _ok(f"full content indexed ({len(captured['text'])} chars), source={captured['source']} (resolvable)")

    # ---- [C] distill_for_query: lazy + density routing ----
    print("[C] distill_for_query (lazy, density-routed)")
    chunks = ["chunk one: gradient clipping at 1.0", "chunk two: lr=3e-4 warmup 1000 steps"]

    # local available, cloud OFF -> local
    c.CLOUD_DISTILL = False
    c.rc._llm = lambda *a, **k: "LOCAL distilled: lr=3e-4"
    d = c.distill_for_query("hyperparameters?", chunks, source_type="arxiv")
    if d["method"] != "local" or "3e-4" not in d["distilled"]:
        _fail(f"cloud off should use local: {d}")
    _ok("cloud OFF + dense source -> LOCAL distill")

    # no model anywhere -> raw (still sovereign, honest)
    c.rc._llm = lambda *a, **k: None
    d = c.distill_for_query("hyperparameters?", chunks, source_type="arxiv")
    if d["method"] != "raw" or "gradient clipping" not in d["distilled"]:
        _fail(f"no model should fall back to raw chunks: {d}")
    _ok("no model anywhere -> RAW chunk concatenation (sovereign fallback)")

    # cloud ON + dense -> conductor (DeepSeek) wins
    c.CLOUD_DISTILL = True
    c._conductor_distill = lambda prompt, max_tokens=1500: "CLOUD distilled (DeepSeek): lr=3e-4 warmup"
    c.rc._llm = lambda *a, **k: "LOCAL should not be used"
    d = c.distill_for_query("hyperparameters?", chunks, source_type="arxiv")
    if d["method"] != "cloud" or "DeepSeek" not in d["distilled"]:
        _fail(f"cloud on + dense should use cloud: {d}")
    _ok("cloud ON + dense source -> CLOUD distill (DeepSeek via conductor)")

    # cloud ON but NON-dense source -> still local (density gate)
    c.rc._llm = lambda *a, **k: "LOCAL for web source"
    d = c.distill_for_query("q", chunks, source_type="web")
    if d["method"] != "local":
        _fail(f"cloud on + non-dense should stay local: {d}")
    _ok("cloud ON + non-dense (web) -> LOCAL (density gate respected)")

    # cloud ON + dense but conductor declines (proceed_local) -> local fallback
    c._conductor_distill = lambda prompt, max_tokens=1500: None
    c.rc._llm = lambda *a, **k: "LOCAL fallback when cloud declines"
    d = c.distill_for_query("q", chunks, source_type="arxiv")
    if d["method"] != "local":
        _fail(f"conductor declining should fall to local: {d}")
    _ok("cloud declines (proceed_local) -> falls back to LOCAL cleanly")

    # ---- [D] resolve_source round-trip (resolvability for the verify gate) ----
    print("[D] resolve_source")
    res = c.resolve_source(captured["source"])  # the relpath RAG stored
    if not res["ok"] or res["front_matter"].get("source_url") != "https://arxiv.org/abs/x":
        _fail(f"resolve_source provenance: {res}")
    if "TECHNICAL DETAIL" not in res["content"]:
        _fail(f"resolve_source should return full body: {res['chars']}")
    _ok(f"chunk source resolves -> doc ({res['chars']} chars) + provenance "
        f"(title={res['front_matter'].get('title')})")
    miss = c.resolve_source("research/nope/web/ghost.md")
    if miss["ok"]:
        _fail("resolve_source on a missing file should be ok=False")
    _ok("resolve_source on missing file -> ok=False (no crash)")

    # ---- [E] long arXiv paper stored UNTRUNCATED ----
    print("[E] long paper untruncated on disk")
    long_paper = "Section. " * 5000  # ~45000 chars > old 10K/20K truncations
    c._rag_index = lambda *a, **k: {"ok": True, "result": {"ok": True, "chunks_indexed": 20}}
    ir = c.ingest_research("research/big", "arxiv", long_paper, {"title": "Long"})
    disk = Path(ir["corpus"]["path"]).read_text()
    # disk == front-matter + full body; body length must equal the full input
    if len(disk) < len(long_paper):
        _fail(f"disk doc truncated: {len(disk)} < {len(long_paper)}")
    _ok(f"45K-char paper stored untruncated on disk ({ir['corpus']['chars']} body chars)")

    print("mcp-research corpus (Stage 3) smoke test PASSED")


if __name__ == "__main__":
    main()
