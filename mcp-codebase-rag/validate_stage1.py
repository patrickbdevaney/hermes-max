#!/usr/bin/env python3
"""Live Stage-1 validation: measure retrieval precision across modes.

Unlike smoke_test.py (deterministic, models stubbed), this hits the REAL local
endpoints to prove the headline DoD: "retrieval precision on a known query
measurably improves with rerank on." Requires a live EMBED_BASE_URL and/or
RERANK_BASE_URL (start ./serve-embed.sh and ./serve-rerank.sh first).

It indexes a repo ONCE with embeddings on (so dense vectors exist), then runs a
set of (query -> expected symbol) probes under three configs by toggling the
module globals:
    A  bm25+graph        (dense off, rerank off)   — the floor
    B  hybrid            (dense on,  rerank off)    — if EMBED_BASE_URL set
    C  hybrid+rerank     (dense on,  rerank on)     — if RERANK_BASE_URL set
For each mode it reports the rank of the expected symbol per probe and the mean
reciprocal rank (MRR). Higher MRR = better precision.

Usage:  .venv/bin/python validate_stage1.py [repo_path]
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Probes over THIS server's own code (indexed below): semantic queries whose
# wording deliberately avoids the exact symbol name, so lexical BM25 alone is
# weak and dense/rerank have room to help.
PROBES = [
    ("re-order candidate documents with the cross encoder before returning", "rerank"),
    ("turn a list of ranked result lists into one fused ranking", "_rrf"),
    ("split a source file into function and class chunks", "chunk_file"),
    ("call the embeddings http endpoint to vectorize text", "embed_texts"),
    ("build the safe full text search match expression from a query", "_fts_query"),
    ("rank symbols by pagerank for the repo map", "repo_map"),
]


def _rank_of(results: list[dict], expected: str) -> int | None:
    for i, r in enumerate(results):
        if r["symbol"] == expected:
            return i + 1
    return None


def _run_mode(rag_core, label: str, embed: str, rerank: str) -> float:
    rag_core.EMBED_BASE_URL = embed
    rag_core.RERANK_BASE_URL = rerank
    print(f"\n── mode {label}  (embed={'on' if embed else 'off'}, rerank={'on' if rerank else 'off'}) ──")
    rr = 0.0
    mode_seen = ""
    for query, expected in PROBES:
        res = rag_core.search_code(query, k=8)
        mode_seen = res["mode"]
        rank = _rank_of(res["results"], expected)
        rr += (1.0 / rank) if rank else 0.0
        top = [r["symbol"] for r in res["results"][:5]]
        mark = f"#{rank}" if rank else "MISS"
        print(f"  [{mark:>4}] want {expected:<12} top5={top}")
    mrr = rr / len(PROBES)
    print(f"  → retrieval_mode={mode_seen}  MRR={mrr:.3f}")
    return mrr


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else HERE
    embed_env = os.environ.get("EMBED_BASE_URL", "").rstrip("/")
    rerank_env = os.environ.get("RERANK_BASE_URL", "").rstrip("/")
    if not embed_env and not rerank_env:
        print("Neither EMBED_BASE_URL nor RERANK_BASE_URL set — nothing to validate.")
        print("Start ./serve-embed.sh / ./serve-rerank.sh and export the URLs first.")
        sys.exit(2)

    os.environ["RAG_INDEX_PATH"] = os.path.join(tempfile.mkdtemp(prefix="rag-val-"), "index.db")
    # Index WITH embeddings on so dense vectors are populated for modes B/C.
    os.environ["EMBED_BASE_URL"] = embed_env
    os.environ["RERANK_BASE_URL"] = ""
    import rag_core

    print(f"indexing {repo} (embeddings {'on' if embed_env else 'OFF'}) …")
    info = rag_core.index_repo(repo)
    print(f"  indexed {info.get('chunks_indexed')} chunks; mode={info.get('mode')}; "
          f"dense_embedded={info.get('dense_embedded')}; graph={info.get('graph_available')}")

    results: dict[str, float] = {}
    results["A bm25+graph"] = _run_mode(rag_core, "A bm25+graph", "", "")
    if embed_env:
        results["B hybrid"] = _run_mode(rag_core, "B hybrid", embed_env, "")
    if embed_env and rerank_env:
        results["C hybrid+rerank"] = _run_mode(rag_core, "C hybrid+rerank", embed_env, rerank_env)
    elif rerank_env and not embed_env:
        results["C bm25+graph+rerank"] = _run_mode(rag_core, "C bm25+graph+rerank", "", rerank_env)

    print("\n════════ SUMMARY (MRR, higher = better precision) ════════")
    for k, v in results.items():
        print(f"  {k:<22} {v:.3f}")
    best = max(results, key=results.get)
    floor = results["A bm25+graph"]
    lift = results[best] - floor
    print(f"\n  best: {best} (MRR {results[best]:.3f}); lift over bm25+graph floor: {lift:+.3f}")
    if lift > 0:
        print("  ✓ precision measurably improved with dense/rerank ON")
    else:
        print("  • no lift on these probes (honest signal — try a larger repo / more probes)")


if __name__ == "__main__":
    main()
