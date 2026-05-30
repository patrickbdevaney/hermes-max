"""Stage 4 — semantic dedup + authority ranking + citation-graph edge extraction.

Three quality layers over fanned-out / extracted sources:

  * semantic_dedup — collapse NEAR-duplicates by embedding cosine (not just URL or
    n-gram), so paraphrased SEO mirror content doesn't dominate. Keeps the most
    AUTHORITATIVE instance of each cluster. Degrades to n-gram Jaccard (the existing
    research_core helpers) when the embedding endpoint is unavailable.
  * authority_rank — composite of domain authority (research_core.authority_score)
    + citation count (log-scaled, from Semantic Scholar) + recency. Surfaces an
    arXiv primary over a blog summary of it; anchors to seminal work while still
    rewarding recency for fast-moving fields.
  * citation_edges — turn a paper + its Semantic Scholar references/citations into
    normalized {src, rel, dst} edges (cites / cited_by) ready to become KG edges in
    Stage 5. Pure transform, no I/O.

Never raises; every path degrades.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

import httpx

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc  # authority_score, _shingles, _jaccard, _domain

EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "/model")
SEMANTIC_DUP_COSINE = float(os.environ.get("RESEARCH_SEMANTIC_DUP_COSINE", "0.92"))
_CURRENT_YEAR = int(os.environ.get("RESEARCH_CURRENT_YEAR", "2026"))


# ── embedding (shared endpoint with the RAG store; monkeypatchable) ───────────
def _embed(texts: list[str]) -> list[list[float]] | None:
    if not EMBED_BASE_URL or not texts:
        return None
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{EMBED_BASE_URL}/embeddings",
                       json={"model": EMBED_MODEL, "input": [t[:8000] for t in texts]})
            r.raise_for_status()
            data = r.json().get("data", [])
        vecs = [d["embedding"] for d in data]
        return vecs if len(vecs) == len(texts) else None
    except Exception:  # noqa: BLE001
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ── composite authority (domain + citations + recency) ────────────────────────
def composite_authority(item: dict[str, Any]) -> float:
    """0..~6 composite. Domain authority dominates (primary > farm); citation count
    is log-scaled (seminal anchor); recency adds a small fast-moving-field bump."""
    url = item.get("url", "")
    auth = rc.authority_score(url)  # 0..3
    cc = item.get("citation_count")
    cite = math.log1p(cc) / 3.0 if isinstance(cc, (int, float)) and cc else 0.0  # ~0..3
    recency = 0.0
    m = re.search(r"(19|20)\d{2}", str(item.get("date", "")))
    if m:
        yr = int(m.group(0))
        recency = max(0.0, 1.0 - (max(0, _CURRENT_YEAR - yr) / 10.0))  # 0..1, decays over a decade
    return round(auth + cite + 0.5 * recency, 4)


def authority_rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort items by composite authority desc, annotating each with the score.
    Surfaces primary/cited/recent sources over SEO summaries."""
    scored = [dict(it, _authority_composite=composite_authority(it)) for it in (items or [])]
    scored.sort(key=lambda it: it["_authority_composite"], reverse=True)
    otel_emit.record("authority_ranked", {"items": len(scored),
                                          "top": scored[0]["_authority_composite"] if scored else 0})
    return scored


# ── semantic dedup (embedding cosine; n-gram fallback) ────────────────────────
def _text_of(item: dict[str, Any]) -> str:
    return (item.get("markdown") or item.get("content") or item.get("title") or "")[:8000]


def semantic_dedup(items: list[dict[str, Any]],
                   threshold: float = SEMANTIC_DUP_COSINE) -> dict[str, Any]:
    """Collapse near-duplicate items, keeping the MOST AUTHORITATIVE of each cluster.
    Uses embedding cosine when the endpoint is up; otherwise n-gram Jaccard (the
    existing echo-chamber helper). Returns {kept, collapsed, method}."""
    items = [it for it in (items or []) if it]
    if len(items) <= 1:
        return {"ok": True, "kept": items, "collapsed": 0, "method": "noop"}
    # rank by authority first so the cluster representative is the best instance.
    ranked = authority_rank(items)
    texts = [_text_of(it) for it in ranked]
    vecs = _embed(texts)
    method = "embedding" if vecs else "ngram"
    shingles = [rc._shingles(t, n=4) for t in texts] if not vecs else None

    kept: list[dict[str, Any]] = []
    kept_idx: list[int] = []
    collapsed = 0
    for i, it in enumerate(ranked):
        dup = False
        for j in kept_idx:
            sim = (_cosine(vecs[i], vecs[j]) if vecs
                   else rc._jaccard(shingles[i], shingles[j]))
            if sim >= threshold:
                dup = True
                # record the merge on the kept (more-authoritative) instance
                kept[kept_idx.index(j)].setdefault("_dup_of", []).append(it.get("url", ""))
                break
        if dup:
            collapsed += 1
        else:
            kept.append(it)
            kept_idx.append(i)
    otel_emit.record("dedup_collapsed", {"in": len(items), "kept": len(kept),
                                         "collapsed": collapsed, "method": method})
    return {"ok": True, "kept": kept, "collapsed": collapsed, "method": method,
            "threshold": threshold}


# ── citation-graph edges (prep for Stage-5 KG) ────────────────────────────────
def citation_edges(paper: dict[str, Any], refs: list[dict[str, Any]] | None = None,
                   cites: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Turn a paper + its references (backward) / citations (forward) into normalized
    edges ready for the KG: paper --cites--> ref ; citing --cites--> paper. Each edge
    carries source IDs/URLs for provenance. Pure transform (no I/O)."""
    def _key(p: dict) -> str:
        ex = p.get("extra", {}) or {}
        return ex.get("paper_id") or p.get("url") or p.get("title", "")

    pkey = _key(paper)
    edges: list[dict[str, Any]] = []
    for r in (refs or []):
        edges.append({"src": pkey, "rel": "cites", "dst": _key(r),
                      "src_title": paper.get("title", ""), "dst_title": r.get("title", ""),
                      "dst_url": r.get("url", "")})
    for cdoc in (cites or []):
        edges.append({"src": _key(cdoc), "rel": "cites", "dst": pkey,
                      "src_title": cdoc.get("title", ""), "src_url": cdoc.get("url", ""),
                      "dst_title": paper.get("title", "")})
    otel_emit.record("citation_edges", {"paper": pkey, "refs": len(refs or []),
                                        "cites": len(cites or []), "edges": len(edges)})
    return {"ok": True, "paper": pkey, "edges": edges}


def rank_stats() -> dict[str, Any]:
    return {"embed_endpoint": EMBED_BASE_URL or "(unset -> n-gram dedup fallback)",
            "semantic_dup_cosine": SEMANTIC_DUP_COSINE, "current_year": _CURRENT_YEAR}
