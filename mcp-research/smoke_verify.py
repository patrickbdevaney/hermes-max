#!/usr/bin/env python3
"""Standalone smoke for Stage 5 — KG provenance + decomposed verification gate.

No live services (KG call + entailment LLM monkeypatched; corpus -> temp dir).
Asserts:
  [A] verify_claim resolves each source to a stored chunk and entails it; ≥2
      independent supporting domains => well-supported, carrying resolvable IDs
  [B] entailment FLAGS an unsupported claim (chunk doesn't entail) -> not asserted
  [C] a contradiction between two sources is surfaced with BOTH citations (not averaged)
  [D] KG ingestion records episodes/entities/edges with source_id + temporal
      validity; an invented relation is rejected
  [E] decompose_question yields complementary sub-questions + diverse paraphrases
      + per-source syntax; degrades deterministically with no model
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
    import verify_gate as vg
    import kg_provenance as kg
    import research_core as rc

    tmp = tempfile.mkdtemp(prefix="verify_smoke_")
    c.CORPUS_DIR = tmp
    c.CLOUD_DISTILL = False

    # seed two resolvable corpus docs (distinct domains) for a claim
    c._rag_index = lambda *a, **k: {"ok": True, "result": {"ok": True}}
    d1 = c.write_corpus_doc("research/zk", "arxiv",
                            "Polynomial commitments enable succinct ZK proofs with O(1) verification.",
                            {"title": "ZK Paper", "source_url": "https://arxiv.org/abs/zk1"})
    d2 = c.write_corpus_doc("research/zk", "eip_erc",
                            "EIP-4844 introduces blob transactions priced by a separate fee market.",
                            {"title": "EIP-4844", "source_url": "https://eips.ethereum.org/EIPS/eip-4844"})

    # ---- [A] well-supported: 2 independent domains, entailment 'supports' ----
    print("[A] verify_claim — resolvable + entailed -> well-supported")
    rc.VLLM_BASE_URL = "http://stub"  # enable the entailment branch
    vg._entail = lambda claim, chunk, st="web": "supports"
    claim = "ZK proofs can be verified succinctly"
    srcs = [{"source_id": d1["relpath"], "url": "https://arxiv.org/abs/zk1", "source_type": "arxiv"},
            {"source_id": d2["relpath"], "url": "https://eips.ethereum.org/EIPS/eip-4844", "source_type": "eip_erc"}]
    v = vg.verify_claim(claim, srcs)
    if v["status"] != "well-supported":
        _fail(f"2 independent supports should be well-supported: {v}")
    if len(v["source_ids"]) != 2 or any(not s for s in v["source_ids"]):
        _fail(f"claim must carry resolvable source IDs: {v}")
    if not all(vd["resolvable"] for vd in v["verdicts"]):
        _fail(f"both sources should resolve to stored chunks: {v['verdicts']}")
    _ok(f"well-supported, {len(v['source_ids'])} resolvable source IDs, all entailed")

    # ---- [B] entailment flags an unsupported claim ----
    print("[B] entailment flags unsupported")
    vg._entail = lambda claim, chunk, st="web": "neutral"  # chunk doesn't back it
    v = vg.verify_claim("ZK proofs cure cancer", srcs)
    if v["status"] not in ("unsupported", "single-sourced", "candidate-unverified"):
        _fail(f"neutral entailment should NOT be well-supported: {v}")
    if v["supports"]:
        _fail(f"no source should count as support: {v}")
    _ok(f"unsupported claim flagged status={v['status']} (0 entailed supports)")

    # unresolvable source is counted/flagged
    v = vg.verify_claim("x", [{"url": "https://nowhere.example/a", "source_id": "research/zk/arxiv/ghost.md"}])
    if v["unresolved_sources"] < 1:
        _fail(f"unresolvable source should be flagged: {v}")
    _ok(f"unresolvable source flagged (unresolved_sources={v['unresolved_sources']})")

    # ---- [C] contradiction surfaced with both citations ----
    print("[C] contradiction surfaced with both citations")
    def _split_entail(claim, chunk, st="web"):
        return "supports" if "Polynomial" in chunk else "contradicts"
    vg._entail = _split_entail
    vf = vg.verify_findings([{"claim": "succinct verification is possible", "sources": srcs}])
    cons = vf["contradictions"]
    if not cons or not cons[0]["supported_by"] or not cons[0]["contradicted_by"]:
        _fail(f"contradiction should surface BOTH sides' citations: {vf}")
    if "averaged" not in cons[0]["note"]:
        _fail("contradiction note should state it is not averaged")
    _ok(f"conflict surfaced: supported_by={cons[0]['supported_by']} contradicted_by={cons[0]['contradicted_by']}")

    # ---- [D] KG ingestion: provenance + temporal validity; bad rel rejected ----
    print("[D] KG provenance + temporal validity")
    calls = []
    kg._kg_call = lambda tool, args: (calls.append((tool, args)) or {"ok": True, "result": {"ok": True}})
    r = kg.add_fact_edge("paperA", "cites", "paperB", source_id="research/zk/arxiv/zk-paper.md",
                         valid_from="2026-01-01")
    rel_calls = [a for (t, a) in calls if t == "record_relation"]
    if not r["ok"] or rel_calls[-1]["props"]["source_id"] != "research/zk/arxiv/zk-paper.md":
        _fail(f"fact edge must carry source_id in props: {rel_calls[-1] if rel_calls else None}")
    if rel_calls[-1]["props"].get("valid_from") != "2026-01-01" or "valid_until" not in rel_calls[-1]["props"]:
        _fail(f"fact edge must carry temporal validity: {rel_calls[-1]['props']}")
    _ok("fact edge carries source_id + valid_from/valid_until")

    bad = kg.add_fact_edge("a", "frobnicates", "b", source_id="s")
    if bad["ok"]:
        _fail("an invented relation should be rejected, not stored")
    _ok(f"invented relation rejected: {bad['error'][:50]}")

    calls.clear()
    ep = kg.add_episode("research/zk", "ZK proofs summary", "research/zk/arxiv/zk-paper.md",
                        entities=[{"type": "technique", "name": "polynomial-commitment"}],
                        edges=[{"a": "polynomial-commitment", "rel": "implements", "b": "succinct-zk"}])
    if not ep["ok"] or ep["entities"] != 1 or ep["edges"] != 1:
        _fail(f"episode should record entity + edge: {ep}")
    sup = kg.mark_superseded("eip-old", "eip-4844", "research/zk/eip_erc/eip-4844.md", as_of="2026-03-01")
    ent_calls = [a for (t, a) in calls if t == "record_entity"]
    if not any(a["props"].get("valid_until") == "2026-03-01" for a in ent_calls):
        _fail(f"supersede should stamp valid_until on the old entity: {ent_calls}")
    _ok(f"episode (1 entity, 1 edge) + supersede stamps valid_until (temporal)")

    # ---- [E] decompose_question (echo-chamber fix) ----
    print("[E] decompose_question")
    rc._llm = lambda *a, **k: ('[{"sub_question":"How do blobs price?","paraphrases":'
                               '["blob fee market","EIP-4844 blob gas","danksharding pricing"]}]')
    dq = vg.decompose_question("How does EIP-4844 pricing work?")
    if not dq["ok"] or not dq["sub_questions"][0]["paraphrases"]:
        _fail(f"decompose should yield paraphrases: {dq}")
    ps = dq["sub_questions"][0]["per_source"]
    sample = next(iter(ps.values()))
    if "arxiv" not in sample or not sample["arxiv"].startswith("all:"):
        _fail(f"per-source syntax should translate (arxiv all:): {sample}")
    _ok(f"sub-questions w/ {len(dq['sub_questions'][0]['paraphrases'])} paraphrases + per-source syntax")

    rc._llm = lambda *a, **k: None  # no model -> deterministic variants
    dq = vg.decompose_question("anything")
    if not dq["ok"] or not dq["sub_questions"]:
        _fail(f"decompose should degrade deterministically: {dq}")
    _ok("no model -> deterministic sub-question variants (degrades)")

    print("mcp-research KG-provenance + verify-gate (Stage 5) smoke test PASSED")


if __name__ == "__main__":
    main()
