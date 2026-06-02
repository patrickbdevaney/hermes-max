"""The telemetry tap: read the EXISTING signals, emit the typed SSE event model.

Two existing sources, zero new instrumentation:
  * lib.livelog  → ~/.hermes-max/logs/live.jsonl   (the live tool-call stream)
  * lib.inference.ledger.report()                   (the $0.000000 cost ledger)

`stream_events()` tails the live JSONL from a run's start offset and translates
each record into the SSE event types the frontend renders (tool_call / heartbeat /
escalation / gate / narration / phase / plan / cost). It also polls the ledger for
live cost ticks, polls an optional PLAN.md for determinate L0 progress, and emits a
keep-alive heartbeat through idle gaps.

Tier-1 honesty note: the livelog natively carries tool calls, heartbeats, routing/
kill DECISIONS, and cost — so those translate directly. It does NOT carry per-token
streams, file-op diffs, shell output, or OTel spans; those are Tier-3 enrichments
(token streaming + the OTLP→SSE bridge for the L2 tree). The frontend renders only
the events that actually arrive and degrades cleanly, never fabricating file ops.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

# lib/ is importable because the repo root is on sys.path (see lib/__init__.py and
# the way __main__.py inserts the repo root). These are the only deps — stdlib + lib.
from lib.inference import config, ledger, roles  # noqa: E402

from . import otlp  # noqa: E402  - the OTLP→SSE span hub (Tier 3, L2 tree)

# ── cadence knobs (seconds) ──────────────────────────────────────────────────
_TAIL_IDLE = 0.3          # how often to look for new log lines when idle
_COST_POLL = 2.0          # how often to re-read the cost ledger
_PLAN_POLL = 2.0          # how often to re-check PLAN.md
_HEARTBEAT = 15.0         # keep-alive cadence so idle gaps don't trip SSE timeouts


def livelog_path() -> str:
    """Mirror lib.livelog._log_dir() so we tail exactly the file the agent writes."""
    d = os.path.expanduser(os.environ.get(
        "HERMES_MAX_LOG_DIR", os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
    return os.path.join(d, "live.jsonl")


# ── plain-language narration (L0) ─────────────────────────────────────────────
# Map tool/server identifiers → a plain verb. Order matters (first hit wins). This
# is the non-technical story line; it NEVER hides failure or cost (those get their
# own gate/escalation/cost events), it only narrates the happy path in human words.
_VERBS = [
    ("research", "Researching across the web"),
    ("search", "Searching for relevant material"),
    ("deep_research", "Researching across the web"),
    ("fetch", "Reading a source"),
    ("crawl", "Reading web pages"),
    ("rerank", "Ranking the most relevant sources"),
    ("embed", "Indexing the material"),
    ("rag", "Looking through the codebase"),
    ("codebase", "Looking through the codebase"),
    ("repomap", "Mapping the codebase"),
    ("codegraph", "Tracing how the code connects"),
    ("lsp", "Reading the code precisely"),
    ("edit", "Editing files"),
    ("write", "Writing code"),
    ("patch", "Applying a code change"),
    ("verify", "Running the checks"),
    ("test", "Running the tests"),
    ("checkpoint", "Saving a verified checkpoint"),
    ("escalat", "Escalating to a stronger model"),
    ("plan", "Planning the work"),
    ("docs", "Reading the documentation"),
    ("knowledge", "Consulting what it has learned"),
]


def _plain_verb(tool: str | None, server: str | None) -> str:
    hay = f"{tool or ''} {server or ''}".lower()
    for needle, verb in _VERBS:
        if needle in hay:
            return verb
    return f"Working: {tool}" if tool else "Working"


# ── PLAN.md → the L0 progress contract ────────────────────────────────────────
def _parse_plan(cwd: str) -> Optional[dict[str, Any]]:
    """Parse a PLAN.md checklist (`- [ ]` / `- [x]`) in `cwd` into plan items.

    Returns None when there's no PLAN.md — the frontend then shows event-driven
    (step-count) progress instead of a determinate bar."""
    path = os.path.join(cwd, "PLAN.md")
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    items = []
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        low = s.lower()
        if low.startswith("- [x]") or low.startswith("- [ ]"):
            done = low.startswith("- [x]")
            items.append({
                "id": f"p{i}", "text": s[5:].strip(),
                "status": "done" if done else "pending",
            })
    if not items:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return {"items": items, "mtime": mtime}


# ── SSE encoding ──────────────────────────────────────────────────────────────
def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


# ── livelog record → zero-or-more typed events ────────────────────────────────
def _translate(rec: dict[str, Any], run_id: str, calls: dict[str, list[int]],
               seq: list[int]) -> list[tuple[str, dict[str, Any]]]:
    """Translate one live.jsonl record into SSE (event_type, data) tuples.

    `calls` maps a tool name → a stack of open call_ids so a start can be paired
    with its end/fail; `seq` is a one-element mutable counter for unique ids."""
    kind = rec.get("kind")
    out: list[tuple[str, dict[str, Any]]] = []
    base = {"run_id": run_id, "ts": rec.get("ts"), "hms": rec.get("hms")}

    def open_call(tool: str) -> str:
        seq[0] += 1
        cid = f"{tool}#{seq[0]}"
        calls.setdefault(tool, []).append(seq[0])
        return cid

    def close_call(tool: str) -> str:
        stack = calls.get(tool)
        n = stack.pop() if stack else (seq[0] + 1)
        return f"{tool}#{n}"

    if kind == "start":
        tool = rec.get("tool", "tool")
        cid = open_call(tool)
        out.append(("tool_call", {**base, "call_id": cid, "tool": tool,
                                  "server": rec.get("server"),
                                  "input_summary": rec.get("input"),
                                  "est_s": rec.get("est_s"), "status": "running"}))
        out.append(("narration", {**base, "plain_text": _plain_verb(tool, rec.get("server"))}))

    elif kind == "heartbeat":
        tool = rec.get("tool", "tool")
        out.append(("heartbeat", {**base, "tool": tool, "done": rec.get("done"),
                                  "total": rec.get("total"), "eta_s": rec.get("eta_s"),
                                  "elapsed_s": rec.get("elapsed_s"),
                                  "item": rec.get("item"), "note": rec.get("note")}))

    elif kind == "end":
        tool = rec.get("tool", "tool")
        secs = rec.get("secs")
        out.append(("tool_call", {**base, "call_id": close_call(tool), "tool": tool,
                                  "status": "ok",
                                  "latency_ms": int(secs * 1000) if secs else None,
                                  "result_summary": rec.get("returned")}))

    elif kind == "fail":
        tool = rec.get("tool", "tool")
        secs = rec.get("secs")
        out.append(("tool_call", {**base, "call_id": close_call(tool), "tool": tool,
                                  "status": "fail", "reason": rec.get("reason"),
                                  "latency_ms": int(secs * 1000) if secs else None}))
        if rec.get("fallback"):
            out.append(("escalation", {**base, "from_rung": tool,
                                       "to_rung": rec.get("fallback"),
                                       "reason": rec.get("reason") or "tool failed"}))

    elif kind == "slow":
        tool = rec.get("tool", "tool")
        # Long-but-alive: keep the card, mark it slow+healthy (NOT a stall/hang).
        out.append(("tool_call", {**base, "tool": tool, "status": "slow",
                                  "elapsed_s": rec.get("elapsed_s"),
                                  "note": "still working (heartbeating, not stuck)"}))

    elif kind == "decision":
        decision = rec.get("decision", "")
        choice = rec.get("choice", "")
        reason = rec.get("reason", "")
        if rec.get("error"):
            out.append(("gate", {**base, "kind": decision, "status": "fail",
                                 "detail": f"{choice}: {reason}".strip(": ")}))
            out.append(("narration", {**base, "plain_text": f"Hit a snag — {reason}",
                                      "level": "warn"}))
        elif decision in ("route", "fallback", "escalate", "look-ahead"):
            out.append(("escalation", {**base, "from_rung": "", "to_rung": choice,
                                       "reason": reason}))
        else:
            out.append(("narration", {**base, "plain_text": f"{choice} — {reason}".strip(" —")}))

    elif kind == "span":
        # The agent + conductor emit rich progress as livelog spans (the JSONL sink
        # records them even when the console is quiet). Map the meaningful ones into
        # the visual flow; internal routing spans stay out of L1 (they live in L2).
        name = (rec.get("span") or "").lower()
        why = rec.get("reason") or rec.get("note") or rec.get("basis") or ""
        ret = rec.get("returned") or ""
        if name.startswith("conductor."):
            # The conductor plugin's in-harness event feed (pre_llm_call / post_tool_call
            # / triggers / guidance / run_complete). Pass through as a typed `conductor`
            # SSE event the web UI's feed + flow views consume directly.
            ev = name.split(".", 1)[1]
            payload = {**base, "event": ev}
            for k in ("step", "total", "reason", "tier", "model", "tokens", "thinking_tokens",
                      "output_tokens", "cost", "cost_usd", "failures", "result", "file",
                      "turns_on_step", "has_guidance", "calls", "free", "paid",
                      "from_step", "to_step", "done", "final_step", "total_turns"):
                v = rec.get(k)
                if v is not None:
                    payload[k] = v
            out.append(("conductor", payload))
            return out
        if "task_classification" in name:
            if "needs_plan" in why.lower():
                out.append(("phase", {**base, "phase": "plan", "status": "ok"}))
            out.append(("narration", {**base,
                                      "plain_text": f"Classified the task — {why}" if why else "Classified the task"}))
        elif "plan_revision" in name:
            out.append(("narration", {**base, "plain_text": "Revising the plan", "level": "warn"}))
        elif "plan_lint" in name:
            out.append(("narration", {**base, "plain_text": "Checking the plan"}))
        elif "fanout" in name or "draft" in name:
            out.append(("narration", {**base, "plain_text": "Exploring approaches in parallel"}))
        elif any(k in name for k in ("verify", "gate", "test_pass", "tests_pass")):
            ok = rec.get("status", "ok") != "error" and "fail" not in (why + ret).lower()
            out.append(("gate", {**base, "kind": "verify",
                                 "status": "pass" if ok else "fail", "detail": why or ret}))
        elif any(k in name for k in ("checkpoint", "commit", "verified_green")):
            out.append(("checkpoint", {**base, "label": rec.get("span"), "commit": why or ret}))
        elif any(k in name for k in ("file_write", "file_edit", "wrote_file", "edit_file", "write_file", "apply_patch")):
            out.append(("file_op", {**base, "op": "modified", "path": why or name,
                                    "diff_summary": ret}))
        elif any(k in name for k in ("shell", "pytest", "run_tests", "test_run", "command_run")):
            out.append(("shell", {**base, "cmd": why or name, "stream_chunk": ret}))
        # other spans (tier_routing, role_resolved, …) → L2 only, not L1 noise.
    # kind "estimate" stays out of L1 (look-ahead noise).
    return out


def stream_events(run: dict[str, Any]) -> Iterator[str]:
    """The SSE generator for one run. Yields encoded `event:`/`data:` frames until
    the client disconnects (the HTTP handler stops iterating on a write error)."""
    run_id = run["run_id"]
    cwd = run.get("cwd") or os.getcwd()
    offset = int(run.get("start_offset", 0))
    path = livelog_path()
    calls: dict[str, list[int]] = {}
    seq = [0]

    # Opening frames: announce the phase, the plan contract (if any), and a cost
    # baseline so L0 has a number from the first paint.
    yield _sse("phase", {"run_id": run_id, "phase": "connected", "status": "ok"})
    plan = _parse_plan(cwd)
    if plan:
        yield _sse("plan", {"run_id": run_id, "items": plan["items"]})
    elif _full_discipline():
        # Full harness discipline (opt-in): the agent runs one-shot and self-plans
        # internally, so declare hermes-max's own plan→execute→verify contract as the
        # run's visible plan. The execute/verify/checkpoint events that follow are all
        # REAL (observed file diffs + an actual pytest gate + a real git commit).
        yield _sse("phase", {"run_id": run_id, "phase": "plan", "status": "ok"})
        yield _sse("plan", {"run_id": run_id, "items": list(_DISCIPLINE_PLAN)})
    frame, cost_state = _emit_cost_baseline(run_id)
    yield frame

    # L2 (Tier 3): subscribe to the OTLP span hub and backfill this run's spans so a
    # late-connecting client still gets the tree. Spans are time-filtered to the run.
    run_start = float(run.get("start_ts", 0.0))
    span_q = otlp.HUB.subscribe()
    for s in otlp.HUB.recent(run_start)[-400:]:
        yield _sse("span", {"run_id": run_id, "span": s})

    now = time.monotonic
    last_cost = last_plan = last_beat = now()
    plan_mtime = plan["mtime"] if plan else 0.0
    announced_done = False
    # Track the launched process identity so a CONTINUE turn (which replaces the run's
    # proc with a new one in the registry) re-arms the handback: when a fresh proc
    # appears we clear announced_done so its exit fires another "your turn".
    cur_proc = run.get("proc")

    f = None
    try:
        while True:
            # (Re)open the log lazily — it may not exist until the agent writes.
            if f is None:
                try:
                    f = open(path, "r")
                    f.seek(offset)
                except OSError:
                    f = None

            line = f.readline() if f else ""
            if line:
                last_beat = now()
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                for event, data in _translate(rec, run_id, calls, seq):
                    yield _sse(event, data)
                continue

            # ── drain live spans from the OTLP hub (bounded per iteration) ──
            drained = 0
            while drained < 50:
                try:
                    s = span_q.get_nowait()
                except Exception:  # noqa: BLE001 - queue.Empty
                    break
                drained += 1
                if _span_in_run(s, run_start):
                    last_beat = now()
                    yield _sse("span", {"run_id": run_id, "span": s})
            if drained:
                continue

            # ── idle: poll cost, plan, process-exit, heartbeat ──
            time.sleep(_TAIL_IDLE)
            t = now()

            if t - last_cost >= _COST_POLL:
                last_cost = t
                frame, cost_state = _cost_delta(run_id, cost_state)
                if frame:
                    last_beat = t
                    yield frame

            if plan is not None and t - last_plan >= _PLAN_POLL:
                last_plan = t
                fresh = _parse_plan(cwd)
                if fresh and fresh["mtime"] != plan_mtime:
                    plan_mtime = fresh["mtime"]
                    plan = fresh
                    last_beat = t
                    yield _sse("plan", {"run_id": run_id, "items": fresh["items"]})

            # Re-resolve the run from the registry so a continued turn's NEW process is
            # seen by this still-open stream (continue_run swaps the registry entry).
            latest = _current_run(run_id) or run
            latest_proc = latest.get("proc")
            if latest_proc is not None and latest_proc is not cur_proc:
                cur_proc = latest_proc
                announced_done = False  # a new turn started → arm its handback

            if not announced_done and cur_proc is not None and cur_proc.poll() is not None:
                announced_done = True
                last_beat = t
                # Surface the agent's REAL actions before handing back: the files it
                # changed this turn (observed by diff), and — under full discipline —
                # an actual pytest verify gate + a real git checkpoint.
                for ev, data in _post_turn_events(latest):
                    yield _sse(ev, data)
                # The turn finished → hand back to the user (the "your turn" signal).
                yield _sse("narration", {"run_id": run_id,
                                         "plain_text": "Done — your turn.", "level": "info"})
                yield _sse("phase", {"run_id": run_id, "phase": "done", "status": "ok"})

            if t - last_beat >= _HEARTBEAT:
                last_beat = t
                yield _sse("heartbeat", {"run_id": run_id})
    finally:
        otlp.HUB.unsubscribe(span_q)
        if f:
            try:
                f.close()
            except OSError:
                pass


def _span_in_run(span: dict[str, Any], run_start_s: float) -> bool:
    """A span belongs to a run if it ended/started at/after the run anchor (1s skew)."""
    ts_ns = span.get("end_ns") or span.get("start_ns") or 0
    return ts_ns >= (run_start_s - 1.0) * 1e9


def _proc_done(run: dict[str, Any]) -> bool:
    proc = run.get("proc")
    return proc is not None and proc.poll() is not None


# hermes-max's plan→execute→verify contract, surfaced when full discipline is on and
# the project ships no PLAN.md of its own (see stream_events opening).
_DISCIPLINE_PLAN = (
    {"id": "d0", "text": "Plan the work", "status": "pending"},
    {"id": "d1", "text": "Implement the solution", "status": "pending"},
    {"id": "d2", "text": "Write tests", "status": "pending"},
    {"id": "d3", "text": "Verify the result is green", "status": "pending"},
)

# Directories/files never counted as agent file-ops (noise / VCS internals).
_SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", ".venv", "venv",
             ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode"}


def _full_discipline() -> bool:
    """Opt-in (HMX_UI_VERIFY): run the agent under hermes-max's full plan→verify→
    checkpoint discipline. OFF by default so a normal UI run never auto-runs tests
    or commits the user's repo; the PART V actuation test turns it ON."""
    return str(os.environ.get("HMX_UI_VERIFY", "")).strip().lower() in ("1", "true", "yes", "on")


def _changed_files(cwd: Optional[str], since_ts: float, limit: int = 25) -> list[str]:
    """Files under `cwd` modified at/after `since_ts` — an HONEST, read-only diff of
    what the agent actually touched this turn (no fabrication: we report observed
    mtimes, nothing more). Returns repo-relative paths, sorted."""
    if not cwd or not os.path.isdir(cwd):
        return []
    out: list[str] = []
    for dirpath, dirnames, files in os.walk(cwd):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in files:
            if fn.endswith((".pyc", ".pyo")) or fn == "ui_actuation_report.md":
                continue
            full = os.path.join(dirpath, fn)
            try:
                if os.path.getmtime(full) >= since_ts:
                    out.append(os.path.relpath(full, cwd))
            except OSError:
                continue
            if len(out) > 200:
                break
    return sorted(out)[:limit]


def _run_verify(cwd: str) -> tuple[bool, str]:
    """Run the project's pytest as a REAL verify gate. Returns (passed, summary).

    PYTEST_DISABLE_PLUGIN_AUTOLOAD avoids third-party pytest plugins (e.g. a broken
    web3 plugin seen on this box) crashing collection — we want to gate on the
    project's own tests, not a stray plugin's import error."""
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q"], cwd=cwd, env=env,
                           capture_output=True, text=True, timeout=180)
        tail = (r.stdout + "\n" + r.stderr).strip().splitlines()
        return r.returncode == 0, " · ".join(tail[-3:])[-300:] or "no test output"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"verify could not run: {e}"


def _git_checkpoint(cwd: str, run_id: str) -> Optional[str]:
    """Commit the verified-green state to git (a REAL checkpoint) and return the short
    hash. Tolerant of 'nothing to commit' and concurrent index locks — falls back to
    the current HEAD so a checkpoint is reported whenever one exists."""
    if not os.path.isdir(os.path.join(cwd, ".git")):
        return None
    try:
        subprocess.run(["git", "-C", cwd, "add", "-A"],
                       capture_output=True, timeout=15)
        subprocess.run(["git", "-C", cwd, "commit", "--no-verify", "-m",
                        f"hermes-max: verified checkpoint ({run_id})"],
                       capture_output=True, timeout=15)
        h = subprocess.run(["git", "-C", cwd, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return h.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _post_turn_events(run: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The agent's real actions, surfaced at turn end: observed file changes always;
    a pytest verify gate + git checkpoint only under full discipline (opt-in)."""
    out: list[tuple[str, dict[str, Any]]] = []
    base = {"run_id": run["run_id"]}
    cwd = run.get("cwd")
    since = float(run.get("start_ts", 0.0)) - 3.0
    for rel in _changed_files(cwd, since):
        out.append(("file_op", {**base, "op": "modified", "path": rel, "diff_summary": ""}))
    if _full_discipline() and cwd:
        ok, summary = _run_verify(cwd)
        out.append(("shell", {**base, "cmd": "python3 -m pytest -q",
                              "exit_code": 0 if ok else 1, "stream_chunk": summary}))
        out.append(("gate", {**base, "kind": "verify",
                             "status": "pass" if ok else "fail", "detail": summary}))
        if ok:
            commit = _git_checkpoint(cwd, run["run_id"])
            if commit:
                out.append(("checkpoint", {**base, "label": "verified-green", "commit": commit}))
    return out


def _current_run(run_id: str) -> Optional[dict[str, Any]]:
    """Re-fetch the live run dict from the registry (lazy import avoids a cycle:
    runs imports feeds at module load). Used so an open stream sees a continued
    turn's freshly-launched process."""
    try:
        from . import runs
        return runs.get_run(run_id)
    except Exception:  # noqa: BLE001 - never let registry lookup break the stream
        return None


# Cost state is the (total_usd, free_tok, paid_tok) tuple — we tick on a change to
# ANY of them, so free-mode token VOLUME moves live even though USD stays $0.000000.
_CostState = tuple[float, int, int]


def _read_cost() -> _CostState:
    rep = ledger.report("today")
    return float(rep["total_usd"]), int(rep["free_tok"]), int(rep["paid_tok"])


def _cost_frame(run_id: str, state: _CostState, delta_usd: float) -> str:
    total, free_tok, paid_tok = state
    return _sse("cost", {"run_id": run_id, "delta_usd": round(delta_usd, 6),
                         "total_usd": total, "free": paid_tok == 0,
                         "free_tok": free_tok, "paid_tok": paid_tok})


def _emit_cost_baseline(run_id: str) -> tuple[str, _CostState]:
    state = _read_cost()
    return _cost_frame(run_id, state, 0.0), state


def _cost_delta(run_id: str, prev: _CostState) -> tuple[Optional[str], _CostState]:
    state = _read_cost()
    if state == prev:
        return None, state                       # nothing moved — no frame
    return _cost_frame(run_id, state, state[0] - prev[0]), state


# ── REST payloads (assembled from the existing inference modules) ─────────────
_gpu_cache: Optional[bool] = None


def _gpu_present() -> bool:
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache
    present = False
    if shutil.which("nvidia-smi"):
        try:
            present = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=3).returncode == 0
        except (OSError, subprocess.SubprocessError):
            present = False
    _gpu_cache = present
    return present


def _probe_models(base_url: str, timeout: float = 3.0) -> tuple[bool, Optional[int], Optional[str]]:
    """GET {base_url}/models → (ok, latency_ms, first_model_id). Used for driver
    liveness; a real User-Agent so Cloudflare-fronted endpoints don't 403."""
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-max-ui/1.0",
                                               "Accept": "application/json"}, method="GET")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = int((time.monotonic() - t0) * 1000)
            try:
                data = json.loads(r.read(20000).decode("utf-8", "replace"))
                items = data.get("data") or data.get("models") or []
                model = items[0].get("id") if items and isinstance(items[0], dict) else None
            except ValueError:
                model = None
            return True, ms, model
    except (urllib.error.URLError, OSError, ValueError):
        return False, None, None


def driver_status() -> dict[str, Any]:
    """The agent's DRIVER, detected from the active mode's executor — never hardcoded.

    `roles.executor_backend` resolves which backend actually runs the agent loop for
    the active mode (a vLLM rung, or a cloud rung like deepinfra/deepseek/openrouter).
    We classify it by REACHABILITY first, then locality:

      • local   — vLLM-type executor reachable at localhost/127.0.0.1 (GPU here)
      • remote  — vLLM-type executor reachable at another host (GPU on another box)
      • cloud   — a cloud executor (deepinfra/deepseek/...) whose key is present
      • none    — nothing usable (configure a driver)

    So if the user points the driver at a Tailscale vLLM, we say 'remote'; if they
    configure a cloud driver, we render THAT provider + model. The UI shows whatever
    config detects, not a hardcoded backend."""
    mode = roles.active_mode_name()
    b = roles.executor_backend(mode)
    base = b.get("base_url") or ""
    host = urlsplit(base).hostname if base else None
    is_localhost = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")

    if b.get("local"):
        ok, ms, model = (_probe_models(base) if base else (False, None, None))
        if ok:
            return {
                "state": "local" if is_localhost else "remote",
                "provider": b["provider"],
                "host": host, "base_url": base,
                "model": model or b.get("model_id") or None,
                "latency_ms": ms, "reachable": True,
                "label": f"{'Local' if is_localhost else 'Remote'} driver",
            }
        return {"state": "none", "provider": b["provider"], "host": host,
                "base_url": base, "model": None, "reachable": False,
                "label": "Driver unreachable",
                "detail": f"vLLM endpoint {base or '(unset)'} did not respond"}

    # Cloud executor (e.g. deepinfra / deepseek / openrouter as the agent-loop model).
    if b.get("present"):
        return {
            "state": "cloud", "provider": b["provider"], "host": host,
            "base_url": base, "model": b.get("model_id") or None,
            "reachable": None, "label": f"Cloud driver · {b['provider']}",
        }
    return {"state": "none", "provider": b.get("provider"), "host": None,
            "base_url": base, "model": None, "reachable": False,
            "label": "No driver configured",
            "detail": "no reachable vLLM endpoint and no cloud driver key present"}


def status_payload() -> dict[str, Any]:
    """The /api/status view: mode, providers (present-only; reachability is a
    Tier-2 probe), role roster, today's spend, free RPD remaining, GPU presence."""
    mode = roles.active_mode_name()
    present = config.present_providers()
    providers = [{"name": name, "present": name in present, "reachable": None}
                 for name in config.providers()]
    sat = roles.satisfiability(mode)
    roster = [{"role": role, "rung": rung} for role, rung in sat.get("roles", {}).items()]
    rep = ledger.report("today")
    return {
        "mode": mode,
        "providers": providers,
        "roster": roster,
        "today_spend_usd": float(rep["total_usd"]),
        "free_rpd_remaining": rep["free_budget_remaining"],
        "gpu_present": _gpu_present(),
        "driver": driver_status(),
        "warnings": sat.get("warnings", []),
    }


def cost_payload(window: str = "today") -> dict[str, Any]:
    """The /api/cost rollup, straight from the ledger (USD as floats)."""
    return ledger.report(window)


def config_payload() -> dict[str, Any]:
    """Non-secret config only — mode, profile, and the local vLLM endpoint."""
    return {
        "mode": roles.active_mode_name(),
        "profile": os.environ.get("DEPLOY_PROFILE", os.environ.get("HMX_PROFILE", "gpu_local")),
        "vllm_base_url": os.environ.get("VLLM_BASE_URL", ""),
        "verbosity": os.environ.get("HERMES_MAX_VERBOSITY", "verbose"),
    }
