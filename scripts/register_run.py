#!/usr/bin/env python3
"""register_run.py <cwd> [hermes args...] — register a hermes run so the web UI can
see it (Fix 4). Drops a descriptor in ~/.hermes-max/runs/ recording the cwd, prompt,
mode, pid, and the CURRENT global-livelog byte offset (so the UI streams this run's
events from there). Prints the run_id on stdout. Holds no secrets. Never fails the
launch — any error just means the run won't appear in the UI."""
from __future__ import annotations

import json
import os
import secrets
import sys
import time


def _log_offset() -> int:
    log_dir = os.path.expanduser(os.environ.get(
        "HERMES_MAX_LOG_DIR", os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
    try:
        return os.path.getsize(os.path.join(log_dir, "live.jsonl"))
    except OSError:
        return 0


def _prompt_from_args(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a in ("-z", "--oneshot") and i + 1 < len(args):
            return args[i + 1]
    nonflag = [a for a in args if not a.startswith("-")]
    return nonflag[0] if nonflag else None


def main() -> int:
    cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    args = sys.argv[2:]
    reg = os.path.expanduser(os.environ.get("HERMES_MAX_STATE_DIR", "~/.hermes-max")) + "/runs"
    run_id = secrets.token_urlsafe(6)
    desc = {
        "run_id": run_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "start_ts": time.time(),
        "cwd": os.path.abspath(os.path.expanduser(cwd)),
        "prompt": _prompt_from_args(args),
        "mode": os.environ.get("CONDUCTOR_MODE") or os.environ.get("INFERENCE_MODE"),
        "pid": os.getppid(),            # the shell running `command hermes` (alive during the run)
        "start_offset": _log_offset(),
        "status": "running",
        "origin": "terminal",
    }
    try:
        os.makedirs(reg, exist_ok=True)
        with open(os.path.join(reg, run_id + ".json"), "w") as f:
            json.dump(desc, f, indent=2)
    except OSError:
        return 0
    print(run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
