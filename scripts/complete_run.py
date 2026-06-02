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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
