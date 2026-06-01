"""Run registry + recent-projects store.

The existing livelog is a SINGLE global JSONL (`~/.hermes-max/logs/live.jsonl`),
not per-run — so a "run" here is a lightweight handle: the byte offset into that
file at the moment the run started, plus its cwd/prompt/mode. The events endpoint
tails the global log FROM that offset, which scopes the stream to "everything the
agent emitted since this run began."

Two ways a run comes to exist:
  * POST /api/run launches the agent (`hermes`) in a chosen cwd and registers a
    run anchored at the current end of the log.
  * the synthetic run id "live" anchors at the current end of the log WITHOUT
    launching anything — so the UI can attach to an already-running agent.

No secrets are stored; recent-projects is just a list of working directories.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Optional

from . import feeds

_lock = threading.Lock()
_RUNS: dict[str, dict[str, Any]] = {}


def _state_dir() -> str:
    d = os.path.expanduser(os.environ.get(
        "HERMES_MAX_STATE_DIR", "~/.hermes-max")) + "/ui"
    os.makedirs(d, exist_ok=True)
    return d


def _recent_path() -> str:
    return os.path.join(_state_dir(), "recent_projects.json")


def recent_projects(limit: int = 10) -> list[dict[str, Any]]:
    """Most-recent-first list of working dirs the UI has launched runs in."""
    try:
        with open(_recent_path()) as f:
            items = json.load(f)
    except (OSError, ValueError):
        items = []
    # The repo root is always a sensible default to offer, even before any run.
    if not items:
        return [{"path": os.getcwd(), "last_used": None}]
    return items[:limit]


def _remember_project(cwd: str) -> None:
    items = [p for p in recent_projects(limit=50) if p.get("path") != cwd]
    items.insert(0, {"path": cwd, "last_used": time.time()})
    try:
        with open(_recent_path(), "w") as f:
            json.dump(items[:50], f, indent=2)
    except OSError:
        pass


def _current_offset() -> int:
    """End-of-file byte offset of the live log right now (0 if it doesn't exist)."""
    try:
        return os.path.getsize(feeds.livelog_path())
    except OSError:
        return 0


def attach_live() -> dict[str, Any]:
    """Register (idempotently) the synthetic 'live' run anchored at log end now."""
    with _lock:
        run = {
            "run_id": "live", "cwd": os.getcwd(), "prompt": None, "mode": None,
            "start_ts": time.time(), "start_offset": _current_offset(),
            "proc": None, "launched": False,
        }
        _RUNS["live"] = run
        return run


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        run = _RUNS.get(run_id)
    if run is None and run_id == "live":
        return attach_live()
    return run


def _run_log_dir() -> str:
    d = os.path.expanduser(os.environ.get("HERMES_MAX_LOG_DIR",
                                          os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
    os.makedirs(d, exist_ok=True)
    return d


def create_run(cwd: str, prompt: str, mode: str | None,
               continue_session: bool = False,
               run_id: str | None = None) -> dict[str, Any]:
    """Anchor a run at the current log end and launch the agent in `cwd`.

    The agent is invoked one-shot as `hermes --yolo -z <prompt>` (the documented
    one-shot form; `--continue` is added for turn 2+ of a conversation so the agent
    resumes its session). Launch is best-effort: if `hermes` isn't on PATH the run
    still registers (so the UI streams the global log), and `launch_error` explains
    why nothing was spawned. Returns a JSON-safe view (no Popen handle)."""
    cwd = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    run_id = run_id or secrets.token_urlsafe(9)
    offset = _current_offset()
    proc: Optional[subprocess.Popen] = None
    launch_error: Optional[str] = None

    if not os.path.isdir(cwd):
        launch_error = f"cwd does not exist: {cwd}"
    elif shutil.which("hermes") is None:
        launch_error = ("'hermes' is not on PATH — the run is registered and will "
                        "stream the live log, but no agent was launched. Start the "
                        "agent yourself, or use the 'live' run to attach.")
    else:
        env = dict(os.environ)
        if mode:
            env["CONDUCTOR_MODE"] = mode
        # Inject any keychain-held provider keys the agent can't see otherwise
        # (no-op for the .env backend — the agent reads .env itself).
        try:
            from . import config_api, secrets_store
            env.update(secrets_store.launch_env(config_api.secret_env_vars()))
        except Exception:  # noqa: BLE001 - never block a launch on secret plumbing
            pass
        try:
            # Detached from this server's stdio; the agent's own livelog is the feed.
            # `-z PROMPT` is hermes' one-shot form (a bare positional is parsed as a
            # subcommand and errors); `--yolo` auto-accepts so a headless turn never
            # blocks on an approval prompt. A turn's stderr is captured to a log so a
            # launch failure (e.g. unconfigured provider) is diagnosable.
            cmd = ["hermes", "--yolo", "-z", prompt]
            if continue_session:
                cmd.insert(1, "--continue")  # resume the conversation for turn 2+
            errlog = open(os.path.join(_run_log_dir(), f"turn-{run_id}.log"), "wb")
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdin=subprocess.DEVNULL,
                stdout=errlog, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as e:  # noqa: BLE001
            launch_error = f"failed to launch hermes: {e}"

    run = {
        "run_id": run_id, "cwd": cwd, "prompt": prompt, "mode": mode,
        "start_ts": time.time(), "start_offset": offset,
        "proc": proc, "launched": proc is not None, "launch_error": launch_error,
    }
    with _lock:
        _RUNS[run_id] = run
    _remember_project(cwd)
    return public_view(run)


def continue_run(run_id: str, prompt: str) -> dict[str, Any]:
    """Turn 2+ of a conversation: relaunch the agent in the SAME cwd with
    `--continue` (resumes its session), re-anchoring the run so the open SSE stream
    surfaces the new turn. Returns an error view if the run_id is unknown."""
    prev = get_run(run_id)
    if prev is None or run_id == "live":
        return {"ok": False, "error": f"unknown run to continue: {run_id}"}
    return create_run(prev["cwd"], prompt, prev.get("mode"),
                      continue_session=True, run_id=run_id)


def public_view(run: dict[str, Any]) -> dict[str, Any]:
    """A JSON-serialisable subset (drops the Popen handle, adds live status)."""
    proc = run.get("proc")
    status = "attached"
    if proc is not None:
        status = "running" if proc.poll() is None else "exited"
    return {
        "run_id": run["run_id"], "cwd": run["cwd"], "prompt": run.get("prompt"),
        "mode": run.get("mode"), "start_ts": run["start_ts"],
        "launched": run.get("launched", False), "status": status,
        "launch_error": run.get("launch_error"),
    }


def proc_finished(run: dict[str, Any]) -> bool:
    """True once a launched agent process has exited (always False for attach)."""
    proc = run.get("proc")
    return proc is not None and proc.poll() is not None
