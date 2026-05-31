#!/usr/bin/env python3
"""LIVE plan/execute proof — the expensive-planner path against REAL V4-Pro.

The full hermes agent loop (local 35B executor) is not drivable from this sandbox
(no `hermes` on PATH; the running :9105 server is in a separate PID namespace and
cannot be restarted to expose the new tools). So this proves the part that needs
the real cloud: with the funded DEEPINFRA key, generate a Bloom-filter PLAN.md on
V4-Pro (the synth role) through the plan/execute tools, gate it with plan_lint
(revising on V4-Pro if thin), and run the advisory quality_check.

Proves: classify_plan_need -> plan_route(plan/synth/V4-Pro) -> REAL
conductor_synthesize generates a contract-shaped PLAN.md -> plan_lint passes
(after <=PLAN_LINT_MAX_ROUNDS real revisions) -> quality_check advisory. Prints
the conductor cost.

Run with the repo .env sourced:  set -a; . ../.env; set +a; .venv/bin/python live_planexec_proof.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

def _load_env(path: str) -> None:
    """Load KEY=value lines from the repo .env into os.environ (robust to the
    bash-source quirks that left the key empty under `set -a; . .env`)."""
    try:
        for ln in open(path):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            v = v.split(" #")[0].strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)
    except Exception:  # noqa: BLE001 - missing .env -> rely on the live env
        pass


_load_env(str(Path(__file__).resolve().parent.parent / ".env"))

import conductor_core as cc  # noqa: E402
import escalation_core as ec  # noqa: E402
import plan_split as ps  # noqa: E402

TASK = ("Implement a Bloom filter with a configurable false-positive rate in Python, "
        "with a pytest test suite.")

CONTRACT = """You are the PLANNER in a plan/execute split. Write a PLAN.md so complete that a
weaker executor model can implement it WITHOUT making any design decision. Output ONLY the
PLAN.md markdown (no code fences around the whole thing, no implementation code bodies).

Use EXACTLY these section headers:

## TASK
one sentence.

## WORKING_DIRECTORY
{wd}

## FILES
- bloom.py — <one-line purpose>
- test_bloom.py — <one-line purpose>

## FILE SPEC: bloom.py
For every public class/function: the EXACT typed signature (def name(params: types) -> ret),
then PROSE describing the algorithm precisely (the formula for m and k, the bit array, the
hashing, the control flow) — enough that writing the body needs no design decision. State the
edge cases and the EXACT error type/message to raise.

## FILE SPEC: test_bloom.py
List each test by name (def test_...) with the property it checks.

## DONE_CONDITION (Definition of Done)
Concrete and checkable (numbers: test count, FPR tolerance; and verify-green).

## RISKS
What could go wrong and how the executor detects it early.

Remember: specify WHAT and HOW-in-prose, NOT the implementation code itself.
TASK: """ + TASK


def main() -> int:
    """Run the live plan/execute proof and return a process exit code (0 = passed,
    1 = plan still incomplete after the revision rounds, 2 = skipped: no key)."""
    if not os.environ.get("DEEPINFRA_API_KEY", "").strip():
        print("SKIP: DEEPINFRA_API_KEY not in env — source ../.env first")
        return 2

    repo = tempfile.mkdtemp(prefix="bloom-live-")
    print(f"working dir: {repo}")

    # 1. classify + route
    cls = ec.classify_plan_need(TASK)
    assert cls["plan_required"], f"Bloom task must be NEEDS_PLAN: {cls}"
    route = ps.plan_route(task=TASK, phase="auto")
    assert route["phase"] == "plan" and route["tier"] == "synth", route
    print(f"  classify -> NEEDS_PLAN ({cls['reason']})")
    print(f"  route    -> phase={route['phase']} tier={route['tier']} model={route['model_id']}")

    # 2. generate PLAN.md on REAL V4-Pro
    print("  calling V4-Pro (synth) to generate PLAN.md ...")
    r = cc.run_role("synth", prompt=CONTRACT.format(wd=repo), max_tokens=4000)
    if not r.get("ok"):
        print(f"  synth unavailable: {r.get('reason')}")
        return 1
    plan_md = r["content"]
    Path(repo, "PLAN.md").write_text(plan_md)
    print(f"  V4-Pro returned {len(plan_md)} chars (provider={r['provider']} "
          f"model={r['model']} cost=${r['cost_usd']})")

    # 3. plan_lint, with up to PLAN_LINT_MAX_ROUNDS real revisions
    rounds = 0
    while True:
        lint = ps.plan_lint(repo=repo, revision_round=rounds)
        print(f"  plan_lint round {rounds}: complete={lint['complete']} "
              f"missing={lint['missing']}")
        if lint["complete"] or lint["bounded"]:
            break
        rounds += 1
        gaps = "; ".join(lint["missing"])
        fix = cc.run_role("synth", max_tokens=4000, prompt=(
            "Revise this PLAN.md to fix these gaps, output the COMPLETE corrected PLAN.md "
            f"with the same headers. GAPS: {gaps}\n\nCURRENT PLAN.md:\n{Path(repo,'PLAN.md').read_text()}"))
        if fix.get("ok"):
            Path(repo, "PLAN.md").write_text(fix["content"])

    # 4. advisory quality_check on the planned source file IF the executor had written
    #    it; here we just show quality_check runs on a representative file (the plan
    #    itself is markdown, so check this proof script as a stand-in real .py).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-verify"))
        import quality_core

        q = quality_core.quality_check(__file__)
        print(f"  quality_check(self): clean={q['clean']} ({q['summary']})")
    except Exception as e:  # noqa: BLE001
        print(f"  quality_check unavailable here: {e}")

    cost = cc.cost_report()
    print(f"  conductor spend today: ${cost['spend_today_usd']} (cap-safe)")

    ok = lint["complete"]
    print(f"\nLIVE PROOF {'PASSED' if ok else 'INCOMPLETE'}: "
          f"V4-Pro planned a Bloom filter, plan_lint {'passed' if ok else 'still flagged gaps'} "
          f"after {rounds} real revision round(s).")
    print(f"PLAN.md written to {repo}/PLAN.md")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
