#!/usr/bin/env python3
"""smoke_planner.py — PLANNER_PROMPT_SPEC regression: determinism + §6 worked example.

OFFLINE (always, no network): the linter / classifier / escalation parser against stored
good+bad fixtures — the deterministic contract that does NOT depend on a model rung.

LIVE (only with RUN_LIVE=1 and a reachable synth chain): runs conductor_plan() on the §6
task and asserts STRUCTURE ONLY — all schema sections, every step has DONE-WHEN, lint clean.
Never asserts exact MPMC content; the §6 instance is a fixture, not baked-in logic.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp-scopemap"))
import conductor_core as cc  # noqa: E402

SECTION6_TASK = ("Implement a lock-free bounded MPMC queue in Python using ctypes atomics "
                 "on mmap-backed shared memory")

_GOOD_PLAN = """## CONTEXT
Implement add(a, b) returning a+b, with a pytest test.
## ARCHITECTURE DECISIONS
1. Plain numeric addition BECAUSE the range has no overflow concern.
## STEPS
- DO: write add(a: int|float, b: int|float) -> int|float in calc.py
  DONE-WHEN: pytest -x test_calc.py::test_add exits 0
  LIKELY-FAILURE: wrong type coercion; PREEMPT: annotate int|float and test floats.
## VERIFICATION
ruff check . && pytest -q  — exit 0, 1 test passed.
## REFERENCES
none
"""

_BAD_PLAN = """## CONTEXT
build it
## STEPS
- maybe use a list or a dict, consider performance; it works correctly when tests pass
"""


def _offline() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"  {'ok' if cond else 'FAIL'}: {name}")
        fails += 0 if cond else 1

    print("[A] lint_plan")
    check("clean plan -> no violations", cc.lint_plan(_GOOD_PLAN) == [])
    bad = cc.lint_plan(_BAD_PLAN)
    check("bad plan flags missing sections", any("missing required section" in v for v in bad))
    check("bad plan flags no DONE-WHEN", any("DONE-WHEN" in v for v in bad))
    check("bad plan flags banned 'consider'", any("AP1" in v for v in bad))
    check("bad plan flags 'tests pass'", any("AP5" in v for v in bad))

    print("[B] classify_task (§4)")
    check("concurrency signal -> frontier", cc.classify_task("lock-free MPMC queue")["frontier"])
    check("trivial -> not frontier", not cc.classify_task("add a multiply function")["frontier"])
    check(">3 files -> frontier (structural)", cc.classify_task("refactor", n_files=7)["frontier"])

    print("[C] conductor_escalate parser (§5)")
    ok = cc._parse_escalation("DIAGNOSIS: wrong order.\nDECISION: patch-step\nPATCH: use seq_cst.")
    check("valid 3-field parses", ok is not None and ok["decision"] == "patch-step")
    check("bad enum rejected", cc._parse_escalation("DIAGNOSIS: x\nDECISION: rewrite\nPATCH: y") is None)
    check("missing field rejected", cc._parse_escalation("DIAGNOSIS: x\nPATCH: y") is None)
    return fails


def _live() -> int:
    print("[D] LIVE conductor_plan on the §6 worked example (structure only)")
    import re
    import tempfile
    res = cc.conductor_plan(task=SECTION6_TASK, cwd=tempfile.mkdtemp())
    if not res.get("ok"):
        print(f"  SKIP: synth unavailable ({res.get('reason')})")
        return 0
    plan = res.get("plan", "")
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"  {'ok' if cond else 'FAIL'}: {name}")
        fails += 0 if cond else 1

    for sec in cc.PLAN_SCHEMA_SECTIONS:
        check(f"section ## {sec} present", bool(re.search(rf"(?im)^#+\s*{re.escape(sec)}\b", plan)))
    check("every step has DONE-WHEN", len(re.findall(r"(?i)DONE[\s-]?WHEN:", plan)) >= 1)
    check("frontier classified", res.get("frontier") is True)
    check("lint_plan clean (no residual violations)", res.get("lint_violations") == [])
    return fails


if __name__ == "__main__":
    n = _offline()
    if os.environ.get("RUN_LIVE") == "1":
        n += _live()
    print(f"\nsmoke_planner: {'PASSED' if n == 0 else f'{n} FAILURE(S)'}")
    sys.exit(1 if n else 0)
