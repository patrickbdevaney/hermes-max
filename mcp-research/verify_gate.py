"""Stage 5b — decomposed verification gate (grounding, not generation).

The most important reliability layer. Verification is RETRIEVAL-decomposed, not a
generative "does this look right":

  * every synthesized claim must carry a SOURCE ID that resolves to a stored chunk
    (corpus.resolve_source) — claims with no resolvable backing are flagged, never
    asserted;
  * a cheap entailment pass checks each claim is actually ENTAILED by its cited
    chunk (local Qwen, or DeepSeek via the conductor for dense sources);
  * CONTRADICTIONS across sources are surfaced EXPLICITLY with both citations —
    never averaged away (critical when research drives an architecture decision).

Plus query-diversity decomposition (the echo-chamber fix): break a question into
complementary sub-questions, generate diverse paraphrase angles + per-source query
syntax, optional HyDE — the actual searches fuse via sources.rrf_fuse.

Never raises; degrades to deterministic behavior with no model.
"""
from __future__ import annotations

from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc
import corpus
import sources

DENSE_SOURCE_TYPES = corpus.DENSE_SOURCE_TYPES


# ── resolve a source ID -> the stored chunk text it backs ─────────────────────
def _resolve_chunk(src: dict[str, Any]) -> tuple[str, str, bool]:
    """Return (text, source_id, resolvable). Prefers a corpus relpath resolved to
    full on-disk content; falls back to an inline snippet; flags unresolvable."""
    sid = src.get("source_id") or src.get("source") or src.get("url") or ""
    if src.get("source_id") or (isinstance(sid, str) and sid.endswith(".md")):
        res = corpus.resolve_source(sid)
        if res.get("ok"):
            return res["content"], sid, True
    snippet = src.get("snippet") or src.get("markdown") or ""
    return snippet, sid, bool(snippet)


# ── entailment pass (decomposed; local default, dense -> optional cloud) ──────
_ENTAIL_SYS = (
    "You are a strict entailment checker. Does the SOURCE CHUNK entail the CLAIM? "
    "Answer STRICT JSON {\"label\": \"supports\"|\"contradicts\"|\"neutral\"}. "
    "'supports' ONLY if the chunk clearly backs the claim; 'contradicts' if it "
    "states the opposite; else 'neutral'. Judge only from the chunk, not prior "
    "knowledge."
)


def _entail(claim: str, chunk: str, source_type: str = "web") -> str:
    if not chunk.strip():
        return "unchecked"
    prompt = f"CLAIM: {claim}\n\nSOURCE CHUNK:\n{chunk[:4000]}"
    out = None
    if corpus.CLOUD_DISTILL and source_type in DENSE_SOURCE_TYPES:
        out = corpus._conductor_distill(f"{_ENTAIL_SYS}\n\n{prompt}", max_tokens=300)
    if out is None:
        out = rc._llm([{"role": "system", "content": _ENTAIL_SYS},
                       {"role": "user", "content": prompt}], max_tokens=2000, temperature=0)
    parsed = rc._json_from_llm(out)
    if isinstance(parsed, dict):
        lab = str(parsed.get("label", "")).lower().strip()
        if lab in ("supports", "contradicts", "neutral"):
            return lab
    return "unchecked"


def verify_claim(claim: str, sources: list[dict[str, Any]],
                 min_sources: int = 2) -> dict[str, Any]:
    """Verify ONE claim by decomposed retrieval: resolve each source to its stored
    chunk, entail the claim against it, count INDEPENDENT (distinct-domain) support.
    Returns status + per-source verdicts + resolvable source IDs. Contradictions are
    preserved (status='conflicting'), never averaged."""
    claim = (claim or "").strip()
    if not claim:
        return {"ok": False, "error": "empty claim"}
    by_domain: dict[str, dict] = {}
    unresolved = 0
    for src in (sources or []):
        chunk, sid, resolvable = _resolve_chunk(src)
        if not resolvable:
            unresolved += 1
        dom = rc._domain(src.get("url", "") or sid)
        if not dom or dom in by_domain:
            continue  # one vote per domain -> independence
        label = _entail(claim, chunk, src.get("source_type", "web")) if (rc.VLLM_BASE_URL or corpus.CLOUD_DISTILL) else "unchecked"
        by_domain[dom] = {"source_id": sid, "url": src.get("url", ""), "label": label,
                          "resolvable": resolvable}
    supports = [d for d in by_domain.values() if d["label"] == "supports"]
    contradicts = [d for d in by_domain.values() if d["label"] == "contradicts"]
    # 'unchecked' (no model) counts as candidate support for the deterministic path,
    # but the wording stays honest.
    candidate = supports + [d for d in by_domain.values() if d["label"] == "unchecked"]
    if contradicts and (supports or candidate):
        status = "conflicting"
    elif len(supports) >= min_sources:
        status = "well-supported"
    elif len(candidate) >= min_sources:
        status = "candidate-unverified"  # ≥2 domains but no model entailment
    elif by_domain:
        status = "single-sourced"
    else:
        status = "unsupported"
    otel_emit.record("claim_verified" if status in ("well-supported", "candidate-unverified")
                     else "claim_unsupported",
                     {"status": status, "independent": len(by_domain), "unresolved": unresolved})
    return {"ok": True, "claim": claim, "status": status,
            "independent_sources": len(by_domain),
            "supports": [d["source_id"] for d in supports],
            "contradicts": [d["source_id"] for d in contradicts],
            "unresolved_sources": unresolved,
            "source_ids": [d["source_id"] for d in by_domain.values()],
            "verdicts": list(by_domain.values())}


def verify_findings(findings: list[dict[str, Any]], min_sources: int = 2) -> dict[str, Any]:
    """Verify a batch of claims and surface contradictions explicitly. Each finding:
    {"claim": str, "sources": [{source_id|url|snippet, source_type?}]}."""
    verified = [verify_claim(f.get("claim", ""), f.get("sources", []), min_sources)
                for f in (findings or [])]
    verified = [v for v in verified if v.get("ok")]
    contradictions = surface_contradictions(verified)
    summary = {s: sum(1 for v in verified if v["status"] == s)
               for s in ("well-supported", "candidate-unverified", "single-sourced",
                         "conflicting", "unsupported")}
    return {"ok": True, "verified": verified, "contradictions": contradictions,
            "summary": summary}


def surface_contradictions(verified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull out claims where sources disagree, presenting BOTH sides' citations.
    Never averaged — the operator/agent sees the conflict and both sources."""
    out = []
    for v in verified:
        if v.get("status") == "conflicting":
            out.append({"claim": v["claim"],
                        "supported_by": v["supports"],
                        "contradicted_by": v["contradicts"],
                        "note": "sources disagree — both citations surfaced, not averaged"})
    if out:
        otel_emit.record("contradiction_surfaced", {"count": len(out)})
    return out


# ── query-diversity decomposition (echo-chamber fix) ──────────────────────────
_DECOMP_SYS = (
    "Decompose the research question into 2-4 COMPLEMENTARY sub-questions (not "
    "overlapping). For EACH sub-question give 3 diverse search paraphrases that vary "
    "abstraction and phrasing to retrieve DIFFERENT sources. Return STRICT JSON: "
    '[{"sub_question": "...", "paraphrases": ["...", "...", "..."]}]. No prose.'
)


def _per_source_syntax(query: str) -> dict[str, str]:
    """Translate one query into per-source syntax (arXiv field prefixes != GitHub
    qualifiers != web). Lightweight, deterministic — the diverse-retrieval step."""
    q = query.strip()
    return {"web": q, "arxiv": f"all:{q}", "github": q, "semantic_scholar": q,
            "hn": q}


def decompose_question(question: str, hyde: bool = False) -> dict[str, Any]:
    """Sub-question decomposition + diverse paraphrase angles + per-source syntax,
    so retrieval doesn't echo one phrasing. Uses the local model (or conductor for
    a stronger decomposition); degrades to deterministic variants with no model.
    Optional HyDE: a hypothetical answer doc to embed for dense retrieval."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}
    parsed = rc._json_from_llm(rc._llm(
        [{"role": "system", "content": _DECOMP_SYS},
         {"role": "user", "content": question}], temperature=0.4))
    subs: list[dict[str, Any]] = []
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and item.get("sub_question"):
                paras = [str(p).strip() for p in (item.get("paraphrases") or []) if str(p).strip()]
                subs.append({"sub_question": str(item["sub_question"]).strip(),
                             "paraphrases": sources_dedup(paras or [item["sub_question"]]),
                             "per_source": {p: _per_source_syntax(p) for p in (paras[:3] or [question])}})
    if not subs:  # deterministic fallback
        variants = [question, f"{question} overview", f"{question} latest research"]
        subs = [{"sub_question": question, "paraphrases": variants,
                 "per_source": {v: _per_source_syntax(v) for v in variants}}]
    hyde_doc = None
    if hyde:
        hyde_doc = rc._llm([{"role": "system", "content":
                             "Write a short hypothetical expert answer to embed for retrieval (HyDE)."},
                            {"role": "user", "content": question}], max_tokens=400, temperature=0.3)
    otel_emit.record("query_decomposed", {"sub_questions": len(subs), "hyde": bool(hyde_doc)})
    return {"ok": True, "question": question, "sub_questions": subs, "hyde_doc": hyde_doc}


def sources_dedup(qs: list[str]) -> list[str]:
    """Thin reuse of the existing n-gram query dedup so paraphrases stay diverse."""
    return rc._dedup_queries(qs)


def verify_gate_stats() -> dict[str, Any]:
    return {"entailment": "local" + ("+cloud(dense)" if corpus.CLOUD_DISTILL else ""),
            "corpus_resolvable": True}
