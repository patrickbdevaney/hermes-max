#!/usr/bin/env python3
"""Smoke for Phase 1 — cost/latency/backend profiler. Deterministic, hermetic (temp paths).

[A] backend_of maps providers → the three (+frontier) named backends
[B] log_call + report rolls up BY BACKEND (calls/tokens/$/wall p50,p95)
[C] report INGESTS the lib/inference ledger (cost-bearing calls tagged by backend)
[D] cost_per_solved_task + uplift_per_dollar join the outcome log (and degrade w/o it)
Exit non-zero on first failure."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# hermetic paths BEFORE importing profiler_core (it reads env at use-time, so set first)
_d = tempfile.mkdtemp(prefix="profiler-smoke-")
os.environ["PROFILER_CALLS_PATH"] = str(Path(_d) / "calls.jsonl")
os.environ["INFERENCE_LEDGER_PATH"] = str(Path(_d) / "ledger.jsonl")
os.environ["ROUTER_OUTCOMES_PATH"] = str(Path(_d) / "outcomes.jsonl")

import profiler_core as pc


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def section_backend() -> None:
    print("[A] backend_of mapping")
    cases = {"local_vllm": "local-serial", "groq": "fabric", "cerebras": "fabric",
             "deepinfra": "cloud-deepseek", "deepseek_direct": "cloud-deepseek",
             "anthropic": "cloud-frontier"}
    for prov, want in cases.items():
        got = pc.backend_of(prov)
        if got != want:
            _fail(f"backend_of({prov}) = {got}, want {want}")
    _ok(f"providers map to backends: {sorted(set(cases.values()))}")


def section_rollup() -> None:
    print("[B] log_call + per-backend rollup")
    pc.log_call("local-serial", "code_execute", in_tok=1000, out_tok=500, cost_usd=0.0, wall_ms=8000)
    pc.log_call("local-serial", "code_execute", in_tok=800, out_tok=400, cost_usd=0.0, wall_ms=12000)
    pc.log_call("fabric", "research_fanout", in_tok=2000, out_tok=300, cost_usd=0.0, wall_ms=900)
    r = pc.report("all")
    ls = r["by_backend"]["local-serial"]
    if ls["calls"] != 2 or ls["out_tok"] != 900:
        _fail(f"local-serial rollup wrong: {ls}")
    if ls["wall_ms_p50"] < 8000 or r["by_backend"]["fabric"]["calls"] != 1:
        _fail(f"rollup p50/fabric wrong: {r['by_backend']}")
    _ok(f"rollup by backend: local={ls['calls']} calls p50={ls['wall_ms_p50']}ms, "
        f"fabric={r['by_backend']['fabric']['calls']}")


def section_ledger() -> None:
    print("[C] ingests the lib/inference ledger (cost-bearing calls)")
    led = os.environ["INFERENCE_LEDGER_PATH"]
    with open(led, "w") as f:
        # a paid DeepInfra planner call + a free groq call, ledger schema
        f.write(json.dumps({"ts": __import__("time").time(), "role": "code_plan",
                            "provider": "deepinfra", "model": "v4-pro", "in_tok": 4000,
                            "out_tok": 800, "cost_usd": 0.0075, "wall_ms": 1500}) + "\n")
        f.write(json.dumps({"ts": __import__("time").time(), "role": "research_fanout",
                            "provider": "groq", "model": "llama", "in_tok": 1200,
                            "out_tok": 200, "cost_usd": 0.0, "wall_ms": 400}) + "\n")
    r = pc.report("all")
    cd = r["by_backend"]["cloud-deepseek"]
    if cd["calls"] != 1 or abs(cd["cost_usd"] - 0.0075) > 1e-9:
        _fail(f"ledger cloud-deepseek not ingested with cost: {cd}")
    if r["total_usd"] < 0.0074:
        _fail(f"total_usd should include the paid call: {r['total_usd']}")
    _ok(f"ledger ingested: cloud-deepseek cost=${cd['cost_usd']}, total=${r['total_usd']}")


def section_outcomes() -> None:
    print("[D] outcome-joined queries (cost_per_solved, uplift_per_dollar)")
    q0 = pc.cost_per_solved_task()
    if q0.get("status") != "no_outcomes":
        _fail(f"with no outcomes should report no_outcomes: {q0}")
    _ok("cost_per_solved degrades cleanly with no outcomes logged")
    # write outcomes: local-serial cheaper-but-lower-pass, cloud higher-pass-at-cost
    out = os.environ["ROUTER_OUTCOMES_PATH"]
    rows = (
        [{"ts": 1, "task_class": "hard_bug", "backend": "local-serial", "solved": False, "cost_usd": 0.0}] * 3 +
        [{"ts": 1, "task_class": "hard_bug", "backend": "local-serial", "solved": True, "cost_usd": 0.0}] * 1 +
        [{"ts": 1, "task_class": "hard_bug", "backend": "cloud-deepseek", "solved": True, "cost_usd": 0.01}] * 3 +
        [{"ts": 1, "task_class": "hard_bug", "backend": "cloud-deepseek", "solved": False, "cost_usd": 0.01}] * 1)
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    cps = pc.cost_per_solved_task("hard_bug")
    hb = cps["by_task_class"]["hard_bug"]
    if hb["solved"] != 4 or hb["attempts"] != 8:
        _fail(f"cost_per_solved counts wrong: {hb}")
    _ok(f"cost_per_solved: {hb['solved']}/{hb['attempts']} solved, ${hb['total_cost']} spent")
    up = pc.uplift_per_dollar("hard_bug")
    cd = up["by_backend"]["cloud-deepseek"]
    ls = up["by_backend"]["local-serial"]
    if not (cd["pass_rate"] > ls["pass_rate"]):
        _fail(f"cloud should show higher pass-rate: {up['by_backend']}")
    if "uplift_per_dollar" not in cd:
        _fail(f"uplift_per_dollar not computed vs baseline: {cd}")
    _ok(f"uplift_per_dollar: cloud pass={cd['pass_rate']} vs local {ls['pass_rate']}, "
        f"uplift/$={cd['uplift_per_dollar']}")


def main() -> None:
    section_backend()
    section_rollup()
    section_ledger()
    section_outcomes()
    print("cost/latency profiler (Phase 1) smoke PASSED")


if __name__ == "__main__":
    main()
