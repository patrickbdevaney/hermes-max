"""Phase 5 — novel research capabilities (exceed, don't just match).

Four moves that are cheap for a sovereign loop and unaffordable for a stateless paid
API. Every one degrades to a deterministic non-LLM path; none imports a framework.

  5.1 adversarial_wave        — a fan-out wave that tries to FALSIFY tentative claims
                                ("X criticism / debunked / counter-evidence"), then we
                                re-verify and measure which claims got downgraded.
  5.2 cross_run_contradictions— a new verified claim that conflicts with a PRIOR corpus
                                claim is surfaced as a KG `contradicts` edge (self-
                                correcting across runs). No-op when the KG/corpus is down.
  5.3 temporal_annotate       — each verified claim is stamped "true as of <date>",
                                with supersession when a newer source overrides it.
  5.4 ensemble_decompositions — 2–3 decomposition strategies (plan-and-execute / STORM-
                                perspective / citation-seeded); their retained evidence
                                is RRF-fused so a source that surfaces under multiple
                                framings ranks highest.

Pure helpers here; deep_research does the (state-carrying) wiring. Never raises.
"""
from __future__ import annotations

import datetime
from typing import Any

import research_core as rc

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore


def _today_iso() -> str:
    return datetime.date.today().isoformat()


# ── 5.1 adversarial / disconfirming wave ──────────────────────────────────────
_DISCONFIRM_SYS = (
    "You are a RED-TEAM researcher. For each tentative finding, write ONE web search "
    "query whose purpose is to DISPROVE it — surface criticism, counter-evidence, "
    "failure cases, retractions, or dissenting expert views. Do not search for "
    "confirmation. Return STRICT JSON: a list of query strings, one per finding."
)


def disconfirm_queries(claims: list[dict], n_claims: int | None = None) -> list[str]:
    """Falsification queries for the strongest tentative claims. LLM rung when a model
    is configured; deterministic 'criticism / debunked / limitations' variants otherwise.
    Targets WELL-SUPPORTED claims first — those are the ones worth trying to break."""
    n = n_claims if n_claims is not None else rc.RESEARCH_ADVERSARIAL_CLAIMS
    ranked = sorted(claims, key=lambda c: 0 if c.get("status") == "well-supported" else 1)
    picked = [c.get("claim", "") for c in ranked if c.get("claim")][:max(1, n)]
    if not picked:
        return []
    raw = rc._llm(
        [{"role": "system", "content": _DISCONFIRM_SYS},
         {"role": "user", "content": "\n".join(f"- {c}" for c in picked)}],
        temperature=0.3, max_tokens=600)
    parsed = rc._json_from_llm(raw)
    if isinstance(parsed, list) and parsed:
        qs = [str(q).strip() for q in parsed if str(q).strip()]
        if qs:
            return rc._dedup_queries(qs)
    # deterministic fallback: three falsification angles for the top claim(s)
    out: list[str] = []
    for c in picked[:max(1, n // 2 or 1)]:
        stem = c.strip().rstrip(".")[:80]
        out += [f"{stem} criticism", f"{stem} debunked counter-evidence",
                f"{stem} limitations failure cases"]
    return rc._dedup_queries(out)


def verdict_downgrades(pre: list[dict], post: list[dict]) -> dict[str, Any]:
    """How many claims lost standing once the disconfirming evidence was added —
    the headline metric for the adversarial wave (claims revised/retracted)."""
    _RANK = {"well-supported": 2, "single-sourced": 1, "contradicted": 0, "unsupported": 0}
    pre_by = {c.get("claim"): c.get("status") for c in pre}
    downgraded: list[dict[str, Any]] = []
    for c in post:
        k = c.get("claim")
        if k in pre_by:
            before, after = pre_by[k], c.get("status")
            if _RANK.get(after, 1) < _RANK.get(before, 1):
                downgraded.append({"claim": k, "from": before, "to": after})
    return {"downgraded": downgraded, "count": len(downgraded),
            "claims_checked": len(post)}


# ── 5.3 per-claim temporal provenance ─────────────────────────────────────────
def _source_year(s: dict) -> int | None:
    """Cheap year sniff from a source's url/title (no extra fetch). Best-effort."""
    import re
    blob = f"{s.get('url','')} {s.get('title','')}"
    yrs = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", blob) if 1990 <= int(y) <= 2099]
    return max(yrs) if yrs else None


def temporal_annotate(verified: list[dict], sources: list[dict],
                      as_of_iso: str | None = None) -> list[dict]:
    """Stamp each verified finding with `valid_as_of` (the run date) and, when its
    supporting sources disagree on year, a `superseded_by` pointer to the newest one —
    turning the report into a living artifact ('true as of <date>'). In place + returned."""
    as_of = as_of_iso or _today_iso()
    by_url = {s.get("url"): s for s in sources}
    for f in verified:
        f["valid_as_of"] = as_of
        # a finding's `sources` may be plain URL strings (verify_claims) or dicts
        urls = [(s if isinstance(s, str) else s.get("url", "")) for s in (f.get("sources") or [])]
        dated = [(by_url.get(u, {"url": u}), _source_year(by_url.get(u, {"url": u}))) for u in urls]
        dated = [(s, y) for s, y in dated if y]
        if len(dated) >= 2:
            dated.sort(key=lambda t: t[1])
            newest, newest_y = dated[-1]
            oldest_y = dated[0][1]
            if newest_y > oldest_y:  # a newer source exists → flag potential supersession
                f["superseded_by"] = {"url": newest.get("url"), "year": newest_y,
                                      "supersedes_year": oldest_y}
    return verified


# ── 5.4 ensemble of decompositions + RRF fusion ───────────────────────────────
def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal-rank fusion of several ranked id lists (same k=60 as sources.py).
    An id that ranks well across MULTIPLE lists beats one that ranks high in only one —
    so evidence corroborated by several decomposition framings rises to the top."""
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, _id in enumerate(lst):
            if _id:
                scores[_id] = scores.get(_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def ensemble_decompositions(question: str, base_subgoals: list[str]) -> list[dict[str, Any]]:
    """2–3 DISTINCT framings of the same question (plan-and-execute / STORM-perspective
    / citation-seeded). Each is a deterministic transform of the base plan so the
    ensemble works with no model; downstream explore+RRF rewards cross-framing overlap."""
    q = question.strip().rstrip("?")
    base = [sg for sg in base_subgoals if sg][:6] or [q]
    plan_and_execute = {"strategy": "plan-and-execute", "subgoals": base}
    # STORM perspectives: re-frame each subgoal through stakeholder lenses
    perspective = {"strategy": "storm-perspective", "subgoals": [
        f"{q} from a practitioner's perspective",
        f"{q} from a skeptic's / critic's perspective",
        f"{q} state of the art and recent developments"]}
    # citation-seeded: aim at the authoritative literature / primary sources
    citation = {"strategy": "citation-seeded", "subgoals": [
        f"{q} foundational papers and primary sources",
        f"{q} survey or systematic review",
        f"{q} authoritative documentation or standard"]}
    return [plan_and_execute, perspective, citation]


def ensemble_wave1(question: str, base_subgoals: list[str], max_total: int,
                   category: str | None = None) -> dict[str, Any]:
    """Run each decomposition framing as an INDEPENDENT exploration, then RRF-fuse the
    retained URLs so a source corroborated across framings ranks first. Returns a fused,
    capped, deduped source list + the merged seen_urls. The expensive Phase-5 path —
    deep_research only calls this when RESEARCH_ENSEMBLE is on."""
    decomps = ensemble_decompositions(question, base_subgoals)
    ranked_lists: list[list[str]] = []
    by_url: dict[str, dict] = {}
    seen: list[str] = []
    for d in decomps:
        qmap: list[tuple[str, str]] = []
        for sg in d["subgoals"]:
            for q in rc.develop_queries(sg)["queries"]:
                qmap.append((q, sg))
        ex = rc.explore([q for q, _ in qmap], seen_urls=seen,
                        max_total=max_total, category=category)
        q2sg = {q: sg for q, sg in qmap}
        srcs = ex.get("sources", [])
        for s in srcs:
            s["_subgoal"] = q2sg.get(s.get("query"))
            s["_strategy"] = d["strategy"]
            by_url.setdefault(s["url"], s)
        ranked_lists.append([s["url"] for s in srcs])
        seen = ex.get("seen_urls", seen)  # don't re-fetch across framings (cost guard)
    fused = rrf_fuse(ranked_lists)
    fused_sources = [by_url[u] for u, _ in fused if u in by_url][:max_total]
    otel_emit.record("ensemble_decompositions", {
        "tool": "deep_research", "strategies": len(decomps),
        "candidates_fused": len(by_url), "retained": len(fused_sources)})
    return {"sources": fused_sources, "seen_urls": seen,
            "strategies": [d["strategy"] for d in decomps]}


# ── 5.2 cross-run contradiction detection (KG-backed; degrades to no-op) ───────
def cross_run_contradictions(verified: list[dict], question: str,
                             write_kg: bool = False) -> dict[str, Any]:
    """For each WELL-SUPPORTED new claim, ask the corpus whether a PRIOR run asserted
    something on the same topic, and entailment-check the prior snippet against the new
    claim. A 'contradicts' verdict is surfaced (and optionally written as a KG edge).
    No-op (empty) when the corpus/KG/entailment backend is unavailable — sovereign-safe."""
    strong = [c for c in verified if c.get("status") == "well-supported" and c.get("claim")]
    if not strong:
        return {"contradictions": [], "checked": 0, "backend": "off"}
    contradictions: list[dict[str, Any]] = []
    pairs: list[tuple[str, str]] = []
    meta: list[dict[str, Any]] = []
    for c in strong:
        pc = rc._mcp_call(rc.RAG_MCP_URL, "corpus_hit_check", {
            "query": c["claim"], "namespace_prefix": rc.RESEARCH_CORPUS_NS_PREFIX,
            "threshold": 0.5, "min_chunks": 1})
        res = (pc.get("result") or {}) if isinstance(pc, dict) else {}
        for ch in (res.get("chunks") or [])[:1]:
            snippet = ch.get("snippet", "")
            if snippet:
                pairs.append((c["claim"], snippet))
                meta.append({"claim": c["claim"], "prior": ch.get("source", "?")})
    if not pairs:
        return {"contradictions": [], "checked": len(strong), "backend": "corpus-empty"}
    labels = rc._label_support_batch([(cl, sn) for cl, sn in pairs])  # batched entailment
    for label, m in zip(labels, meta):
        if label == "contradicts":
            edge = {**m, "relation": "contradicts"}
            if write_kg:
                try:
                    import kg_provenance as kg
                    kg.add_fact_edge(m["claim"][:120], "contradicts", str(m["prior"])[:120],
                                     source_id="cross-run", props={"detected": _today_iso()})
                    edge["kg_written"] = True
                except Exception:  # noqa: BLE001
                    edge["kg_written"] = False
            contradictions.append(edge)
    otel_emit.record("cross_run_contradictions", {
        "checked": len(strong), "found": len(contradictions), "backend": "entailment"})
    return {"contradictions": contradictions, "checked": len(strong), "backend": "entailment"}
