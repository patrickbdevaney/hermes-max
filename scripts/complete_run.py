#!/usr/bin/env python3
"""complete_run.py <run_id> — mark a registered run done (Fix 4). Called by the shell
wrapper after `hermes` exits so the UI shows the final state instead of a live run.
Best-effort; an unknown id or unreadable descriptor is a silent no-op."""
from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        return 0
    run_id = "".join(c for c in sys.argv[1] if c.isalnum() or c in "-_")
    reg = os.path.expanduser(os.environ.get("HERMES_MAX_STATE_DIR", "~/.hermes-max")) + "/runs"
    path = os.path.join(reg, run_id + ".json")
    try:
        with open(path) as f:
            d = json.load(f)
        d["status"] = "done"
        d["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(path, "w") as f:
            json.dump(d, f, indent=2)
    except (OSError, ValueError):
        pass

    # End-of-run cost summary (Fix 6): escalation calls / free vs paid / $ spent this
    # run — surfaced in the cockpit and printed. Best-effort.
    try:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(repo, "mcp-escalation"))
        import conductor_core
        s = conductor_core.escalation_summary()
        line = (f"cost summary: {s['calls']} escalation call(s) / {s['free']} free ($0) / "
                f"{s['paid']} paid (${s['cost_usd']:.4f}) / total ${s['cost_usd']:.4f}")
        print("  " + line)
        try:
            sys.path.insert(0, repo)
            from lib import livelog
            livelog.forward("run_cost_summary", {
                "calls": s["calls"], "free": s["free"], "paid": s["paid"],
                "cost_usd": s["cost_usd"]}, status="ok")
        except Exception:
            pass
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
