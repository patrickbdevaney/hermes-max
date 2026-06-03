#!/usr/bin/env python3
"""Smoke for Phase 7 — gated committee planning. Honors cloud/fabric-only fan-out.

[A] OFF by default (critical=False → does not run)
[B] critical + NO parallel backend → does NOT run (never serialized on local)
[C] critical + fabric up → fans N drafts on fabric, scores, returns the best-formed plan
[D] selection prefers the well-formed plan (structural × accuracy weight)
Exit non-zero on first failure."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["ROUTER_OUTCOMES_PATH"] = str(Path(tempfile.mkdtemp(prefix="cmte-")) / "o.jsonl")
os.environ["ROUTER_NOTES_PATH"] = str(Path(tempfile.mkdtemp(prefix="cmte2-")) / "n.jsonl")

import committee_core as cc
import dispatch_core


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


WELL_FORMED = ("Approach: do X.\n## Files\n- a.py: f()\n## Steps\n- [ ] step, complexity: standard\n"
               "DONE_CONDITION: pytest green, 3 tests pass\n")
THIN = "just write some code\n"


def section_off() -> None:
    print("[A] OFF by default")
    r = cc.committee_plan("design the scheduler", critical=False)
    if r["ran"]:
        _fail(f"committee must be OFF unless critical: {r}")
    _ok("critical=False → committee does not run (off by default)")


def section_no_local() -> None:
    print("[B] never serialized on local")
    dispatch_core._fabric = lambda: None
    dispatch_core._cloud_available = lambda: False
    r = cc.committee_plan("design the scheduler", critical=True)
    if r["ran"]:
        _fail(f"no parallel backend → committee must NOT run on local: {r}")
    if "never" not in r["reason"].lower() and "local" not in r["reason"].lower():
        _fail(f"reason should explain the no-local rule: {r}")
    _ok("critical + no parallel backend → does NOT run (never serialized on local)")


def section_runs() -> None:
    print("[C]/[D] critical + fabric up → fan out + accuracy-weighted selection")
    class _Fab:
        @staticmethod
        def map_cheap(prompts, system=None, temperature=0.2, max_tokens=1200):
            # member 0 thin, member 1 well-formed, member 2 thin
            return [THIN, WELL_FORMED, THIN][:len(prompts)]
    dispatch_core._fabric = lambda: _Fab()
    r = cc.committee_plan("design the scheduler", n=3, critical=True)
    if not r["ran"] or r.get("draft_backend") != "fabric" or r.get("drafts") != 3:
        _fail(f"should fan 3 drafts on fabric: {r}")
    _ok(f"critical + fabric up → fanned {r['drafts']} drafts on {r['draft_backend']}")
    if r["selected"] != 1:
        _fail(f"the well-formed plan (member 1) should win: selected={r['selected']}, {r['scores']}")
    _ok(f"accuracy-weighted × well-formedness selected the structured plan (member {r['selected']})")


def main() -> None:
    section_off()
    section_no_local()
    section_runs()
    print("committee planning (Phase 7) smoke PASSED")


if __name__ == "__main__":
    main()
