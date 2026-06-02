"""conductor — a Hermes plugin that makes the conductor↔executor split deterministic.

It uses Hermes's own lifecycle-hook primitive (the mechanism Hermes recommends for
exactly this problem): a ``pre_llm_call`` hook RE-INJECTS the execution contract into
EVERY model call (so it survives context compaction — process standards are dropped on
compaction, task objectives survive, so we re-inject fresh each turn), and a
``post_tool_call`` hook detects verify-pass/fail + file writes and fires the conductor
(the cloud synth cascade, in-process) on stuck-detection — no subprocess, no external
watcher. Everything runs inside the Hermes process.

State lives in ``<cwd>/.hermes-conductor/state.json`` (survives the compaction boundary).
The agent's optional ``EXECUTION_STATE.json`` is merged in as a SUPPLEMENT — the hook's
own turn-counting + verify parsing is the ground truth, so the loop never depends on the
model self-reporting correctly.

API: a plugin is ``register(ctx)`` + ``ctx.register_hook(event, callback)`` (the real
Hermes plugin API). pre_llm_call callbacks return ``{"context": "..."}`` which Hermes
injects into the next user message (ephemeral, never persisted). This plugin runs inside
the Hermes venv, so all hermes-max imports (conductor_core / verify_core / livelog) are
LAZY + GUARDED — a missing module degrades gracefully, never breaks the agent loop.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# hermes-max repo root (this file is symlinked into ~/.hermes/plugins/conductor, so
# realpath resolves back to the repo: <repo>/plugins/conductor/__init__.py).
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
for _p in (_REPO, os.path.join(_REPO, "mcp-escalation"), os.path.join(_REPO, "mcp-verify")):
    if _p not in sys.path:
        sys.path.append(_p)

_STUCK_TURNS = int(os.environ.get("CONDUCTOR_STUCK_TURNS", "4"))


# ── state ──────────────────────────────────────────────────────────────────
def _state_path(cwd: str) -> Path:
    return Path(cwd) / ".hermes-conductor" / "state.json"


def _default_state() -> dict[str, Any]:
    return {"current_step": 1, "step_status": "not_started", "turns_on_current_step": 0,
            "total_turns": 0, "verify_consecutive_failures": 0, "last_verify_result": "not run",
            "conductor_requested": False, "done_condition_met": False,
            "conductor_triggered_this_step": False, "pending_guidance": None,
            "escalation_budget": {"standard": 5, "deep": 2}}


def _load_state(cwd: str) -> dict[str, Any]:
    p = _state_path(cwd)
    if p.exists():
        try:
            return {**_default_state(), **json.loads(p.read_text())}
        except (OSError, ValueError):
            pass
    return _default_state()


def _save_state(cwd: str, state: dict[str, Any]) -> None:
    try:
        p = _state_path(cwd)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


# ── PLAN.md parsing (minimal, no external dep) ───────────────────────────────
def _load_plan(cwd: str) -> dict[str, Any]:
    p = Path(cwd) / "PLAN.md"
    if not p.exists():
        return {"steps": []}
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return {"steps": []}
    steps: list[dict[str, Any]] = []
    in_steps = False
    for line in text.splitlines():
        if re.match(r"^\s*##\s*Steps", line, re.I):
            in_steps = True
            continue
        if in_steps and re.match(r"^\s*##\s", line):  # next section ends Steps
            in_steps = False
        m = re.match(r"^\s*-\s*\[[ x]\]\s*(?:step\s*\d+\s*:?\s*)?(.+)", line, re.I) \
            or (re.match(r"^\s*-\s+(.+)", line) if in_steps else None)
        if m:
            desc = m.group(1).strip()
            complexity = "HIGH" if re.search(r"complexity\s*:\s*high", desc, re.I) else "standard"
            desc = re.sub(r"\s*complexity\s*:\s*\w+\s*$", "", desc, flags=re.I).strip()
            steps.append({"description": desc[:200], "complexity": complexity})
    return {"steps": steps}


def _get_step(plan: dict[str, Any], n: int) -> dict[str, Any]:
    steps = plan.get("steps", [])
    if 1 <= n <= len(steps):
        return steps[n - 1]
    return {"description": f"step {n}", "complexity": "standard"}


# ── observability: emit to the global livelog (the existing SSE/cockpit bus) ──
def _emit(event: str, data: dict[str, Any]) -> None:
    try:
        from lib import livelog
        livelog.forward(f"conductor.{event}", data, status="ok")
    except Exception:  # noqa: BLE001 — observability must never break the agent
        pass


# ── the conductor trigger (in-process, no subprocess) ────────────────────────
def _trigger_conductor(cwd: str, state: dict[str, Any], reason: str, step: int, context: str) -> None:
    tier = "deep" if reason == "verify_double_fail" else "standard"
    budget = state.get("escalation_budget") or {"standard": 5, "deep": 2}
    if int(budget.get(tier, 0)) <= 0:
        _emit("budget_exhausted", {"reason": reason, "tier": tier})
        return
    _emit("trigger", {"reason": reason, "step": step, "tier": tier})
    try:
        import conductor_core
        plan = _load_plan(cwd)
        sd = _get_step(plan, step)
        question = (f"An agent is executing step {step} of a plan and is stuck "
                    f"(reason: {reason}). Step: {sd.get('description','?')}. "
                    f"Context: {context[:500]}. Give targeted, specific guidance to unblock "
                    "this step in 3-5 sentences — no re-architecting. If the step is "
                    "impossible as written, say so and give the minimal revision.")
        r = conductor_core.reasoning_escalation(question, context=context, budget=tier, trigger=reason)
    except Exception as e:  # noqa: BLE001
        _emit("error", {"error": str(e)[:160]})
        return
    if r.get("ok") and (r.get("guidance") or r.get("answer")):
        state["pending_guidance"] = r.get("guidance") or r.get("answer")
        budget[tier] = int(budget.get(tier, 0)) - 1
        state["escalation_budget"] = budget
        state["conductor_triggered_this_step"] = True
        _emit("guidance", {"reason": reason, "step": step, "model": r.get("model"),
                           "tier": r.get("tier"), "tokens": r.get("tokens", 0),
                           "cost": r.get("cost_usd", 0.0)})
        print(f"[conductor] ⚡ {reason} on step {step} ({r.get('model')}) — guidance ready, "
              "will inject next turn", flush=True)
    else:
        _emit("error", {"reason": reason, "why": str(r.get("reason", "no guidance"))[:120]})


# ── EXECUTION_STATE.json sync (agent report = supplement, not source of truth) ─
def _sync_execution_state(cwd: str, state: dict[str, Any]) -> None:
    es = Path(cwd) / "EXECUTION_STATE.json"
    if not es.exists():
        return
    try:
        rep = json.loads(es.read_text())
    except (OSError, ValueError):
        return
    for k in ("current_step", "step_status", "last_verify_result", "conductor_requested",
              "conductor_request_reason", "done_condition_met"):
        if k in rep:
            state[k] = rep[k]
    reported = rep.get("current_step", state.get("current_step", 1))
    if isinstance(reported, int) and reported > state.get("current_step", 1):
        state["current_step"] = reported
        state["turns_on_current_step"] = 0
        state["verify_consecutive_failures"] = 0
        state["conductor_triggered_this_step"] = False
        _emit("step_advance", {"step": reported})


def _extract_pytest_summary(text: str) -> str:
    for line in text.splitlines():
        if any(w in line for w in ("passed", "failed", "error")):
            return line.strip()[:120]
    return text[:120]


def _handle_done(cwd: str, state: dict[str, Any]) -> None:
    """Independently verify before accepting the agent's done declaration."""
    passed, summary = False, "verify unavailable"
    try:
        import verify_core
        os.environ.setdefault("VERIFY_REQUIRE_PLAN", "false")  # plan already enforced upstream
        res = verify_core.verify(cwd)
        passed, summary = bool(res.get("passed")), res.get("summary", "")
    except Exception as e:  # noqa: BLE001
        summary = f"verify error: {e}"
    if passed:
        _emit("run_complete", {"turns": state.get("total_turns", 0),
                               "steps": state.get("current_step", 1)})
        print(f"\n[conductor] ✓ done condition met AND verified — {summary}", flush=True)
    else:
        state["done_condition_met"] = False
        state["verify_consecutive_failures"] = state.get("verify_consecutive_failures", 0) + 1
        _emit("done_rejected", {"verify": summary[:120]})
        print(f"[conductor] ✗ agent declared done but verify failed — continuing: {summary}", flush=True)


# ── HOOK CALLBACKS ───────────────────────────────────────────────────────────
def _pre_llm_call(**kw: Any) -> dict[str, Any]:
    """Re-inject the execution contract into the next user message — every turn,
    so it survives context compaction. Returns {"context": ...} (the Hermes schema)."""
    cwd = os.getcwd()
    state = _load_state(cwd)
    plan = _load_plan(cwd)
    step = int(state.get("current_step", 1))
    total = len(plan.get("steps", []))
    sd = _get_step(plan, step)
    turns_on = int(state.get("turns_on_current_step", 0))
    guidance = state.get("pending_guidance")

    lines = [
        f"## Execution State [conductor, turn {state.get('total_turns', 0) + 1}]",
        f"Current step: {step}/{total or '?'} — {sd.get('description', '?')}",
        f"Complexity: {sd.get('complexity', 'standard')}",
        f"Turns on this step: {turns_on}",
        f"Last verify: {state.get('last_verify_result', 'not run')}",
    ]
    if guidance:
        lines += ["", "## Frontier Guidance (from conductor — apply NOW):", str(guidance)]
        state["pending_guidance"] = None
        _emit("guidance_applied", {"step": step})
    if sd.get("complexity") == "HIGH" and turns_on == 0:
        lines.append("⚠ This step is HIGH complexity — call reasoning_escalation before "
                     "writing code, or set conductor_requested=true in EXECUTION_STATE.json.")
    lines += [
        "", "After this turn, update EXECUTION_STATE.json in the cwd:",
        '  {"current_step": N, "step_status": "complete"|"in_progress",',
        '   "last_verify_result": "pytest: …", "conductor_requested": false,',
        '   "done_condition_met": false}',
        "Set done_condition_met=true ONLY when pytest passes and all steps are complete. "
        "Do not replan or re-architect — execute the plan.",
    ]

    state["total_turns"] = state.get("total_turns", 0) + 1
    state["turns_on_current_step"] = turns_on + 1
    _save_state(cwd, state)
    _emit("llm_call", {"step": step, "total": total, "turns_on_step": turns_on + 1,
                       "has_guidance": guidance is not None})
    return {"context": "\n".join(lines)}


def _post_tool_call(tool_name: str = "", args: Optional[dict] = None, result: Any = None, **kw: Any):
    """Detect file writes + verify results; fire the conductor on stuck-detection."""
    cwd = os.getcwd()
    state = _load_state(cwd)
    args = args if isinstance(args, dict) else {}
    step = int(state.get("current_step", 1))
    result_text = str(result)[:2000]

    if tool_name in ("write_file", "edit_file", "str_replace", "patch"):
        _emit("file_write", {"step": step, "file": args.get("path") or args.get("file_path") or "?"})

    if tool_name in ("terminal", "bash", "shell"):
        cmd = str(args.get("command", ""))
        if "pytest" in cmd:
            passed = "passed" in result_text and "failed" not in result_text and "error" not in result_text.lower()
            state["last_verify_result"] = _extract_pytest_summary(result_text)
            if passed:
                state["verify_consecutive_failures"] = 0
                _emit("verify_pass", {"step": step, "result": state["last_verify_result"]})
                print(f"[conductor] ✓ verify passed on step {step}", flush=True)
            else:
                fails = int(state.get("verify_consecutive_failures", 0)) + 1
                state["verify_consecutive_failures"] = fails
                _emit("verify_fail", {"step": step, "failures": fails})
                if fails >= 2 and not state.get("conductor_triggered_this_step"):
                    _trigger_conductor(cwd, state, "verify_double_fail", step, result_text[:500])

    _sync_execution_state(cwd, state)

    turns = int(state.get("turns_on_current_step", 0))
    if turns >= _STUCK_TURNS and not state.get("conductor_triggered_this_step"):
        _trigger_conductor(cwd, state, "no_progress", step,
                           f"step {step} has taken {turns} turns without verify passing")

    if state.get("conductor_requested"):
        _trigger_conductor(cwd, state, state.get("conductor_request_reason", "executor_requested"), step, "")
        state["conductor_requested"] = False

    if state.get("done_condition_met"):
        _handle_done(cwd, state)

    _save_state(cwd, state)
    return None


def _on_session_end(**kw: Any) -> None:
    cwd = os.getcwd()
    state = _load_state(cwd)
    summary = None
    try:
        import conductor_core
        summary = conductor_core.escalation_summary()
    except Exception:  # noqa: BLE001
        pass
    _emit("session_end", {"total_turns": state.get("total_turns", 0),
                          "final_step": state.get("current_step", 1),
                          "done": state.get("done_condition_met", False),
                          "cost": summary})
    if summary:
        print(f"[conductor] session end — {summary['calls']} escalation(s), "
              f"{summary['free']} free / {summary['paid']} paid, ${summary['cost_usd']:.4f}", flush=True)


# ── plugin entry point (the real Hermes API) ─────────────────────────────────
def register(ctx) -> None:
    """Hermes calls this with a PluginContext. Register the lifecycle hooks."""
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)
