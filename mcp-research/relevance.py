"""RISK-B remedy (Stage-6): a relevance + authority FILTER on research findings
BEFORE they feed the synth brief.

The suspicion: noisy/irrelevant research poisons the synth brief → confident WRONG
directives (caught by verify, but at cost). Precision matters more than recall
here, so this gate drops a finding unless it clears BOTH a source-authority floor
and a query-relevance floor. Feature-flagged (RESEARCH_RELEVANCE_FILTER, default
on) and threshold-tunable; off → every finding passes through (recall-max).

Relevance is a cheap lexical overlap (shingle Jaccard) between the finding text and
the synth query — no embedding endpoint needed, so it runs anywhere. Authority
reuses research_core.authority_score (peer-review / standards / official-docs rank).
"""
from __future__ import annotations

import os
import re
from typing import Any

import research_core as rc

RELEVANCE_FILTER = os.environ.get("RESEARCH_RELEVANCE_FILTER", "true").strip().lower() in (
    "1", "true", "yes", "on")
MIN_AUTHORITY = int(os.environ.get("RESEARCH_MIN_AUTHORITY", "2"))
MIN_RELEVANCE = float(os.environ.get("RESEARCH_MIN_RELEVANCE", "0.25"))

_STOP = {"the", "a", "an", "to", "of", "in", "for", "and", "or", "is", "how", "use"}


def _relevance(query: str, text: str) -> float:
    """Query-token CONTAINMENT: fraction of the query's content words that appear in
    the finding. Forgiving on short snippets (unlike shingle-Jaccard) so a clearly
    on-topic source clears the floor while off-topic noise does not."""
    q = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if w not in _STOP}
    t = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    if not q:
        return 0.0
    return len(q & t) / len(q)


def filter_findings(findings: list[dict], query: str, *, enabled: bool | None = None,
                    min_authority: int | None = None,
                    min_relevance: float | None = None) -> dict[str, Any]:
    """Keep only findings clearing BOTH the authority and relevance floors. Each
    finding: {text|claim|snippet, url, authority?}. Returns kept + dropped (with
    reasons) so the synth brief can ingest `kept` and the eval can measure how much
    noise the filter removes. When disabled, everything is kept (annotated)."""
    enabled = RELEVANCE_FILTER if enabled is None else enabled
    ma = MIN_AUTHORITY if min_authority is None else min_authority
    mr = MIN_RELEVANCE if min_relevance is None else min_relevance
    kept: list[dict] = []
    dropped: list[dict] = []
    for f in findings:
        text = f.get("text") or f.get("claim") or f.get("snippet") or ""
        url = f.get("url", "")
        auth = f.get("authority")
        if auth is None:
            auth = rc.authority_score(url) if url else 0
        rel = _relevance(query, text)
        annotated = {**f, "authority": auth, "relevance": round(rel, 4)}
        if not enabled:
            kept.append(annotated)
            continue
        if auth >= ma and rel >= mr:
            kept.append(annotated)
        else:
            why = (f"authority {auth} < {ma}" if auth < ma else f"relevance {rel:.3f} < {mr}")
            dropped.append({**annotated, "drop_reason": why})
    return {"ok": True, "enabled": enabled, "min_authority": ma, "min_relevance": mr,
            "kept": kept, "dropped": dropped, "n_in": len(findings),
            "n_kept": len(kept), "n_dropped": len(dropped)}
