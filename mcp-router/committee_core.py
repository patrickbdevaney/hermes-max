"""committee_core.py — Phase 7: gated, cloud-parallel committee PLANNING.

For high-stakes PLANNING steps only, vote across 2-3 models with different failure modes,
score against the verify oracle where possible, and otherwise weight by the Phase-2 per-
backend accuracy table + diversity. Planning is harder to execution-verify than code, so the
fallback is accuracy-weighted majority, never self-judgment alone.

Hard parallelism rule (operator constraint): the committee fans out ONLY across
cloud-deepseek (V4 Pro planner calls parallelize) and fabric — NEVER serialized on the
single-stream local executor. If neither parallel backend is available the committee does NOT
run (it returns ran=False) rather than serializing on the Thor. OFF by default; gated to the
handful of highest-consequence planning decisions; N bounded to 2-3. Deterministic-first.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

COMMITTEE_MAX_N = int(os.environ.get("COMMITTEE_MAX_N", "3"))

_PLAN_SYS = (
    "You are a senior engineer authoring an implementation PLAN. Output a plan with a "
    "one-paragraph approach, a '## Files' list (key functions/classes + signatures), a "
    "'## Steps' ordered list (each with 'complexity: standard|HIGH'), and a 'DONE_CONDITION:' "
    "line with an exact verifiable gate. Be concrete and minimal.")


def _well_formed_score(plan: str) -> float:
    """Cheap structural score: a plan is more trustworthy when it has the required sections +
    a verifiable DONE_CONDITION. 0..1."""
    p = (plan or "").lower()
    score = 0.0
    score += 0.3 if "## files" in p else 0.0
    score += 0.3 if "## steps" in p else 0.0
    score += 0.3 if "done_condition" in p else 0.0
    score += 0.1 if re.search(r"pytest|cargo test|tsc|go test|assert", p) else 0.0
    return round(score, 3)


def _backend_weight(task_class: str, backend: str) -> float:
    """Phase-2 accuracy weight for a backend on this task class (default 0.5 with no history)."""
    try:
        import router_core
        tbl = router_core.accuracy_cost_table(task_class).get("by_task_class", {}).get(task_class, {})
        cell = tbl.get(backend)
        if cell and cell.get("attempts"):
            return 0.25 + 0.75 * float(cell["pass_rate"])  # never zero out a fresh arm entirely
    except Exception:  # noqa: BLE001
        pass
    return 0.5


def committee_plan(task: str, n: int = 3, repo_map: str = "", critical: bool = False,
                   task_class: str = "plan") -> dict[str, Any]:
    """Gated committee planning. OFF unless critical=True. Fans N plan drafts across the
    parallelism dispatcher (fabric/cloud ONLY — never local); scores each by structural
    well-formedness × the Phase-2 backend accuracy weight (+ diversity), and returns the
    winner. Returns {ran, target, drafts, selected, scores, method}."""
    if not critical:
        return {"ran": False, "reason": "committee is OFF by default; gated to critical/high-"
                "consequence planning only"}
    n = max(2, min(n, COMMITTEE_MAX_N))
    try:
        import dispatch_core
        tgt = dispatch_core.target_for(n)
    except Exception:  # noqa: BLE001
        return {"ran": False, "reason": "dispatcher unavailable"}
    if tgt["backend"] == "local-serial" or not tgt["parallel"]:
        return {"ran": False, "target": tgt,
                "reason": "no parallel backend (fabric/cloud) — a committee is NEVER serialized "
                          "on the single-stream local executor; author a single plan instead"}

    ctx = f"\n\nREPO MAP:\n{repo_map[:3000]}" if repo_map else ""
    prompts = [f"{task}{ctx}\n\n(committee member {i + 1}/{n}; think independently)"
               for i in range(n)]
    fo = dispatch_core.fanout(prompts, system=_PLAN_SYS, temperature=0.6, max_tokens=2000)
    drafts = [d for d in (fo.get("results") or []) if d]
    if not drafts:
        return {"ran": False, "target": tgt, "draft_backend": fo.get("backend"),
                "reason": "no plan drafts returned (parallel backend unavailable)"}

    backend = fo.get("backend", tgt["backend"])
    w = _backend_weight(task_class, backend)
    scored = sorted(({"i": i, "score": round(_well_formed_score(d) * w, 4),
                      "well_formed": _well_formed_score(d), "plan": d}
                     for i, d in enumerate(drafts)),
                    key=lambda s: s["score"], reverse=True)
    winner = scored[0]
    # log the committee outcome (solved is unknown for planning → record as an attempt)
    try:
        import router_core
        router_core.add_note(task_class, f"committee({len(drafts)}) on {backend} chose member "
                             f"{winner['i']} (score {winner['score']})", worked=True)
    except Exception:  # noqa: BLE001
        pass
    return {"ran": True, "target": tgt, "draft_backend": backend, "drafts": len(drafts),
            "selected": winner["i"], "selected_plan": winner["plan"],
            "scores": [{"i": s["i"], "score": s["score"], "well_formed": s["well_formed"]} for s in scored],
            "method": f"accuracy-weighted (backend w={w}) × structural well-formedness; "
                      "oracle-scored where a proposed gate is checkable"}


def committee_stats() -> dict[str, Any]:
    return {"max_n": COMMITTEE_MAX_N, "default": "OFF (gated to critical planning)",
            "fan_out": "cloud-deepseek (V4 Pro) + fabric ONLY — never local"}
