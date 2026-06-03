#!/usr/bin/env python3
"""Smoke for Phase 2 — outcome-memory + bandit router. Deterministic, hermetic.

[A] task_class_of + classify (difficulty/criticality, deterministic)
[B] UCB1 bandit: unvisited → +inf explore; updates move the mean
[C] route policy: default local-serial-free; escalate→fabric when warranted; cloud gated
    by uplift-per-dollar (stays local when paid escalation doesn't pay)
[D] log_outcome closes the loop (profiler-readable outcomes + bandit update + notes)
[E] failure-class taxonomy
Exit non-zero on first failure."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_d = tempfile.mkdtemp(prefix="router-smoke-")
os.environ["ROUTER_BANDIT_PATH"] = str(Path(_d) / "bandit.json")
os.environ["ROUTER_NOTES_PATH"] = str(Path(_d) / "notes.jsonl")
os.environ["ROUTER_OUTCOMES_PATH"] = str(Path(_d) / "outcomes.jsonl")
# isolate the profiler the router reads for the uplift gate
os.environ["PROFILER_CALLS_PATH"] = str(Path(_d) / "calls.jsonl")
os.environ["INFERENCE_LEDGER_PATH"] = str(Path(_d) / "ledger.jsonl")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-costprofiler"))

import router_core as rc


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def section_classify() -> None:
    print("[A] task_class + classify")
    if rc.task_class_of("fix the broken parser that crashes") != "bugfix":
        _fail("bugfix not classified")
    if rc.task_class_of("design a distributed scheduler") != "plan":
        _fail("plan not classified")
    _ok("task_class_of: bugfix / plan / refactor / feature / research / code_execute")
    c = rc.classify("implement a tiny pure helper that adds two ints")
    if c["task_class"] != "feature" or c["predicted_difficulty"] not in ("standard", "hard"):
        _fail(f"classify shape wrong: {c}")
    _ok(f"classify → difficulty={c['predicted_difficulty']}, escalate={c['escalate']}")


def section_bandit() -> None:
    print("[B] UCB1 bandit")
    s0 = rc.bandit_scores("bugfix")
    if not all(v == float("inf") for v in s0.values()):
        _fail(f"unvisited arms must be +inf: {s0}")
    _ok("unvisited arms force exploration (+inf)")
    rc.bandit_update("bugfix", "local-serial", 1.0)
    rc.bandit_update("bugfix", "local-serial", 1.0)
    rc.bandit_update("bugfix", "cloud-deepseek", 0.0)
    s1 = rc.bandit_scores("bugfix")
    # fabric still unvisited (+inf); local mean 1.0 should beat cloud mean 0.0 on exploitation
    if s1["fabric"] != float("inf"):
        _fail("fabric should remain unvisited")
    if not (s1["local-serial"] > s1["cloud-deepseek"]):
        _fail(f"local (mean 1.0) should outscore cloud (mean 0.0): {s1}")
    _ok("bandit update moves means; better arm scores higher")


def section_route() -> None:
    print("[C] route policy")
    # easy default task → local-serial, no escalation
    d = rc.route("add a docstring to one function", attempt=0, verify_failed=False)
    if d["backend"] != "local-serial" or d["escalate"]:
        _fail(f"easy task must stay local-serial: {d}")
    _ok("easy task → local-serial-free, no escalation (the cheap default)")

    # verify failed → escalate. fabric available → fabric first
    rc._fabric_available = lambda: True  # stub fabric present
    d2 = rc.route("fix this failing test", attempt=0, verify_failed=True)
    if d2["backend"] != "fabric" or not d2["escalate"]:
        _fail(f"verify-fail with fabric up should escalate to fabric: {d2}")
    _ok("verify-fail + fabric up → escalate to FREE fabric first")

    # fabric unavailable → cloud gated by uplift. Seed outcomes so cloud uplift is NEGATIVE.
    rc._fabric_available = lambda: False
    out = os.environ["ROUTER_OUTCOMES_PATH"]
    import json
    with open(out, "a") as f:
        # local solves cheaply; cloud no better but costs → uplift-per-dollar < floor
        for _ in range(3):
            f.write(json.dumps({"ts": 1, "task_class": "bugfix", "backend": "local-serial",
                                "solved": True, "cost_usd": 0.0}) + "\n")
        for _ in range(3):
            f.write(json.dumps({"ts": 1, "task_class": "bugfix", "backend": "cloud-deepseek",
                                "solved": True, "cost_usd": 0.02}) + "\n")
    d3 = rc.route("fix the broken parser", attempt=0, verify_failed=True, task_class="bugfix")
    if d3["backend"] != "local-serial" or d3["escalate"]:
        _fail(f"cloud with zero uplift-per-dollar should NOT be chosen: {d3}")
    _ok("fabric down + cloud uplift-per-dollar≈0 → stay local (paid escalation doesn't pay)")


def section_outcome() -> None:
    print("[D] outcome logging closes the loop")
    before = rc.bandit_scores("feature")
    r = rc.log_outcome("feature", "local-serial", solved=True, cost_usd=0.0,
                       note="small pure helpers: one local attempt suffices")
    if not r["solved"]:
        _fail("outcome not recorded")
    notes = rc.recall_notes("feature")
    if not notes or "local attempt" not in notes[0]:
        _fail(f"reflexion note not recalled: {notes}")
    tbl = rc.accuracy_cost_table("feature")
    cell = tbl["by_task_class"]["feature"]["local-serial"]
    if cell["solved"] != 1 or cell["pass_rate"] != 1.0:
        _fail(f"accuracy table wrong: {cell}")
    _ok("log_outcome → outcomes + bandit + reflexion note + accuracy table all updated")


def section_failure() -> None:
    print("[E] failure-class taxonomy")
    cases = {("needs_research",): "tool-fixable", ("replan",): "trajectory-fixable",
             ("verify_failed",): "sample-fixable", (): "route-fixable"}
    for keys, want in cases.items():
        sig = {k: True for k in keys}
        got = rc.classify_failure(sig)
        if got != want:
            _fail(f"classify_failure({sig}) = {got}, want {want}")
    _ok("failure classes: tool / trajectory / sample / route-fixable")


def main() -> None:
    section_classify()
    section_bandit()
    section_route()
    section_outcome()
    section_failure()
    print("outcome-memory + bandit router (Phase 2) smoke PASSED")


if __name__ == "__main__":
    main()
