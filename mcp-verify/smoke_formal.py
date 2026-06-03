#!/usr/bin/env python3
"""Smoke + seeded-bug corpus for the formal-verification ladder (Part A Phase 1).

verify_formal's cheap LLM only PROPOSES properties; the pytest oracle + mutation engine
ADJUDICATE. So we validate the ADJUDICATION deterministically by STUBBING the generator
(`_generate_properties`) with canned property sets — strong, weak, vacuous, falsifying —
and asserting the four-value verdict the engine returns. No live model/network required.

Sections:
  [A] compile gate (rung 0) — syntax error → counterexample(stage=compile)
  [B] verified            — correct module + strong, mutation-killing properties
  [C] counterexample      — buggy module + a property that catches the bug
  [D] spec_rejected:vacuity — properties with no assertion on output
  [E] spec_rejected:weak    — properties that pass but survive mutation (too weak)
  [F] unknown (no model)    — sovereign floor: degrade to smoke, never fabricate a pass
  [G] seeded-bug vs pytest-only — the DoD: catch a bug the agent's weak tests miss
  [H] multi-language rung-0 — Rust compile gate runs (cargo present) / degrades
Exit non-zero on first failure (mirrors the other smoke tests)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import formal_core
import verify_core


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def _mkmod(src: str, name: str = "mod") -> str:
    d = tempfile.mkdtemp(prefix="formal-corpus-")
    p = Path(d) / f"{name}.py"
    p.write_text(src)
    return str(p)


def _with_stub_props(props: str, fn):
    """Run fn() with the generator stubbed to return `props` and a model 'available'."""
    g, m = formal_core._generate_properties, formal_core._model_available
    formal_core._generate_properties = lambda *a, **k: props
    formal_core._model_available = lambda: True
    try:
        return fn()
    finally:
        formal_core._generate_properties = g
        formal_core._model_available = m


# ── corpus modules ────────────────────────────────────────────────────────────
ADD = "def add(a, b):\n    return a + b\n"
MAX2_BUGGY = "def max2(a, b):\n    return a  # BUG: ignores b\n"
CLAMP = ("def clamp(x):\n"
         "    if x < 0:\n        return 0\n"
         "    if x > 100:\n        return 100\n"
         "    return x\n")

STRONG_ADD = (
    "from hypothesis import given, strategies as st\n"
    "from mod import *\n"
    "@given(st.integers(), st.integers())\n"
    "def test_commutative(a, b):\n    assert add(a, b) == add(b, a)\n"
    "@given(st.integers(), st.integers())\n"
    "def test_ref(a, b):\n    assert add(a, b) == a + b\n")

CATCHES_MAX_BUG = (
    "from hypothesis import given, strategies as st\n"
    "from mod import *\n"
    "@given(st.integers(), st.integers())\n"
    "def test_is_max(a, b):\n"
    "    m = max2(a, b)\n    assert m >= a and m >= b\n    assert m == a or m == b\n")

VACUOUS = (
    "from mod import *\n"
    "def test_runs():\n    add(1, 2)  # no assertion — vacuous\n")

WEAK_CLAMP = (  # asserts only the TYPE, never the value → survives every value-mutation
    "from hypothesis import given, strategies as st\n"
    "from mod import *\n"
    "@given(st.integers())\n"
    "def test_is_int(x):\n    assert isinstance(clamp(x), int)\n")


def section_compile() -> None:
    print("[A] rung-0 compile gate")
    p = _mkmod("def f(:\n    pass\n")  # syntax error
    r = formal_core.verify_formal(p)
    if r["result"] != "counterexample" or r.get("stage") != "compile":
        _fail(f"syntax error must be a compile counterexample: {r}")
    _ok("syntax error → counterexample(stage=compile)")


def section_verified() -> None:
    print("[B] verified (strong, mutation-killing properties)")
    p = _mkmod(ADD)
    r = _with_stub_props(STRONG_ADD, lambda: formal_core.verify_formal(p))
    if r["result"] != "verified" or "mutation" not in r.get("method", ""):
        _fail(f"strong properties should verify with mutation guard: {r}")
    _ok(f"correct module + strong props → verified (kill_rate={r.get('kill_rate')})")


def section_counterexample() -> None:
    print("[C] counterexample (property catches a real bug)")
    p = _mkmod(MAX2_BUGGY)
    r = _with_stub_props(CATCHES_MAX_BUG, lambda: formal_core.verify_formal(p))
    if r["result"] != "counterexample" or r.get("method") != "hypothesis":
        _fail(f"buggy module should yield a property counterexample: {r}")
    _ok(f"buggy max2 → counterexample (input={r.get('input')})")


def section_vacuity() -> None:
    print("[D] spec_rejected (vacuity guard)")
    p = _mkmod(ADD)
    r = _with_stub_props(VACUOUS, lambda: formal_core.verify_formal(p))
    if r["result"] != "spec_rejected" or "vacuous" not in r["reason"]:
        _fail(f"no-assertion properties must be spec_rejected: {r}")
    _ok(f"vacuous properties → spec_rejected (downgrade={r.get('downgrade')})")


def section_weak() -> None:
    print("[E] spec_rejected (mutation cross-check: too weak)")
    p = _mkmod(CLAMP)
    r = _with_stub_props(WEAK_CLAMP, lambda: formal_core.verify_formal(p))
    if r["result"] != "spec_rejected" or "too weak" not in r["reason"]:
        _fail(f"range-only property should survive mutation → spec_rejected: {r}")
    _ok(f"weak property → spec_rejected ({r['reason'][:60]}…)")


def section_no_model() -> None:
    print("[F] unknown — sovereign floor (no model → smoke, never fabricate a pass)")
    d = tempfile.mkdtemp(prefix="formal-nomodel-")
    (Path(d) / "mod.py").write_text(ADD)
    (Path(d) / "test_mod.py").write_text("from mod import add\ndef test_add():\n    assert add(2,3)==5\n")
    # ensure no model is seen
    mm = formal_core._model_available
    formal_core._model_available = lambda: False
    try:
        r = formal_core.verify_formal(str(Path(d) / "mod.py"))
    finally:
        formal_core._model_available = mm
    if r["result"] != "unknown" or r.get("method") != "smoke":
        _fail(f"no model + green tests → unknown(method=smoke): {r}")
    _ok("no model, green tests → unknown(method=smoke) — no fabricated verified")


def section_dod() -> None:
    print("[G] seeded-bug corpus: beats the pytest-only baseline")
    # A module with a subtle bug + the agent's OWN tests too weak to catch it.
    buggy = "def absdiff(a, b):\n    return a - b  # BUG: not abs()\n"
    weak_tests = "from mod import absdiff\ndef test_basic():\n    assert absdiff(5, 3) == 2\n"
    d = tempfile.mkdtemp(prefix="formal-dod-")
    (Path(d) / "mod.py").write_text(buggy)
    (Path(d) / "test_mod.py").write_text(weak_tests)
    # pytest-only baseline: the weak test passes → bug NOT caught
    base = verify_core.verify(str(Path(d) / "mod.py"), "python")
    if not base.get("passed"):
        _fail(f"baseline pytest should be green on the weak test: {base.get('summary')}")
    # verify_formal with a strong property catches it (a real invariant: absdiff>=0)
    strong = ("from hypothesis import given, strategies as st\nfrom mod import *\n"
              "@given(st.integers(), st.integers())\n"
              "def test_nonneg(a, b):\n    assert absdiff(a, b) >= 0\n")
    r = _with_stub_props(strong, lambda: formal_core.verify_formal(str(Path(d) / "mod.py")))
    if r["result"] != "counterexample":
        _fail(f"verify_formal should CATCH the bug the weak tests miss: {r}")
    _ok("pytest-only is GREEN on the bug; verify_formal returns counterexample → strictly higher catch-rate")


def section_multilang() -> None:
    print("[H] multi-language rung-0 (Rust compile gate)")
    if not shutil.which("cargo"):
        _ok("cargo absent → rung-0 degrades to unknown(no toolchain) [skipped live check]")
        return
    d = tempfile.mkdtemp(prefix="formal-rust-")
    (Path(d) / "Cargo.toml").write_text(
        "[package]\nname = \"smoke\"\nversion = \"0.1.0\"\nedition = \"2021\"\n[lib]\npath = \"lib.rs\"\n")
    # broken Rust → compile counterexample
    (Path(d) / "lib.rs").write_text("pub fn f() -> i32 { let x: i32 = ; x }\n")
    r = formal_core.verify_formal(d, "rust")
    if r["result"] != "counterexample" or r.get("stage") != "compile":
        _fail(f"broken Rust should be a compile counterexample: {r.get('result')}/{r.get('stage')}")
    _ok("broken Rust → counterexample(stage=compile) via cargo build")
    # valid Rust → rungs 0-1 pass, rung-2 honestly unknown
    (Path(d) / "lib.rs").write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    r2 = formal_core.verify_formal(d, "rust")
    if r2["result"] != "unknown":
        _fail(f"valid Rust should be unknown (rung-2 not wired), got {r2['result']}")
    _ok("valid Rust → rungs 0-1 pass; rung-2 unknown (honest, no false verified)")


def main() -> None:
    os.environ.setdefault("VERIFY_REQUIRE_PLAN", "false")
    section_compile()
    section_verified()
    section_counterexample()
    section_vacuity()
    section_weak()
    section_no_model()
    section_dod()
    section_multilang()
    print("formal-verification ladder (Part A Phase 1) smoke PASSED")


if __name__ == "__main__":
    main()
