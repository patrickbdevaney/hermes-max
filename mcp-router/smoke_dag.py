#!/usr/bin/env python3
"""Smoke for Phase 5 — DAG scheduler. Deterministic; honors the no-local-fan-out rule.

[A] parse_dag + is_multifile gate (single-file → off)
[B] ready_nodes respects dependencies (independent set only)
[C] schedule: off-local → PARALLEL; local → context-isolated but SERIAL (the correction)
[D] merge_conflicts flags overlapping-file nodes
Exit non-zero on first failure."""
from __future__ import annotations

import sys

import dag_core as dg


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


STEPS = [
    {"description": "scaffold module a", "files": ["a.py"], "depends_on": []},
    {"description": "scaffold module b", "files": ["b.py"], "depends_on": []},
    {"description": "wire a+b together", "files": ["a.py", "b.py"], "depends_on": [1, 2]},
]


def section_parse() -> None:
    print("[A] parse_dag + multi-file gate")
    nodes = dg.parse_dag(STEPS)["nodes"]
    if len(nodes) != 3 or nodes[2]["depends_on"] != [1, 2]:
        _fail(f"parse_dag wrong: {nodes}")
    if not dg.is_multifile(nodes):
        _fail("multi-file task should gate DAG on")
    single = dg.parse_dag([{"description": "fix one file", "files": ["x.py"], "depends_on": []}])["nodes"]
    if dg.is_multifile(single):
        _fail("single-file single-node task must NOT be multi-file")
    _ok("parse_dag reads deps+files; multi-file gates on, single-file off")


def section_ready() -> None:
    print("[B] ready_nodes respects dependencies")
    nodes = dg.parse_dag(STEPS)["nodes"]
    r0 = [n["id"] for n in dg.ready_nodes(nodes, done=[])]
    if r0 != [1, 2]:
        _fail(f"initial ready set should be the independent nodes [1,2]: {r0}")
    r1 = [n["id"] for n in dg.ready_nodes(nodes, done=[1])]
    if r1 != [2]:
        _fail(f"node 3 must wait for both deps: {r1}")
    r2 = [n["id"] for n in dg.ready_nodes(nodes, done=[1, 2])]
    if r2 != [3]:
        _fail(f"node 3 ready once 1&2 done: {r2}")
    _ok("ready set: [1,2] → [2] → [3] as deps complete")


def section_schedule() -> None:
    print("[C] schedule: off-local parallel; local serial+context-isolated")
    nodes = dg.parse_dag(STEPS)["nodes"]
    import dispatch_core
    # off-local (fabric present) → PARALLEL
    dispatch_core._fabric = lambda: object()
    s = dg.schedule(nodes, done=[])
    if s["wave"] != [1, 2] or not s["parallel"] or s["backend"] != "fabric":
        _fail(f"off-local independent nodes should run parallel: {s}")
    _ok("fabric up → independent nodes [1,2] run in PARALLEL")
    # local only → SERIAL with context isolation (never parallel on local)
    dispatch_core._fabric = lambda: None
    dispatch_core._cloud_available = lambda: False
    s2 = dg.schedule(nodes, done=[])
    if s2["parallel"] or s2["backend"] != "local-serial" or not s2["context_isolation"]:
        _fail(f"local must be SERIAL + context-isolated, never parallel: {s2}")
    if "context" not in s2["note"].lower() or "serial" not in s2["note"].lower():
        _fail(f"local note must state context-isolation-not-parallelism: {s2['note']}")
    _ok("local only → SERIAL + context-isolated (NOT wall-clock parallel) — the correction")


def section_conflicts() -> None:
    print("[D] merge-conflict detection")
    # two nodes touching the same file, schedulable together
    nodes = dg.parse_dag([
        {"description": "edit shared", "files": ["shared.py"], "depends_on": []},
        {"description": "also edit shared", "files": ["shared.py", "x.py"], "depends_on": []},
    ])["nodes"]
    c = dg.merge_conflicts(nodes)
    if not c or c[0]["files"] != ["shared.py"]:
        _fail(f"overlapping-file nodes should be flagged: {c}")
    _ok("nodes touching the same file → merge-conflict flagged (serialize/reconcile)")
    # disjoint files → no conflict
    nodes2 = dg.parse_dag(STEPS[:2])["nodes"]
    if dg.merge_conflicts(nodes2):
        _fail("disjoint-file nodes must not conflict")
    _ok("disjoint-file nodes → no conflict")


def main() -> None:
    section_parse()
    section_ready()
    section_schedule()
    section_conflicts()
    print("DAG scheduler (Phase 5) smoke PASSED")


if __name__ == "__main__":
    main()
