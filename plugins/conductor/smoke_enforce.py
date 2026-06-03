#!/usr/bin/env python3
"""Smoke test for Part B B2 — conductor lifecycle enforcement (enforce.py).

Drives each enforced capability deterministically. on_file_write uses the REAL
formal_core compile gate (py_compile — fast, no model). checkpoint/research/watchdog
cores are faked via the `_mod` seam so we assert the enforcement WIRING (fires
deterministically, once where required, degrades when a core is down, never wedges)
without needing git/network/a live model. Exit non-zero on first failure."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import enforce


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def _fake_mod(mapping):
    """Patch enforce._mod to return fakes by name (None for 'down')."""
    real = enforce._mod
    enforce._mod = lambda name: mapping.get(name, None)
    return real


def section_write_gate() -> None:
    print("[1] verify_formal write gate (real compile gate)")
    d = tempfile.mkdtemp(prefix="enf-write-")
    broken = Path(d) / "broken.py"
    broken.write_text("def f(:\n    return 1\n" + "# pad " * 20)  # syntax error, > min bytes
    state: dict = {"current_step": 1}
    g = enforce.on_file_write(d, state, str(broken))
    if not g or "FAILED" not in g:
        _fail(f"broken write should queue a compile-fail guidance: {g!r}")
    if state.get("formal_write_fails") != 1:
        _fail(f"write-fail counter should increment: {state}")
    _ok("broken .py write → enforced compile-gate guidance + retry counter")

    # bounded retries: after VERIFY_MAX_RETRIES, stop nagging (return None), never wedge
    for _ in range(enforce.VERIFY_MAX_RETRIES + 2):
        enforce.on_file_write(d, state, str(broken))
    g2 = enforce.on_file_write(d, state, str(broken))
    if g2 is not None:
        _fail("after max retries the write gate must stop nagging (proceed, surfaced once)")
    _ok(f"bounded: after {enforce.VERIFY_MAX_RETRIES} retries the gate surfaces and stops wedging")

    good = Path(d) / "good.py"
    good.write_text("def add(a, b):\n    return a + b\n" + "# pad " * 20)
    state2: dict = {"current_step": 1}
    if enforce.on_file_write(d, state2, str(good)) is not None:
        _fail("a clean compile should not queue guidance")
    _ok("clean .py write → no guidance (compile/lint clean)")

    # non-source file is ignored
    txt = Path(d) / "notes.md"
    txt.write_text("hello " * 50)
    if enforce.on_file_write(d, state2, str(txt)) is not None:
        _fail("non-source writes must be ignored")
    _ok("non-source write ignored")


def section_checkpoint() -> None:
    print("[2] checkpoint after green (fired once/step, degrades)")
    calls = []

    class _CP:
        @staticmethod
        def checkpoint(label, verify, repo_path):
            calls.append((label, verify))
            return {"checkpointed": True, "sha": "abc123"}

    real = _fake_mod({"checkpoint_core": _CP})
    try:
        state = {"current_step": 2}
        enforce.checkpoint_after_green("/tmp", state)
        enforce.checkpoint_after_green("/tmp", state)  # second call same step → no-op
        if len(calls) != 1:
            _fail(f"checkpoint should fire exactly once per green step, got {len(calls)}")
        if state.get("checkpointed_step") != 2 or not calls[0][1]:
            _fail(f"checkpoint must run with verify=True and mark the step: {state}, {calls}")
        _ok("checkpoint fires once per green step, with verify=True (the hard gate)")
    finally:
        enforce._mod = real

    real = _fake_mod({})  # core down
    try:
        enforce.checkpoint_after_green("/tmp", {"current_step": 3})  # must not raise
        _ok("checkpoint degrades cleanly when mcp-checkpoint is down")
    finally:
        enforce._mod = real


def section_research() -> None:
    print("[3] research entry gate (once, novelty-classified)")
    fired = []

    class _RC_syn:
        @staticmethod
        def classify_research_need(q):
            return {"class": "synthesis", "block": False, "signals": []}

        @staticmethod
        def deep_research(q):
            fired.append(q)
            return {"ok": True, "report_md": "## Findings\nUse approach X.", "sources_explored": 7}

    real = _fake_mod({"research_core": _RC_syn})
    try:
        state: dict = {}
        g = enforce.research_entry_gate("/tmp", state, "design a novel distributed consensus protocol")
        if not g or "Entry research" not in g:
            _fail(f"synthesis task should fire entry research + inject digest: {g!r}")
        if len(fired) != 1 or not state.get("research_entry_done"):
            _fail(f"entry research should fire exactly once: fired={fired}, state={state}")
        # second call → once-only guard
        if enforce.research_entry_gate("/tmp", state, "another task") is not None or len(fired) != 1:
            _fail("entry research must fire at most once per task")
        _ok("synthesis task → deep_research fires once, digest injected; second call no-ops")
    finally:
        enforce._mod = real

    class _RC_param:
        @staticmethod
        def classify_research_need(q):
            return {"class": "parametric", "block": True, "signals": ["how does"]}

        @staticmethod
        def deep_research(q):
            raise AssertionError("must not research a parametric task")

    real = _fake_mod({"research_core": _RC_param})
    try:
        if enforce.research_entry_gate("/tmp", {}, "how does quicksort work") is not None:
            _fail("parametric task must NOT fire entry research")
        _ok("parametric task → entry research correctly NOT fired")
    finally:
        enforce._mod = real

    # no task text yet → does not consume the once-flag
    real = _fake_mod({"research_core": _RC_syn})
    try:
        st: dict = {}
        enforce.research_entry_gate("/tmp", st, "")
        if st.get("research_entry_done"):
            _fail("empty task text must not consume the once-only flag")
        _ok("empty task text → flag preserved for a later turn")
    finally:
        enforce._mod = real


def section_watchdog() -> None:
    print("[4] watchdog background tick")
    class _WD:
        @staticmethod
        def check_spiral(text):
            return {"spiral": "LOOP" in text}

    real = _fake_mod({"watchdog_core": _WD})
    try:
        g = enforce.watchdog_tick("/tmp", {"current_step": 1}, reasoning_text="LOOP LOOP LOOP")
        if not g or "looping" not in g.lower():
            _fail(f"spiral should produce a nudge: {g!r}")
        _ok("spiral detected → enforced nudge")
        if enforce.watchdog_tick("/tmp", {"current_step": 1}, reasoning_text="normal progress") is not None:
            _fail("no spiral should produce no nudge")
        _ok("no spiral → background span only, no nudge")
    finally:
        enforce._mod = real
    # down → no-op
    real = _fake_mod({})
    try:
        if enforce.watchdog_tick("/tmp", {}, reasoning_text="x") is not None:
            _fail("watchdog down must be a clean no-op")
        _ok("watchdog degrades cleanly when down")
    finally:
        enforce._mod = real


def main() -> None:
    section_write_gate()
    section_checkpoint()
    section_research()
    section_watchdog()
    print("conductor enforcement (Part B B2) smoke PASSED")


if __name__ == "__main__":
    main()
