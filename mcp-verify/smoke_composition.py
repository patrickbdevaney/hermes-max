#!/usr/bin/env python3
"""Smoke for Part A Phase 4 — composition & protocol level.

Runnable parts execute for real (stdlib + Hypothesis); tool-absent rungs assert honest
degradation:
  [A] runtime monitor — a postcondition violation raises at the edge (assume-guarantee)
  [B] edge_contract_monitor — supplied MONITORS dict is accepted; no-model path is honest
  [C] stateful PBT — a RuleBasedStateMachine finds a cross-state bug; a correct one passes
  [D] concurrency_check — concurrent Rust w/o loom → directive; non-concurrent → not warranted
  [E] protocol_check — distributed-design signal → directive; plain code → not warranted
Exit non-zero on first failure."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import composition


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def section_monitor() -> None:
    print("[A] runtime monitor (assume-guarantee at the edge)")
    # postcondition: result of debit is never negative
    mon = composition.make_monitor(lambda b, a: a >= 0, lambda r, b, a: r >= 0)

    @mon
    def debit(balance, amount):
        return balance - amount  # BUG: can go negative

    if debit(10, 3) != 7:
        _fail("monitor must be transparent on valid calls")
    try:
        debit(5, 10)  # postcondition violation → must raise at the edge
        _fail("postcondition violation should raise AssertionError")
    except AssertionError as e:
        if "postcondition" not in str(e):
            _fail(f"wrong assertion: {e}")
    _ok("runtime monitor fires AssertionError at the edge on a postcondition violation")
    # precondition guard
    try:
        debit(5, -1)
        _fail("precondition violation should raise")
    except AssertionError:
        pass
    _ok("precondition monitor fires on a bad caller (caller must establish callee's pre)")


def section_edge() -> None:
    print("[B] edge_contract_monitor")
    d = tempfile.mkdtemp(prefix="comp-edge-")
    p = Path(d) / "ledger.py"
    p.write_text("def debit(balance, amount):\n    return balance - amount\n")
    supplied = ("from composition import make_monitor\n"
                "MONITORS = {'debit': make_monitor(lambda b,a: a>=0, lambda r,b,a: r>=0)}\n")
    r = composition.edge_contract_monitor(str(p), contracts=supplied)
    if not (r["ok"] and r["monitored"] and "debit" in r["functions"]):
        _fail(f"supplied contracts should be accepted: {r}")
    _ok("supplied edge contracts accepted; public fns identified")
    # no model + no contracts → honest 'not monitored', never a fabricated pass
    r2 = composition.edge_contract_monitor(str(p), contracts=None)
    if r2.get("monitored"):
        _fail("no model + no contracts must not claim monitored")
    _ok("no-model path → honest not-monitored (no fabrication)")


def section_stateful() -> None:
    print("[C] stateful PBT (cross-module state, RuleBasedStateMachine)")
    d = tempfile.mkdtemp(prefix="comp-state-")
    # a buggy counter that can go negative when dec past zero (a STATE bug, not a single call)
    (Path(d) / "counter.py").write_text(
        "class Counter:\n    def __init__(self): self.n = 0\n"
        "    def inc(self): self.n += 1\n"
        "    def dec(self): self.n -= 1  # BUG: no floor at 0\n"
        "    def value(self): return self.n\n")
    buggy_machine = (
        "from hypothesis.stateful import RuleBasedStateMachine, rule, invariant\n"
        "from counter import Counter\n"
        "class M(RuleBasedStateMachine):\n"
        "    def __init__(self):\n        super().__init__(); self.c = Counter()\n"
        "    @rule()\n    def inc(self): self.c.inc()\n"
        "    @rule()\n    def dec(self): self.c.dec()\n"
        "    @invariant()\n    def non_negative(self): assert self.c.value() >= 0\n"
        "TestMachine = M.TestCase\n")
    r = composition.stateful_test(str(Path(d) / "counter.py"), buggy_machine, max_examples=50)
    if r["status"] != "fail":
        _fail(f"stateful machine should find the negative-counter sequence: {r}")
    _ok("stateful PBT finds a violating SEQUENCE (dec past zero) single-call PBT can't")

    # a correct counter (floored) passes
    (Path(d) / "counter.py").write_text(
        "class Counter:\n    def __init__(self): self.n = 0\n"
        "    def inc(self): self.n += 1\n"
        "    def dec(self): self.n = max(0, self.n - 1)\n"
        "    def value(self): return self.n\n")
    r2 = composition.stateful_test(str(Path(d) / "counter.py"), buggy_machine, max_examples=50)
    if r2["status"] != "pass":
        _fail(f"correct counter should pass the state machine: {r2}")
    _ok("correct (floored) counter → stateful PBT passes")


def section_concurrency() -> None:
    print("[D] concurrency_check (route to Loom/Shuttle)")
    d = tempfile.mkdtemp(prefix="comp-conc-")
    (Path(d) / "Cargo.toml").write_text("[package]\nname=\"c\"\nversion=\"0.1.0\"\n")
    (Path(d) / "lib.rs").write_text(
        "use std::sync::atomic::{AtomicI32, Ordering};\n"
        "pub fn bump(a: &AtomicI32) { a.fetch_add(1, Ordering::Relaxed); }\n")
    r = composition.concurrency_check(d)
    if r.get("result") != "unknown" or not r.get("concurrent") or "loom" not in r.get("reason", "").lower():
        _fail(f"concurrent Rust w/o loom should yield a loom/shuttle directive: {r}")
    _ok("concurrent Rust, no loom/shuttle → unknown + directive (Kani can't do concurrency)")
    # non-concurrent → not warranted
    (Path(d) / "lib.rs").write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    r2 = composition.concurrency_check(d)
    if r2.get("concurrent"):
        _fail(f"non-concurrent code must not be flagged: {r2}")
    _ok("non-concurrent code → concurrency model-checking not warranted")


def section_protocol() -> None:
    print("[E] protocol_check (route to TLA+/Alloy)")
    d = tempfile.mkdtemp(prefix="comp-proto-")
    design = Path(d) / "DESIGN.md"
    design.write_text("# Design\nA Raft-style leader election with quorum commit across replicas.\n")
    r = composition.protocol_check(str(design))
    if r.get("result") != "unknown" or not r.get("protocol"):
        _fail(f"distributed-protocol design should route to a model checker: {r}")
    _ok("protocol design → unknown + TLA+/Alloy directive (model-check the design)")
    plain = Path(d) / "util.md"
    plain.write_text("# Notes\nA helper that formats strings.\n")
    r2 = composition.protocol_check(str(plain))
    if r2.get("protocol"):
        _fail(f"plain doc must not be flagged as a protocol: {r2}")
    _ok("non-protocol doc → design model-checking not warranted")


def main() -> None:
    section_monitor()
    section_edge()
    section_stateful()
    section_concurrency()
    section_protocol()
    print("composition & protocol level (Part A Phase 4) smoke PASSED")


if __name__ == "__main__":
    main()
