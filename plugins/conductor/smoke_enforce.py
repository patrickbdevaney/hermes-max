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


def section_kg() -> None:
    print("[5] KG task-close write (once/run, soft-enforce)")
    recorded = []

    class _KG:
        @staticmethod
        def record_entity(type, name, props=None):
            recorded.append((type, name, props))
            return {"ok": True}

    real = _fake_mod({"kg_core": _KG})
    try:
        state = {"current_step": 3, "total_turns": 9}
        enforce.kg_taskclose_write("/tmp/proj", state, "built feature X; chose approach Y")
        enforce.kg_taskclose_write("/tmp/proj", state, "again")  # once-only
        if len(recorded) != 1 or recorded[0][0] != "task":
            _fail(f"KG task-close must fire exactly once as a task entity: {recorded}")
        if "feature X" not in (recorded[0][2] or {}).get("summary", ""):
            _fail(f"KG write must carry the decision summary: {recorded[0]}")
        _ok("KG task-close fires once/run, records the decision summary")
    finally:
        enforce._mod = real
    real = _fake_mod({})
    try:
        enforce.kg_taskclose_write("/tmp", {}, "x")  # must not raise
        _ok("KG task-close degrades cleanly when kg_core is down")
    finally:
        enforce._mod = real


def section_classify() -> None:
    print("[6] classification in-hook (once/step, soft-enforce)")
    class _CR:
        @staticmethod
        def criticality_classify(text, language="python"):
            return {"critical": "ledger" in text, "dimensions": ["money"] if "ledger" in text else [],
                    "method": "rules"}

    real = _fake_mod({"criticality": _CR})
    try:
        state: dict = {"current_step": 1}
        g = enforce.classify_step(state, "implement the ledger transfer")
        if not g or "CRITICAL" not in g:
            _fail(f"critical step should inject a classification line: {g!r}")
        if enforce.classify_step(state, "implement the ledger transfer") is not None:
            _fail("classify must fire at most once per step")
        _ok("critical step → in-hook classification injected, once per step")
        if enforce.classify_step({"current_step": 2}, "rename a local variable") is not None:
            _fail("non-critical step should inject nothing")
        _ok("non-critical step → no classification noise")
    finally:
        enforce._mod = real


def section_rag() -> None:
    print("[7] RAG before multi-file edit (once/step, soft-enforce)")
    class _RAG:
        @staticmethod
        def search_code(q, k=5):
            return {"results": [{"path": "a/b.py", "snippet": "def helper(): ..."}]}

    real = _fake_mod({"rag_core": _RAG})
    try:
        state: dict = {"current_step": 1}
        g = enforce.rag_before_multifile("/tmp", state, "refactor the parser across all modules")
        if not g or "Prior patterns" not in g:
            _fail(f"multi-file step should inject RAG digest: {g!r}")
        if enforce.rag_before_multifile("/tmp", state, "refactor across modules") is not None:
            _fail("RAG pre-multifile must fire once per step")
        _ok("multi-file step → RAG retrieval injected, once per step")
        if enforce.rag_before_multifile("/tmp", {"current_step": 2}, "fix a typo in one file") is not None:
            _fail("single-file step should not fire RAG")
        _ok("single-file step → RAG not fired")
    finally:
        enforce._mod = real


def main() -> None:
    section_write_gate()
    section_checkpoint()
    section_research()
    section_watchdog()
    section_kg()
    section_classify()
    section_rag()
    print("conductor enforcement (Part B B2+B3) smoke PASSED")


if __name__ == "__main__":
    main()
