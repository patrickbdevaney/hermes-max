#!/usr/bin/env python3
"""Smoke for Phase 4 — verification-driven generation + bounded reflection.

The generator is a stub (no live model) that returns a BUGGY solution first and, once it
sees the verify counterexample as critique, returns the CORRECT one — proving the loop
consumes counterexamples and is bounded. Oracle = the real verify gate (pytest).

[A] author_contract degrades cleanly with no model (tests-as-oracle)
[B] reflect_loop: buggy→(critique)→correct within k; attempts counted; bounded
[C] reflect_loop: a generator that never fixes it stops at k (no unbounded loop)
[D] no tests → does not run (needs a verifiable target)
Exit non-zero on first failure."""
from __future__ import annotations

import os
import sys

import vdg_core


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


TESTS = {"test_abs.py": ("from solution import absval\n"
                         "def test_pos(): assert absval(3) == 3\n"
                         "def test_neg(): assert absval(-4) == 4\n")}
BUGGY = "def absval(x):\n    return x  # BUG: not abs\n"
CORRECT = "def absval(x):\n    return x if x >= 0 else -x\n"


def section_contract() -> None:
    print("[A] author_contract (no model → tests-as-oracle)")
    a = vdg_core.author_contract("implement absval(x) returning |x|")
    if a["contract"] is not None or "oracle" not in a["method"]:
        _fail(f"no model should degrade to tests-as-oracle: {a}")
    _ok("no model → contract authoring degrades to tests-as-oracle (still verification-driven)")


def section_loop_fixes() -> None:
    print("[B] bounded reflection consumes the counterexample and fixes it")
    calls = {"n": 0, "saw_critique": False}

    def generate(task_spec, critique):
        calls["n"] += 1
        if critique:
            calls["saw_critique"] = True
            return CORRECT       # second attempt uses the critique → correct
        return BUGGY             # first attempt is buggy

    r = vdg_core.reflect_loop("implement absval(x)=|x|", generate, TESTS, k=3)
    if not r["ok"] or r["attempts"] != 2:
        _fail(f"should fix on the 2nd attempt after the counterexample: {r}")
    if not calls["saw_critique"] or not r["critiques"]:
        _fail(f"the loop must feed the counterexample as critique: {r}, {calls}")
    _ok(f"buggy → counterexample → correct in {r['attempts']} attempts (critique consumed)")


def section_loop_bounded() -> None:
    print("[C] bounded: a generator that never fixes it stops at k")
    def always_buggy(task_spec, critique):
        return BUGGY
    r = vdg_core.reflect_loop("implement absval", always_buggy, TESTS, k=3)
    if r["ok"] or r["attempts"] != 3:
        _fail(f"must stop at k=3 without passing (no unbounded loop): {r}")
    _ok("never-fixing generator → stops at k=3 (bounded, no infinite loop)")


def section_needs_oracle() -> None:
    print("[D] needs a verifiable target")
    r = vdg_core.reflect_loop("do something", lambda t, c: CORRECT, tests={})
    if r["ok"] or r["attempts"] != 0:
        _fail(f"no tests should not run: {r}")
    _ok("no tests (no oracle) → does not run")


def main() -> None:
    os.environ.setdefault("VERIFY_REQUIRE_PLAN", "false")
    section_contract()
    section_loop_fixes()
    section_loop_bounded()
    section_needs_oracle()
    print("verification-driven generation (Phase 4) smoke PASSED")


if __name__ == "__main__":
    main()
