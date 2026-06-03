#!/usr/bin/env python3
"""Smoke for Part A Phase 2 — criticality classifier + Kani routing.

[A] criticality_classify deterministic rules (fully runnable): money/auth/unsafe/termination
    are critical-when-pure; impure high-blast → not provable (contract, not proof);
    concurrent flagged; trivial pure → not critical.
[B] Kani routing + output parsing via a FAKE runner (cargo-kani is absent in CI): SUCCESSFUL
    → verified, FAILED → counterexample, timeout/absent → degrade. And the formal_core Rust
    rung-3 router: critical→Kani, non-critical→no solver, concurrent→not routed to Kani.
Exit non-zero on first failure."""
from __future__ import annotations

import sys

import criticality
import kani_verify


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def section_classifier() -> None:
    print("[A] criticality_classify (deterministic rules)")
    money = "def settle(balance, amount):\n    return balance - amount  # ledger transfer\n"
    c = criticality.criticality_classify(money, "python")
    if not c["critical"] or "money" not in c["dimensions"] or c["method"] != "rules":
        _fail(f"pure ledger code must be critical(money): {c}")
    _ok(f"pure money/ledger → critical {c['dimensions']}")

    auth = "def check(token, secret):\n    import hmac\n    return hmac.compare_digest(token, secret)\n"
    c = criticality.criticality_classify(auth, "python")
    if not c["critical"] or not ({"auth", "crypto"} & set(c["dimensions"])):
        _fail(f"auth/crypto must be critical: {c}")
    _ok(f"auth/crypto → critical {c['dimensions']}")

    rust_unsafe = "pub fn poke(p: *mut i32) { unsafe { *p = 1; } }\n"
    c = criticality.criticality_classify(rust_unsafe, "rust")
    if not c["critical"] or "memory" not in c["dimensions"]:
        _fail(f"unsafe Rust must be critical(memory): {c}")
    _ok(f"unsafe Rust → critical {c['dimensions']}")

    impure = ("import requests\ndef sync_balance(acct):\n"
              "    r = requests.get(acct)\n    return r.json()['balance']\n")
    c = criticality.criticality_classify(impure, "python")
    if c["critical"] or c["pure"]:
        _fail(f"high-blast but IMPURE must not be 'critical' (no proof, use contract): {c}")
    _ok("impure money code → not critical (contract/runtime-monitor, not proof)")

    concurrent = ("import threading\ndef transfer(a, b, amt):\n    with threading.Lock():\n"
                  "        a.bal -= amt\n")
    c = criticality.criticality_classify(concurrent, "python")
    if not c["concurrent"]:
        _fail(f"concurrency must be flagged (Kani can't handle it): {c}")
    _ok("concurrent code flagged (router keeps it out of Kani)")

    trivial = "def greet(name):\n    return 'hi ' + name\n"
    c = criticality.criticality_classify(trivial, "python")
    if c["critical"]:
        _fail(f"trivial string code must not be critical: {c}")
    _ok("trivial pure code → not critical (no heavy rung)")


def section_kani() -> None:
    print("[B] Kani routing + output parsing (fake runner)")
    # parse mapping
    if kani_verify._parse_kani("VERIFICATION:- SUCCESSFUL")["result"] != "verified":
        _fail("SUCCESSFUL must map to verified")
    fail = kani_verify._parse_kani("VERIFICATION:- FAILED\nFailed Checks: arithmetic overflow\nvalue: a=2147483647")
    if fail["result"] != "counterexample" or "overflow" not in fail["trace"]:
        _fail(f"FAILED must map to counterexample with trace: {fail}")
    if kani_verify._parse_kani("Unwinding bound 4 reached; timed out")["result"] != "degrade":
        _fail("unwinding/timeout must map to degrade")
    _ok("Kani output → verified / counterexample(trace) / degrade")

    # default harness covers public fns with kani::any()
    h = kani_verify._default_harness(["add", "clamp"])
    if "kani::proof" not in h or "kani::any()" not in h or "add(" not in h:
        _fail(f"default harness malformed: {h[:120]}")
    _ok("default panic/overflow-freedom harness generated (kani::any inputs)")

    # kani absent in CI → degrade (never a false verified)
    if kani_verify.kani_available():
        _ok("cargo-kani present — live path available [skipped fake]")
    else:
        r = kani_verify.kani_verify("/nonexistent/crate", concurrent=False)
        if r["result"] != "degrade":
            _fail(f"kani absent must degrade, got {r}")
        _ok("cargo-kani absent → degrade (honest, no false verified)")

    # concurrent Rust is NOT routed to Kani
    r = kani_verify.kani_verify("/tmp", concurrent=True)
    if r["result"] != "unknown" or "concurrency" not in r.get("reason", ""):
        _fail(f"concurrent code must not be sent to Kani: {r}")
    _ok("concurrent Rust → not routed to Kani (Loom/Shuttle is Phase 4)")


def section_router() -> None:
    print("[C] formal_core Rust rung-3 router (fake Kani)")
    import formal_core
    import tempfile
    from pathlib import Path
    # critical Rust crate routed to Kani; stub kani_verify to a verified result
    import kani_verify as kv
    real = kv.kani_verify
    kv.kani_verify = lambda path, concurrent=False: {"result": "verified",
                                                     "property": "kani", "method": "kani BMC",
                                                     "harness_fns": ["transfer"]}
    try:
        # a critical (money) rust source
        d = tempfile.mkdtemp(prefix="rung3-")
        src = str(Path(d) / "ledger.rs")
        Path(src).write_text("pub fn transfer(balance: i32, amount: i32) -> i32 { balance - amount }\n")
        out = formal_core._rust_rung3(src)
        if out["result"] != "verified" or out.get("method") != "kani BMC":
            _fail(f"critical Rust should route to Kani→verified: {out}")
        _ok("critical Rust → routed to Kani (verified)")
    finally:
        kv.kani_verify = real

    # non-critical Rust → no solver rung
    d2 = tempfile.mkdtemp(prefix="rung3b-")
    src2 = str(Path(d2) / "util.rs")
    Path(src2).write_text("pub fn greet(n: i32) -> i32 { n + 1 }\n")
    out2 = formal_core._rust_rung3(src2)
    if out2["result"] != "unknown" or "non-critical" not in out2.get("reason", ""):
        _fail(f"non-critical Rust must skip the solver rung: {out2}")
    _ok("non-critical Rust → no solver rung (stays at rungs 0-1)")


def main() -> None:
    section_classifier()
    section_kani()
    section_router()
    print("criticality + Kani (Part A Phase 2) smoke PASSED")


if __name__ == "__main__":
    main()
