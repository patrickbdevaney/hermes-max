#!/usr/bin/env python3
"""preplan.py <cwd> <prompt> — the first-turn planner trigger (the thesis, made
deterministic).

Before the executor (hermes-on-vLLM) runs, the CONDUCTOR plans: this maps the repo
(scopemap), then routes a PLAN.md request through the conductor's synth chain — the
strong cloud reasoner (OpenRouter Kimi-K2.6:free first at $0, then the funded
DeepInfra V4-Pro under `full`). It writes PLAN.md (with a DONE_CONDITION) into `cwd`
so the executor transcribes against a gap-free contract instead of designing, and the
verify gate has its DONE_CONDITION. The planner call is emitted to the livelog as an
`LLM·plan` line (with thinking tokens), so the split is visible in the cockpit/UI.

This is why "Kimi/V4-Pro plans, the local model executes" actually fires — rather than
hoping the autonomous executor chooses to call the conductor (it plans natively if
left to itself). Greenfield (empty map) is fine: the planner plans from the prompt.

Exit 0 always (best-effort); prints a one-line summary. CONDUCTOR_MODE selects the
tier ceiling (free → Kimi only; full → Kimi then funded V4-Pro fallback)."""
from __future__ import annotations

import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "mcp-scopemap"))
sys.path.insert(0, os.path.join(_REPO, "mcp-escalation"))

try:
    from lib import livelog
except Exception:  # noqa: BLE001
    livelog = None  # type: ignore


def _repo_map(cwd: str) -> tuple[str, bool]:
    try:
        import scopemap_core
        m = scopemap_core.get_repo_map(cwd)
        return m, "greenfield" in m.lower()
    except Exception:  # noqa: BLE001
        return "(repo map unavailable)", True


def _plan_prompt(task: str, repo_map: str, greenfield: bool) -> str:
    ctx = ("This is a GREENFIELD task — there is no existing code to map."
           if greenfield else
           f"Repository structure (one line per file):\n{repo_map[:8000]}")
    return (
        "You are the PLANNER. Produce a PLAN.md for the task below. Output ONLY the "
        "markdown plan, no preamble. The plan is a contract the executor will follow "
        "literally, so be concrete and gap-free.\n\n"
        f"{ctx}\n\n"
        f"TASK:\n{task}\n\n"
        "The PLAN.md MUST contain:\n"
        "- A one-paragraph approach.\n"
        "- A '## Files' list: each file to create/edit with a one-line FILE SPEC of "
        "what it contains (key functions/classes + signatures).\n"
        "- A '## Steps' ordered list.\n"
        "- A 'DONE_CONDITION:' line stating the exact verifiable gate "
        "(e.g. 'pytest green, >=N tests pass').\n"
    )


_HERMES_MD = """# Execution Contract

You are executing a plan. The conductor plugin injects your current state every turn.
Each turn: read the injected ## Execution State, do the work for the CURRENT step, then
update EXECUTION_STATE.json in this directory:
  {"current_step": N, "step_status": "complete"|"in_progress",
   "last_verify_result": "<pytest summary>", "conductor_requested": false,
   "conductor_request_reason": "", "done_condition_met": false}

Rules:
- done_condition_met=true ONLY when pytest exits 0 AND all steps complete.
- Stuck after 2 turns on a step: set conductor_requested=true with a specific reason.
- HIGH-complexity steps: call reasoning_escalation before writing code.
- A step impossible as written: call review_and_adapt — do not spin.
- Do not replan or re-architect. Execute the plan.
"""


def _write_hermes_md(cwd: str) -> None:
    """Write .hermes.md (the execution contract) — Hermes auto-discovers it in the cwd.
    Don't clobber a user's existing .hermes.md."""
    p = os.path.join(cwd, ".hermes.md")
    if os.path.exists(p):
        return
    try:
        with open(p, "w") as f:
            f.write(_HERMES_MD)
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: preplan.py <cwd> <prompt>")
        return 0
    cwd = os.path.abspath(os.path.expanduser(sys.argv[1]))
    task = sys.argv[2]
    plan_path = os.path.join(cwd, "PLAN.md")
    _write_hermes_md(cwd)   # the execution contract Hermes auto-discovers (.hermes.md)

    if livelog is not None:
        try:
            livelog.tool_start("conductor_plan", server="conductor",
                               inp={"phase": "plan", "task": task[:80]})
        except Exception:  # noqa: BLE001
            pass

    t0 = time.time()
    try:
        import conductor_core
        # The single planner entrypoint: maps the repo, routes through the synth chain
        # (kimi:free → V4-Pro, thinking 8192), writes a SIGNED PLAN.md. Idempotent.
        res = conductor_core.conductor_plan(task, cwd=cwd)
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    secs = time.time() - t0

    if res.get("ok"):
        if livelog is not None:
            try:
                livelog.tool_ok("conductor_plan", secs=secs,
                                ret={"model": res.get("model"), "provider": res.get("provider"),
                                     "thinking_tok": res.get("thinking_tok", 0),
                                     "signed": res.get("signed"), "wrote": res.get("wrote")})
            except Exception:  # noqa: BLE001
                pass
        verb = "reused existing" if not res.get("wrote") else "written by"
        print(f"preplan: PLAN.md {verb} {res.get('provider')}/{res.get('model')} "
              f"in {secs:.1f}s (thinking {res.get('thinking_tok', 0)} tok) → {plan_path}")
        return 0

    # Planner unavailable (every rung 429'd/gated even after retries). NEVER block the
    # run (Fix 3): write a minimal LOCAL-FALLBACK plan and proceed local-only. It is
    # unsigned on purpose — the verify gate WARNS (not blocks) on an unsigned plan, so
    # the executor still runs and can call reasoning_escalation if it gets stuck.
    if livelog is not None:
        try:
            livelog.tool_fail("conductor_plan", reason=str(res.get("reason", "no planner"))[:80], secs=secs)
        except Exception:  # noqa: BLE001
            pass
    minimal = (
        "## Plan authored by: local-fallback (conductor unavailable)\n\n"
        "# PLAN.md\n\n"
        f"## Task\n{task}\n\n"
        "## Approach\nExecute the task directly. Call reasoning_escalation if you hit an "
        "architectural/algorithmic question you can't resolve quickly.\n\n"
        "## DONE_CONDITION\nTask complete and its tests pass. (Verify warns, not blocks, "
        "on this unsigned fallback plan — the conductor was unavailable.)\n")
    try:
        with open(plan_path, "w") as f:
            f.write(minimal)
        print(f"preplan: conductor unavailable ({res.get('reason', 'all rungs 429')}) — "
              f"wrote minimal local-fallback PLAN.md, proceeding local-only → {plan_path}")
    except OSError as e:
        print(f"preplan: conductor unavailable and could not write fallback plan: {e}")
    return 0   # DO NOT block hm run


if __name__ == "__main__":
    raise SystemExit(main())
