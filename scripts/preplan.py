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


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: preplan.py <cwd> <prompt>")
        return 0
    cwd = os.path.abspath(os.path.expanduser(sys.argv[1]))
    task = sys.argv[2]
    plan_path = os.path.join(cwd, "PLAN.md")
    if os.path.isfile(plan_path):
        print(f"preplan: PLAN.md already present at {plan_path} — skipping")
        return 0

    repo_map, greenfield = _repo_map(cwd)
    prompt = _plan_prompt(task, repo_map, greenfield)

    if livelog is not None:
        try:
            livelog.tool_start("LLM·plan", server="conductor",
                               inp={"phase": "plan", "greenfield": greenfield})
        except Exception:  # noqa: BLE001
            pass

    t0 = time.time()
    try:
        import conductor_core
        res = conductor_core.run_role("synth", prompt=prompt, max_tokens=4096)
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    secs = time.time() - t0

    if res.get("ok") and res.get("content"):
        try:
            with open(plan_path, "w") as f:
                f.write(res["content"].strip() + "\n")
        except OSError as e:
            print(f"preplan: could not write PLAN.md: {e}")
            return 0
        if livelog is not None:
            try:
                livelog.tool_ok("LLM·plan", secs=secs,
                                ret={"model": res.get("model"),
                                     "provider": res.get("provider"),
                                     "thinking_tok": res.get("thinking_tok", 0),
                                     "wrote": "PLAN.md"})
            except Exception:  # noqa: BLE001
                pass
        print(f"preplan: PLAN.md written by {res.get('provider')}/{res.get('model')} "
              f"in {secs:.1f}s (thinking {res.get('thinking_tok', 0)} tok) → {plan_path}")
        return 0

    # Planner unavailable (all synth rungs failed/gated) — be honest, don't fabricate.
    if livelog is not None:
        try:
            livelog.tool_fail("LLM·plan", reason=str(res.get("reason", "no planner"))[:80], secs=secs)
        except Exception:  # noqa: BLE001
            pass
    print(f"preplan: conductor planner unavailable ({res.get('reason', 'no rung')}). "
          "The executor will plan locally; set CONDUCTOR_MODE=full for the funded V4-Pro rung.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
