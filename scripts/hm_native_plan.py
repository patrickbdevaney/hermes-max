#!/usr/bin/env python3
"""hm_native_plan.py "<task>" [task_type] — the conductor planning step for `hm native`.

Authors a SIGNED PLAN.md in the CURRENT directory via the REAL conductor
(mcp-escalation/conductor_core.conductor_plan — the same planner `hm run`/preplan use),
prints it so the operator sees the plan, then returns so the wrapper hands the RAW prompt
to native hermes (hermes self-directs with PLAN.md in context, rather than the strict
"execute PLAN.md, do not replan" contract that `hm run` enforces).

conductor_plan maps the repo (scopemap) and writes PLAN.md itself, and is idempotent
(a validly-signed PLAN.md already in cwd is reused). This is a deliberately LIGHTER front
end than preplan.py (PLAN.md only — no HERMES.md execution wrapper), which suits the
self-directing native run.

Exit 0 ALWAYS (best-effort): if the conductor is unavailable / every synth rung is off,
hermes still launches and self-directs without a plan. `task_type` is advisory (the real
conductor_plan infers structure from the prompt + repo map); it is kept for CLI parity
and only colors the printed header.
"""
from __future__ import annotations

import os
import sys

_REPO = os.environ.get("HERMES_MAX_DIR") or os.path.expanduser("~/hermes-max")
for _p in (_REPO, os.path.join(_REPO, "mcp-scopemap"), os.path.join(_REPO, "mcp-escalation")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def _infer_type(prompt: str) -> str:
    p = (prompt or "").lower()
    if any(w in p for w in ("write", "implement", "create", "fix", "refactor", "test", "add ")):
        return "code"
    if any(w in p for w in ("research", "find", "what is", "explain", "summarize", "compare")):
        return "research"
    return "code"


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: hm_native_plan.py <prompt> [code|research|browser|synthesis]", file=sys.stderr)
        return 1
    prompt = sys.argv[1]
    task_type = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].strip() else _infer_type(prompt)
    cwd = os.getcwd()

    print(f"\n── Conductor planning ({task_type}) ──", flush=True)
    try:
        import conductor_core
        res = conductor_core.conductor_plan(task=prompt, cwd=cwd)
    except Exception as e:  # noqa: BLE001 — never block the native run on a planner failure
        print(f"  [WARN] conductor unavailable ({type(e).__name__}: {e}) — hermes will self-direct.",
              file=sys.stderr)
        print("── Handing off to hermes ──\n", flush=True)
        return 0

    if isinstance(res, dict) and res.get("ok") and res.get("signed"):
        path = res.get("path") or os.path.join(cwd, "PLAN.md")
        how = "reused existing" if not res.get("wrote") else f"authored by {res.get('model')} via conductor"
        print(f"  PLAN.md ({how}): {path}")
        for line in (res.get("plan") or "").rstrip().splitlines():
            print(f"  │ {line}")
    else:
        reason = res.get("reason", "conductor unavailable") if isinstance(res, dict) else "conductor unavailable"
        print(f"  (no signed plan — {reason}; hermes self-directs)")

    print("── Handing off to hermes ──\n", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
