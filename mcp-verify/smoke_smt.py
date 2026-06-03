#!/usr/bin/env python3
"""Smoke for Part A Phase 3 — SMT contracts (triple guard) + best-of-N tiebreaker.

The contract GENERATOR is stubbed (no live model); the GUARDS run for real on the Phase-1
mutation/pytest machinery. Validates:
  [A] gating — non-critical module → unknown (rung 4 not warranted)
  [B] consistency guard (G3) — a contract that asserts nothing → spec_rejected
  [C] mutation guard (G1) — a too-weak contract that survives mutation → spec_rejected
  [D] differential guard (G2) — a contract conflicting with a passing agent test → spec_rejected
  [E] verified — a strong, mutation-surviving, agent-consistent contract → verified
  [F] mcp-search formal tiebreaker — formal_rank flips selection between equally-green
      candidates on a CRITICAL module, and is a no-op when formal=False.
Exit non-zero on first failure."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import smt_contracts


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def _mkmod(src, name="mod"):
    d = tempfile.mkdtemp(prefix="smt-corpus-")
    p = Path(d) / f"{name}.py"
    p.write_text(src)
    return str(p)


def _with_contract(contract, fn):
    g = smt_contracts._generate_contract
    # force "model available" so smt_verify proceeds to generation
    import enhanced_verify as ev
    old = ev.VLLM_BASE_URL
    ev.VLLM_BASE_URL = "http://stub"
    smt_contracts._generate_contract = lambda mod, src, ts: contract
    try:
        return fn()
    finally:
        smt_contracts._generate_contract = g
        ev.VLLM_BASE_URL = old


# critical (money), pure module
LEDGER = ("def settle(balance, amount):\n"
          "    '''Return the new balance after debiting amount (never below zero).'''\n"
          "    if amount > balance:\n        return 0\n    return balance - amount\n")

STRONG = (  # asserts a real invariant on output → catches mutations
    "from hypothesis import given, strategies as st, assume\nfrom mod import *\n"
    "@given(st.integers(min_value=0, max_value=10**6), st.integers(min_value=0, max_value=10**6))\n"
    "def test_contract(balance, amount):\n"
    "    out = settle(balance, amount)\n"
    "    assert out >= 0\n"
    "    assert out <= balance\n"
    "    assert (amount <= balance) == (out == balance - amount)\n")

VACUOUS = (  # references the fn but asserts nothing meaningful on output
    "from hypothesis import given, strategies as st\nfrom mod import *\n"
    "@given(st.integers())\ndef test_contract(x):\n    settle(x, 0)\n")

WEAK = (  # asserts only type → survives value mutations
    "from hypothesis import given, strategies as st\nfrom mod import *\n"
    "@given(st.integers(min_value=0, max_value=1000))\n"
    "def test_contract(b):\n    assert isinstance(settle(b, 0), int)\n")


def section_gating() -> None:
    print("[A] gating (critical-few only)")
    trivial = _mkmod("def greet(n):\n    return 'hi'\n")
    r = _with_contract(STRONG, lambda: smt_contracts.smt_verify(trivial))
    if r["result"] != "unknown":
        _fail(f"non-critical module must not run rung-4: {r}")
    _ok("non-critical module → unknown (rung-4 not warranted)")


def section_consistency() -> None:
    print("[B] consistency guard (G3)")
    p = _mkmod(LEDGER)
    r = _with_contract(VACUOUS, lambda: smt_contracts.smt_verify(p))
    if r["result"] != "spec_rejected" or "consistency" not in r["reason"]:
        _fail(f"asserts-nothing contract should hit the consistency guard: {r}")
    _ok("vacuous contract → spec_rejected (consistency)")


def section_mutation() -> None:
    print("[C] mutation guard (G1)")
    p = _mkmod(LEDGER)
    r = _with_contract(WEAK, lambda: smt_contracts.smt_verify(p))
    if r["result"] != "spec_rejected" or "too weak" not in r["reason"]:
        _fail(f"type-only contract should survive mutation → spec_rejected: {r}")
    _ok("weak contract → spec_rejected (mutation cross-check)")


def section_differential() -> None:
    print("[D] differential guard (G2)")
    p = _mkmod(LEDGER)
    # a contract that holds on the code + survives mutation, but a WRONG agent test that
    # contradicts it would be caught; here we give an agent test that ASSERTS a false fact
    # the (correct) contract+code refute — differential must flag the conflict.
    conflicting_tests = ("from mod import settle\n"
                         "def test_wrong():\n    assert settle(10, 20) == -10  # wrong: code returns 0\n")
    r = _with_contract(STRONG, lambda: smt_contracts.smt_verify(p, agent_tests=conflicting_tests))
    if r["result"] != "spec_rejected" or "differential" not in r["reason"]:
        _fail(f"conflicting agent test must trip the differential guard: {r}")
    _ok("contract vs failing agent test → spec_rejected (differential)")


def section_verified() -> None:
    print("[E] verified (all three guards pass)")
    p = _mkmod(LEDGER)
    good_tests = ("from mod import settle\n"
                  "def test_ok():\n    assert settle(10, 3) == 7\n    assert settle(3, 10) == 0\n")
    r = _with_contract(STRONG, lambda: smt_contracts.smt_verify(p, agent_tests=good_tests))
    if r["result"] != "verified":
        _fail(f"strong, consistent, mutation-surviving contract should verify: {r}")
    _ok(f"strong contract → verified (method={r.get('method')}, kill_rate={r.get('kill_rate')})")


def section_tiebreaker() -> None:
    print("[F] mcp-search formal best-of-N tiebreaker")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-search"))
    import search_core as sc
    # two equally-green candidates; stub verify to green-all and verify_formal to prefer B
    sc._verify = lambda path, language: {"reachable": True, "passed": True,
                                         "result": {"summary": "2 passed"}, "error": None}
    sc._verify_formal = lambda path, language: {"result": "verified"} if "B" in path else {"result": "unknown"}
    cands = [{"id": "A", "files": {"mod.py": "def settle(b,a):\n    return b-a\n"}},
             {"id": "B", "files": {"modB.py": "def settle(b,a):\n    return b-a\n"}}]
    # without formal: tie broken by size (A and B equal → first); with formal+critical: B wins
    res_plain = sc.select_from_candidates(cands, language="python")
    res_formal = sc.select_from_candidates(cands, language="python", formal=True, critical=True)
    if not res_formal.get("formal_tiebreaker"):
        _fail(f"formal tiebreaker should be active: {res_formal}")
    if res_formal["selected"] != "B":
        _fail(f"formal-verified candidate B should be selected: {res_formal['selected']}")
    if res_plain.get("formal_tiebreaker"):
        _fail("formal tiebreaker must be OFF by default")
    _ok("critical top-k: formal_rank selects the proven candidate; off by default")


def main() -> None:
    import os
    os.environ.setdefault("VERIFY_REQUIRE_PLAN", "false")
    section_gating()
    section_consistency()
    section_mutation()
    section_differential()
    section_verified()
    section_tiebreaker()
    print("SMT contracts + best-of-N (Part A Phase 3) smoke PASSED")


if __name__ == "__main__":
    main()
