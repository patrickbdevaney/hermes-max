#!/usr/bin/env python3
"""Smoke test for Phase 5 novel capabilities (novel.py). No live services.

Exercises the DETERMINISTIC cores of each capability with monkeypatched backends:
  5.1 disconfirm_queries (falsification angles) + verdict_downgrades (the metric)
  5.3 temporal_annotate (valid_as_of stamp + supersession by source year)
  5.4 rrf_fuse (cross-framing corroboration wins) + ensemble_decompositions
  5.2 cross_run_contradictions (entailment vs prior corpus; KG-write stubbed)
Exit non-zero on first failure (mirrors the other smoke tests)."""
from __future__ import annotations

import sys

import novel
import research_core as rc


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def part_adversarial() -> None:
    print("[5.1] adversarial wave: disconfirm queries + downgrade metric")
    rc.VLLM_BASE_URL = ""  # force the deterministic fallback path
    claims = [
        {"claim": "X scales linearly to 1M nodes", "status": "well-supported"},
        {"claim": "Y is the only viable approach", "status": "single-sourced"},
    ]
    qs = novel.disconfirm_queries(claims, n_claims=2)
    if not qs or not any("criticism" in q or "debunk" in q or "limitation" in q for q in qs):
        _fail(f"disconfirm_queries should produce falsification angles: {qs}")
    # the WELL-SUPPORTED claim is targeted first (it's the one worth breaking)
    if not any("X scales linearly" in q for q in qs):
        _fail(f"strongest claim should be targeted first: {qs}")
    _ok(f"disconfirm queries target strongest claim, falsification framing ({len(qs)} q)")

    pre = [{"claim": "A", "status": "well-supported"}, {"claim": "B", "status": "well-supported"}]
    post = [{"claim": "A", "status": "contradicted"}, {"claim": "B", "status": "well-supported"}]
    d = novel.verdict_downgrades(pre, post)
    if d["count"] != 1 or d["downgraded"][0]["claim"] != "A":
        _fail(f"verdict_downgrades should flag exactly A: {d}")
    _ok("verdict_downgrades counts claims that lost standing after counter-evidence")


def part_temporal() -> None:
    print("[5.3] temporal provenance: valid_as_of + supersession")
    sources = [
        {"url": "https://ex.org/paper-2019", "title": "Foundations 2019"},
        {"url": "https://ex.org/update-2025", "title": "Revised results 2025"},
    ]
    verified = [{"claim": "C", "status": "well-supported",
                 "sources": [{"url": "https://ex.org/paper-2019"},
                             {"url": "https://ex.org/update-2025"}]}]
    out = novel.temporal_annotate(verified, sources, as_of_iso="2026-06-03")
    f = out[0]
    if f.get("valid_as_of") != "2026-06-03":
        _fail(f"valid_as_of not stamped: {f}")
    if not f.get("superseded_by") or f["superseded_by"]["year"] != 2025:
        _fail(f"newer source should be flagged as superseding: {f}")
    _ok("findings stamped valid_as_of; newer (2025) source flagged over older (2019)")

    # single-dated / undated evidence → stamp but no false supersession
    v2 = [{"claim": "D", "status": "well-supported", "sources": [{"url": "https://ex.org/no-date"}]}]
    out2 = novel.temporal_annotate(v2, [{"url": "https://ex.org/no-date", "title": "Evergreen"}])
    if out2[0].get("superseded_by") is not None or not out2[0].get("valid_as_of"):
        _fail(f"undated evidence should stamp but not claim supersession: {out2[0]}")
    _ok("undated evidence: stamped, no spurious supersession")


def part_ensemble() -> None:
    print("[5.4] ensemble: RRF fusion + decomposition strategies")
    # url2 appears in all three lists (cross-framing corroboration) -> must rank #1
    lists = [["u1", "u2", "u3"], ["u2", "u4"], ["u5", "u2"]]
    fused = novel.rrf_fuse(lists)
    if fused[0][0] != "u2":
        _fail(f"cross-list corroborated id should rank first: {fused}")
    _ok(f"rrf_fuse ranks cross-framing corroboration first (u2={round(fused[0][1],4)})")

    decomps = novel.ensemble_decompositions("how does raft consensus handle leader election",
                                            ["leader election", "log replication"])
    strats = {d["strategy"] for d in decomps}
    if strats != {"plan-and-execute", "storm-perspective", "citation-seeded"}:
        _fail(f"expected 3 distinct strategies: {strats}")
    if not all(d["subgoals"] for d in decomps):
        _fail("every decomposition must have subgoals")
    _ok(f"ensemble_decompositions yields 3 distinct framings: {sorted(strats)}")


def part_cross_run() -> None:
    print("[5.2] cross-run contradiction: entailment vs prior corpus (KG-write stubbed)")
    verified = [{"claim": "The limit is 10k", "status": "well-supported"}]
    # stub the corpus to return a prior conflicting chunk
    rc._mcp_call = lambda url, tool, args: {
        "ok": True, "result": {"chunks": [{"snippet": "Prior research found the limit is 50k.",
                                           "source": "research/prior"}]}}
    # stub entailment to label it a contradiction
    novel.rc._label_support_batch = lambda pairs: ["contradicts"] * len(pairs)
    out = novel.cross_run_contradictions(verified, "what is the limit", write_kg=False)
    if out["backend"] != "entailment" or len(out["contradictions"]) != 1:
        _fail(f"should detect the contradiction vs the prior corpus claim: {out}")
    _ok("contradiction with a prior corpus claim is surfaced via entailment")

    # corpus empty -> graceful no-op, no false positives
    rc._mcp_call = lambda url, tool, args: {"ok": True, "result": {"chunks": []}}
    out2 = novel.cross_run_contradictions(verified, "q", write_kg=False)
    if out2["contradictions"] or out2["backend"] != "corpus-empty":
        _fail(f"empty corpus should be a clean no-op: {out2}")
    _ok("empty corpus -> clean no-op (no false contradictions)")


def main() -> None:
    part_adversarial()
    part_temporal()
    part_ensemble()
    part_cross_run()
    print("Phase 5 novel-capabilities smoke test PASSED")


if __name__ == "__main__":
    main()
