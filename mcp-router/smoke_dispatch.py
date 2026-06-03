#!/usr/bin/env python3
"""Smoke for Phase 3 — parallelism dispatcher + execution-verified best-of-N.

[A] target_for honors the asymmetry: fabric→cloud→local; refuses blind local fan-out;
    serial-bounded on verify-fail only
[B] fanout routes to fabric (parallel) and refuses to serialize N on local
[C] best_of_n is OFF by default; gated on verify-fail/critical; needs an execution oracle;
    selects by execution (stubbed search) and logs the outcome
Exit non-zero on first failure."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_d = tempfile.mkdtemp(prefix="dispatch-smoke-")
os.environ["ROUTER_OUTCOMES_PATH"] = str(Path(_d) / "outcomes.jsonl")
os.environ["ROUTER_BANDIT_PATH"] = str(Path(_d) / "bandit.json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-costprofiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-search"))

import dispatch_core as dc


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def section_target() -> None:
    print("[A] target_for honors local=serial / fabric,cloud=parallel")
    # force each backend state via the private hooks
    dc._fabric = lambda: object()  # fabric present
    t = dc.target_for(5)
    if t["backend"] != "fabric" or not t["parallel"] or t["n"] != 5:
        _fail(f"fabric should be the preferred parallel target: {t}")
    _ok("fabric up → fan-out lands on fabric (parallel)")

    dc._fabric = lambda: None
    dc._cloud_available = lambda: True
    t = dc.target_for(5)
    if t["backend"] != "cloud-deepseek" or not t["parallel"]:
        _fail(f"fabric down → cloud parallel: {t}")
    _ok("fabric down → cloud-deepseek (parallel)")

    dc._cloud_available = lambda: False
    t_no_fail = dc.target_for(5, verify_failed=False)
    if t_no_fail["backend"] != "local-serial" or t_no_fail["parallel"] or t_no_fail["n"] != 1:
        _fail(f"local + no verify-fail must be a SINGLE attempt (no fan-out): {t_no_fail}")
    _ok("local only + no verify-fail → ONE attempt (refuses blind fan-out)")

    t_fail = dc.target_for(5, verify_failed=True)
    if t_fail["backend"] != "local-serial" or t_fail["parallel"] or t_fail["n"] != dc.LOCAL_BEST_OF_N_MAX:
        _fail(f"local verify-fail → serial bounded to {dc.LOCAL_BEST_OF_N_MAX}: {t_fail}")
    _ok(f"local + verify-fail → serial best-of-N bounded to {dc.LOCAL_BEST_OF_N_MAX}")


def section_fanout() -> None:
    print("[B] fanout routes parallel; refuses to serialize N on local")
    class _Fab:
        @staticmethod
        def map_cheap(prompts, system=None, temperature=0.2, max_tokens=1200):
            return [f"sol{i}" for i in range(len(prompts))]
    dc._fabric = lambda: _Fab()
    r = dc.fanout(["p1", "p2", "p3"])
    if r["backend"] != "fabric" or not r["parallel"] or len(r["results"]) != 3:
        _fail(f"fanout should run parallel on fabric: {r}")
    _ok("fanout → fabric parallel (3 results)")

    dc._fabric = lambda: None
    dc._cloud_available = lambda: False
    r2 = dc.fanout(["p1", "p2"])
    if r2["parallel"] or any(r2["results"]):
        _fail(f"no parallel backend must NOT serialize on local: {r2}")
    _ok("no parallel backend → does NOT fan out on local (single-attempt signal)")


def section_best_of_n() -> None:
    print("[C] execution-verified best-of-N (gated, selects by execution)")
    # OFF by default
    r0 = dc.best_of_n("write add()", tests={"t.py": "..."}, verify_failed=False, critical=False)
    if r0["ran"]:
        _fail(f"best-of-N must be OFF by default: {r0}")
    _ok("best-of-N OFF by default (not gated on)")

    # needs an execution oracle
    r1 = dc.best_of_n("write add()", tests={}, verify_failed=True)
    if r1["ran"]:
        _fail(f"best-of-N without tests must not run: {r1}")
    _ok("no tests (no execution oracle) → does not run (route to synthesis)")

    # gated on verify-fail: fabric drafts, stubbed search selects by execution
    class _Fab:
        @staticmethod
        def map_cheap(prompts, system=None, temperature=0.2, max_tokens=1200):
            return ["```python\ndef add(a,b):\n    return a+b\n```" for _ in prompts]
    dc._fabric = lambda: _Fab()
    import search_core
    search_core.select_from_candidates = lambda candidates, **k: {
        "selected": candidates[0]["id"], "green_count": len(candidates),
        "reason": "stub: first green", "selected_files": candidates[0]["files"]}
    r2 = dc.best_of_n("write add(a,b)", tests={"test_add.py": "..."}, verify_failed=True)
    if not r2["ran"] or not r2.get("selected") or r2.get("draft_backend") != "fabric":
        _fail(f"gated best-of-N should draft on fabric + select by execution: {r2}")
    _ok(f"verify-fail → drafts on {r2['draft_backend']}, selected '{r2['selected']}' by execution")
    # outcome logged
    if not Path(os.environ["ROUTER_OUTCOMES_PATH"]).exists():
        _fail("best-of-N should log an outcome to close the loop")
    _ok("best-of-N logged the outcome (loop closed)")


def main() -> None:
    section_target()
    section_fanout()
    section_best_of_n()
    print("parallelism dispatcher + best-of-N (Phase 3) smoke PASSED")


if __name__ == "__main__":
    main()
