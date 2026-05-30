#!/usr/bin/env python3
"""Standalone smoke test for the directive-verify gate (Stage 3). Offline: no
sibling servers (KG/verify pointed at dead ports -> degrade). Symbol/file checks
run against the REAL hermes-max repo, so a real function passes and a hallucinated
one is caught. Asserts the Stage-3 DoD:

  • injected WRONG assumption (nonexistent function) is caught and NOT executed
  • a directive with TRUE assumptions + real APIs + concrete tests EXECUTES
  • missing apis_to_use rejects; missing tests reject
  • low-confidence + high-blast-radius triggers a second-opinion requirement
  • a provided AGREEING second opinion clears it; a DISAGREEING one -> escalate
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = str(HERE.parent)  # hermes-max repo root (real code for symbol checks)

# Sibling servers absent: dead ports + short timeout -> KG record + quick_check
# degrade to no-op, proving no hard dependency.
os.environ["MCP_KG_PORT"] = "59103"
os.environ["MCP_VERIFY_PORT"] = "59101"
os.environ["BRIEF_MCP_TIMEOUT"] = "2"

import directive_verify as dv  # noqa: E402


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _fail(m: str) -> None:
    print(f"  FAIL: {m}")
    sys.exit(1)


# real symbols that exist in this repo: classify_difficulty (escalation_core.py),
# run_role (conductor_core.py), brief_assemble (brief_assemble.py)
GOOD = {
    "ordered_steps": [
        {"step": "Call classify_difficulty on the signals to tag the subtask", "confidence": "high"},
        {"step": "Route via run_role for the synth role", "confidence": "high"},
    ],
    "files_to_touch": ["mcp-escalation/conductor_core.py"],
    "apis_to_use": ["classify_difficulty", "run_role()"],
    "tests_to_write": ["assert run_role('steer', prompt='x')['ok'] in (True, False)"],
    "pitfalls": ["do not raise into the core loop"],
    "assumptions": [
        "the function `classify_difficulty` exists in mcp-escalation/escalation_core.py",
        "`run_role` is defined in the conductor core",
    ],
}


def main() -> None:
    # 1. clean directive with TRUE assumptions + real APIs + tests -> execute
    r = dv.directive_verify(GOOD, repo=REPO)
    if not r["execute"] or r["action"] != "execute":
        _fail(f"valid directive should execute: {r['reason']} | gates={r['gates']}")
    if not r["gates"]["assumptions"]["passed"]:
        _fail(f"true assumptions should pass: {r['gates']['assumptions']}")
    _ok("valid directive (true assumptions, real APIs, concrete tests) -> EXECUTE")

    # 2. injected WRONG assumption (nonexistent function) -> caught, NOT executed
    bad = dict(GOOD)
    bad["assumptions"] = GOOD["assumptions"] + [
        "the function `compute_quantum_flux_xyz` exists and returns the answer"]
    rb = dv.directive_verify(bad, repo=REPO)
    if rb["execute"]:
        _fail("directive with a false assumption must NOT execute")
    if rb["action"] != "reject_and_rebrief":
        _fail(f"false assumption should reject_and_rebrief: {rb['action']}")
    falses = [c["target"] for c in rb["gates"]["assumptions"]["false"]]
    if "compute_quantum_flux_xyz" not in falses:
        _fail(f"the hallucinated function should be flagged false: {falses}")
    _ok(f"WRONG assumption caught ({falses}) -> rejected, NOT executed")

    # 2b. false FILE assumption likewise caught
    badf = dict(GOOD)
    badf["assumptions"] = ["the file mcp-escalation/does_not_exist_zzz.py defines the entrypoint"]
    rf = dv.directive_verify(badf, repo=REPO)
    if rf["execute"] or not rf["gates"]["assumptions"]["false"]:
        _fail(f"false file assumption should be caught: {rf['gates']['assumptions']}")
    _ok("false FILE assumption caught -> rejected")

    # 3. missing apis_to_use -> reject
    bad_api = dict(GOOD)
    bad_api["apis_to_use"] = ["totally_made_up_api_zzz()"]
    bad_api["assumptions"] = []  # isolate the API gate
    ra = dv.directive_verify(bad_api, repo=REPO)
    if ra["execute"] or not ra["gates"]["static"]["missing_apis"]:
        _fail(f"missing API should reject: {ra['gates']['static']}")
    _ok(f"missing apis_to_use rejected: {ra['gates']['static']['missing_apis']}")

    # 4. no tests_to_write -> reject
    no_tests = dict(GOOD)
    no_tests["tests_to_write"] = []
    rt = dv.directive_verify(no_tests, repo=REPO)
    if rt["execute"] or rt["gates"]["tests"]["passed"]:
        _fail(f"missing tests should reject: {rt['gates']['tests']}")
    _ok("no concrete tests_to_write -> rejected (need the objective oracle)")

    # 5. low-confidence + high-blast-radius -> needs a second opinion
    risky = {
        "ordered_steps": [{"step": "Rewrite the core routing loop end to end", "confidence": "low"}],
        "files_to_touch": ["mcp-escalation/conductor_core.py", "mcp-search/search_core.py",
                           "mcp-verify/verify_core.py", "mcp-checkpoint/checkpoint_core.py"],
        "apis_to_use": ["run_role"],
        "tests_to_write": ["assert something meaningful holds after the change"],
        "assumptions": ["`run_role` is defined in the conductor core"],
    }
    rr = dv.directive_verify(risky, repo=REPO)
    if rr["execute"] or rr["action"] != "get_second_opinion":
        _fail(f"low-conf + high-blast should demand a second opinion: {rr['action']} | "
              f"{rr['gates']['confidence']}")
    if rr["gates"]["confidence"]["blast_radius"] != "high":
        _fail(f"blast radius should be high: {rr['gates']['confidence']}")
    _ok(f"low-confidence + high-blast-radius -> {rr['action']} "
        f"(blast={rr['gates']['confidence']['blast_radius']})")

    # 5b. agreeing second opinion clears it; disagreeing -> escalate/human
    agree2 = dict(risky)  # same files -> high overlap
    rok = dv.directive_verify(risky, repo=REPO, second_directive=agree2)
    if not rok["execute"]:
        _fail(f"an agreeing second opinion should clear the gate: {rok['reason']}")
    disagree2 = {
        "ordered_steps": [{"step": "Do something completely different", "confidence": "high"}],
        "files_to_touch": ["docs/readme.md", "scripts/other.sh"],
        "tests_to_write": ["assert x"], "assumptions": [],
    }
    rno = dv.directive_verify(risky, repo=REPO, second_directive=disagree2)
    if rno["execute"] or rno["action"] != "escalate_or_human":
        _fail(f"disagreeing opinions should escalate/human: {rno['action']}")
    _ok("agreeing 2nd opinion -> execute; disagreeing -> escalate_or_human")

    # 6. compare_directives sanity
    c = dv.compare_directives(risky, agree2)
    d = dv.compare_directives(risky, disagree2)
    if not c["agree"] or d["agree"]:
        _fail(f"compare_directives wrong: agree={c}, disagree={d}")
    _ok(f"compare_directives: same-files agree={c['agree']}, diff disagree (overlap {d['file_overlap']})")


if __name__ == "__main__":
    main()
    print("directive-verify smoke test PASSED")
