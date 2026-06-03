#!/usr/bin/env python3
"""Smoke for Phase 6 — compounding regression corpus. Hermetic temp dir.

[A] promote appends a deduped record; the SAME counterexample promotes ONCE
[B] a counterexample carrying test_code writes a regression test guard
[C] promote_from_result handles verify_formal four-value (counterexample/spec_rejected; no-op
    on verified/unknown)
[D] seeded_bug_table rolls up by class + kind
Exit non-zero on first failure."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["REGRESSION_DIR"] = tempfile.mkdtemp(prefix="regr-smoke-")

import regression_core as rg


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def section_dedup() -> None:
    print("[A] promote + dedup")
    r1 = rg.promote("Falsifying example: absdiff(a=0, b=1) -> -1 < 0", task_class="bugfix",
                    target="mod.py", kind="counterexample")
    if not (r1["ok"] and r1["added"]):
        _fail(f"first promote should add: {r1}")
    # same bug, cosmetically different numbers/whitespace → same dedup key → NOT re-added
    r2 = rg.promote("Falsifying example:  absdiff(a=5, b=9) -> -4 < 0", task_class="bugfix",
                    target="mod.py", kind="counterexample")
    if r2["added"]:
        _fail(f"cosmetically-different report of the SAME bug must dedup: {r2}")
    if r1["key"] != r2["key"]:
        _fail(f"dedup keys should match after normalization: {r1['key']} vs {r2['key']}")
    _ok("counterexample promoted once; normalized duplicate deduped")
    # a genuinely different bug → added
    r3 = rg.promote("Failed Checks: arithmetic overflow in settle()", task_class="bugfix",
                    target="ledger.py", kind="counterexample")
    if not r3["added"]:
        _fail("a distinct counterexample should be added")
    _ok("distinct counterexample added")


def section_test_guard() -> None:
    print("[B] test-code → regression test guard written")
    test_code = ("from mod import absdiff\n"
                 "def test_absdiff_nonneg():\n    assert absdiff(0, 1) >= 0\n")
    r = rg.promote("absdiff returns negative", task_class="bugfix", target="mod.py",
                   kind="property", test_code=test_code)
    if not r.get("test_path") or not Path(r["test_path"]).exists():
        _fail(f"a regression test guard should be written: {r}")
    if "test_absdiff_nonneg" not in Path(r["test_path"]).read_text():
        _fail("the written guard should contain the test")
    _ok(f"property counterexample → regression test guard at {Path(r['test_path']).name}")


def section_from_result() -> None:
    print("[C] promote_from_result (four-value)")
    n0 = rg.corpus()["count"]
    # verified/unknown → no-op
    for res in ({"result": "verified"}, {"result": "unknown", "reason": "x"}):
        out = rg.promote_from_result(res, task_class="feature", target="f.py")
        if out["added"]:
            _fail(f"verified/unknown must not promote: {res}")
    # counterexample → promoted
    out = rg.promote_from_result({"result": "counterexample", "trace": "boom at f()",
                                  "stage": "compile", "path": "f.py"}, task_class="feature")
    if not out["added"]:
        _fail("counterexample result should promote")
    # spec_rejected with survivors → promoted
    out2 = rg.promote_from_result({"result": "spec_rejected",
                                   "surviving_examples": ["binop:Add"]}, task_class="feature",
                                  target="g.py")
    if not out2["added"]:
        _fail("spec_rejected with survivors should promote")
    if rg.corpus()["count"] != n0 + 2:
        _fail("exactly the two promotable results should be added")
    _ok("verified/unknown no-op; counterexample + rejected-spec promoted")


def section_table() -> None:
    print("[D] seeded-bug table")
    t = rg.seeded_bug_table()
    if t["total"] < 4 or "bugfix" not in t["by_task_class"]:
        _fail(f"seeded-bug table roll-up wrong: {t}")
    _ok(f"seeded-bug table: total={t['total']}, by_class={t['by_task_class']}")


def main() -> None:
    section_dedup()
    section_test_guard()
    section_from_result()
    section_table()
    print("regression corpus (Phase 6) smoke PASSED")


if __name__ == "__main__":
    main()
