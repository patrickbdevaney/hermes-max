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
import signal
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


# ── run registry (Fix 4: universal SSE) ───────────────────────────────────────
# Any hermes run — launched here, in `hm dev`, or bare in a terminal via the shell
# wrapper — drops a descriptor in ~/.hermes-max/runs/. The livelog is a SINGLE global
# JSONL, so a descriptor records the byte OFFSET at start; the events endpoint tails
# the global log from there, scoping the stream to that run. This makes terminal runs
# visible in the browser within a poll interval.
def _registry_dir() -> str:
    d = os.path.expanduser(os.environ.get(
        "HERMES_MAX_STATE_DIR", "~/.hermes-max")) + "/runs"
    os.makedirs(d, exist_ok=True)
    return d


def _descriptor_path(run_id: str) -> str:
    safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
    return os.path.join(_registry_dir(), f"{safe}.json")


def write_descriptor(run: dict[str, Any]) -> None:
    """Persist a run descriptor (no secrets — cwd/prompt/mode/offset only)."""
    try:
        desc = {
            "run_id": run["run_id"],
            "started_at": run.get("started_at")
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(run.get("start_ts", time.time()))),
            "start_ts": float(run.get("start_ts", time.time())),
            "cwd": run.get("cwd"), "prompt": run.get("prompt"),
            "mode": run.get("mode"), "pid": run.get("pid"),
            "start_offset": int(run.get("start_offset", 0)),
            "status": run.get("status", "running"),
            "origin": run.get("origin", "ui"),
        }
        with open(_descriptor_path(run["run_id"]), "w") as f:
            json.dump(desc, f, indent=2)
    except (OSError, ValueError, KeyError):
        pass


def _read_descriptor(run_id: str) -> Optional[dict[str, Any]]:
    try:
        with open(_descriptor_path(run_id)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """All known runs (registry descriptors merged with in-memory launches), most
    recent first, each a JSON-safe public view with a live `active` flag."""
    seen: dict[str, dict[str, Any]] = {}
    # registry descriptors (covers terminal + hm dev + ui runs)
    try:
        for fn in os.listdir(_registry_dir()):
            if not fn.endswith(".json"):
                continue
            d = _read_descriptor(fn[:-5])
            if not d:
                continue
            active = str(d.get("status", "")) == "running" and _pid_alive(d.get("pid"))
            seen[d["run_id"]] = {
                "run_id": d["run_id"], "cwd": d.get("cwd"), "prompt": d.get("prompt"),
                "mode": d.get("mode"), "start_ts": d.get("start_ts"),
                "origin": d.get("origin", "?"),
                "status": "running" if active else (d.get("status") or "exited"),
                "active": active,
            }
    except OSError:
        pass
    # in-memory launches (authoritative for proc liveness)
    with _lock:
        mem = list(_RUNS.values())
    for run in mem:
        pv = public_view(run)
        pv["active"] = pv.get("status") == "running"
        pv["origin"] = run.get("origin", "ui")
        seen[pv["run_id"]] = {**seen.get(pv["run_id"], {}), **pv}
    runs = sorted(seen.values(), key=lambda r: r.get("start_ts") or 0, reverse=True)
    return runs[:limit]


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


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
    if run is not None:
        return run
    if run_id == "live":
        return attach_live()
    # A run discovered via the registry (terminal / hm dev / another browser): build a
    # streamable run dict anchored at its recorded global-log offset. No proc handle —
    # liveness comes from the descriptor's pid/status.
    d = _read_descriptor(run_id)
    if d is not None:
        run = {
            "run_id": run_id, "cwd": d.get("cwd") or os.getcwd(),
            "prompt": d.get("prompt"), "mode": d.get("mode"),
            "start_ts": float(d.get("start_ts", time.time())),
            "start_offset": int(d.get("start_offset", 0)),
            "proc": None, "launched": False, "origin": d.get("origin", "registry"),
        }
        with _lock:
            _RUNS[run_id] = run
        return run
    return None


def _run_log_dir() -> str:
    d = os.path.expanduser(os.environ.get("HERMES_MAX_LOG_DIR",
                                          os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
    os.makedirs(d, exist_ok=True)
    return d


def create_run(cwd: str, prompt: str, mode: str | None,
               continue_session: bool = False,
               run_id: str | None = None,
               approval_gate: bool = False) -> dict[str, Any]:
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
        # Phase 5.3 — optional human-in-the-loop: ask the in-harness conductor to
        # require operator approval before re-injecting guidance. Honoured by the
        # harness when it supports the flag; harmless otherwise.
        if approval_gate:
            env["CONDUCTOR_REQUIRE_APPROVAL"] = "1"
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
        "pid": proc.pid if proc is not None else None, "origin": "ui",
        "status": "running" if proc is not None else "attached",
    }
    with _lock:
        _RUNS[run_id] = run
    _remember_project(cwd)
    write_descriptor(run)   # make UI-launched runs visible in the registry too (Fix 4)
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


# ── Phase 5: control surface (the justified backend write endpoints) ──────────
# The agent is a detached one-shot process per turn, so live control is OS-signal
# based: INTERRUPT aborts the current turn, PAUSE/RESUME suspend/continue it. The
# process is its own session leader (start_new_session=True), so we signal the
# whole process group to catch any children, falling back to the lone pid.
_SIGNALS = {
    "interrupt": signal.SIGTERM,
    "pause": getattr(signal, "SIGSTOP", signal.SIGTERM),
    "resume": getattr(signal, "SIGCONT", signal.SIGTERM),
}


def signal_run(run_id: str, action: str) -> dict[str, Any]:
    """Send a control signal to a run's live process. Returns a JSON-safe result."""
    sig = _SIGNALS.get(action)
    if sig is None:
        return {"ok": False, "error": f"unknown action: {action}"}
    run = get_run(run_id)
    if run is None:
        return {"ok": False, "error": f"unknown run: {run_id}"}
    proc = run.get("proc")
    pid = run.get("pid")
    if pid is None and proc is not None:
        pid = proc.pid
    if pid is None:
        d = _read_descriptor(run_id)
        pid = d.get("pid") if d else None
    if not _pid_alive(pid) and not (proc is not None and proc.poll() is None):
        return {"ok": False, "error": "no live process for this run (it may have finished)"}
    try:
        try:
            os.killpg(os.getpgid(int(pid)), sig)   # whole group (turn + children)
        except (OSError, ProcessLookupError):
            os.kill(int(pid), sig)                  # fallback: the leader only
    except (OSError, ProcessLookupError, ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    if action == "interrupt":
        run["status"] = "exited"
    run["control"] = "paused" if action == "pause" else ("running" if action == "resume" else run.get("control"))
    return {"ok": True, "action": action, "run_id": run_id}


# ── Phase 5.4: editable PLAN.md (the conductor's living plan artifact) ─────────
def _plan_path(cwd: Optional[str]) -> str:
    base = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    return os.path.join(base, "PLAN.md")


def read_plan(cwd: Optional[str]) -> dict[str, Any]:
    path = _plan_path(cwd)
    try:
        with open(path) as f:
            return {"ok": True, "path": path, "exists": True, "content": f.read()}
    except FileNotFoundError:
        return {"ok": True, "path": path, "exists": False, "content": ""}
    except OSError as e:
        return {"ok": False, "path": path, "error": str(e)}


def write_plan(cwd: Optional[str], content: str) -> dict[str, Any]:
    base = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    if not os.path.isdir(base):
        return {"ok": False, "error": f"directory not found: {base}"}
    path = os.path.join(base, "PLAN.md")
    try:
        # atomic-ish write: temp then replace, so a reader never sees a half file
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)
        return {"ok": True, "path": path}
    except OSError as e:
        return {"ok": False, "error": str(e)}
