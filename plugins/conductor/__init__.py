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

# Part B B2 — lifecycle ENFORCEMENT of the high-value MCPs (verify/checkpoint/research/
# watchdog), fired deterministically from the hooks below. Lazy + guarded: if the module
# or any target MCP is absent the enforcement degrades to a no-op, never breaking the loop.
try:
    from . import enforce as _enforce
except Exception:  # noqa: BLE001
    try:
        import enforce as _enforce  # when loaded as a top-level module
    except Exception:  # noqa: BLE001
        _enforce = None  # type: ignore

# Phase 2 — token fan-out. The hermes AIAgent exposes stream_delta / thinking /
# reasoning callbacks; we install them on pre_llm_call and remove them on
# post_llm_call, writing each delta to the livelog as a gen.* span. The Phase 1
# Rust stream + (once wired) feeds.py carry gen.* to both surfaces.
_CTX = None          # captured at register() so hooks can reach the live agent
_tok_warned = False  # warn once if the agent can't be found


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
            # P5 DAG annotations (optional, anywhere in the line): depends_on: [1, 2] · files: a.py, b.py
            dep_m = re.search(r"depends_on\s*:\s*\[([0-9,\s]*)\]", desc, re.I)
            depends_on = [int(x) for x in re.findall(r"\d+", dep_m.group(1))] if dep_m else []
            file_m = re.search(r"files\s*:\s*([^\]]+?)(?:$|;|\s*depends_on)", desc, re.I)
            files = [f.strip() for f in re.split(r"[,\s]+", file_m.group(1)) if f.strip().endswith(
                (".py", ".rs", ".ts", ".js", ".go"))] if file_m else []
            desc = re.sub(r"\s*(complexity\s*:\s*\w+|depends_on\s*:\s*\[[0-9,\s]*\]|files\s*:[^;]*)",
                          "", desc, flags=re.I)
            desc = re.sub(r"[,\s]+$", "", desc).strip()  # drop trailing separators left behind
            steps.append({"description": desc[:200], "complexity": complexity,
                          "depends_on": depends_on, "files": files})
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


# ── mandatory turn-1 conductor planning (in-process, no subprocess) ──────────
_ENV_LOADED = False


def _load_repo_env(repo: Path) -> None:
    """Best-effort: load the repo .env provider keys into os.environ if absent, so the
    conductor's synth chain has its keys even under bare `hermes` (which, unlike hm run /
    hm native, does not source .env). Only sets keys not already present; expands ${VAR}
    against what's loaded so far; runs once per process."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    envf = repo / ".env"
    if not envf.is_file():
        return
    try:
        for line in envf.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k and k not in os.environ:
                os.environ[k] = os.path.expandvars(v.strip().strip('"').strip("'"))
    except OSError:
        pass


def _is_trivial_lookup(task: str) -> bool:
    """A single-line factual lookup doesn't warrant a plan (mirrors workflow-plan's only
    exception). Conservative — only the clearest Q&A forms, so real tasks still plan."""
    t = (task or "").strip().lower()
    if not t:
        return True
    if len(t) <= 80 and t.endswith("?"):
        return True
    return t.startswith(("what is", "what's", "what are", "who is", "who's", "show me",
                         "which file", "where is", "where's", "explain ", "define ",
                         "list the", "how many", "how do i"))


def _ensure_conductor_plan(cwd: str, task: str) -> Optional[str]:
    """Author a signed PLAN.md in cwd via the conductor (in-process; conductor_plan writes
    the file itself). Returns a short context note on success, else None. Never raises."""
    repo = Path(__file__).resolve().parents[2]  # <repo>/plugins/conductor/__init__.py
    _load_repo_env(repo)  # bare `hermes` doesn't source .env; the synth chain needs the keys
    try:
        import conductor_core
    except Exception:  # noqa: BLE001 — not on hermes' path yet; add the repo MCP dirs + retry
        try:
            for sub in ("mcp-escalation", "mcp-scopemap"):  # planner + its repo-map dep
                p = str(repo / sub)
                if (repo / sub).is_dir() and p not in sys.path:
                    sys.path.insert(0, p)
            import conductor_core
        except Exception as e:  # noqa: BLE001 — planner truly unavailable → model self-directs
            _emit("plan_unavailable", {"why": f"import: {str(e)[:120]}"})
            return None
    _emit("plan_start", {"task": task[:160]})
    try:
        r = conductor_core.conductor_plan(task=task, cwd=cwd)
    except Exception as e:  # noqa: BLE001 — conductor down → self-direct, never block hermes
        _emit("plan_error", {"error": str(e)[:160]})
        return None
    if isinstance(r, dict) and r.get("ok") and r.get("signed"):
        _emit("plan_written", {"model": r.get("model"), "wrote": bool(r.get("wrote"))})
        return (f"## Conductor plan (enforced — read FIRST)\n"
                f"A signed PLAN.md was written to {cwd}/PLAN.md by the conductor "
                f"({r.get('model', '?')}). Read it now and execute against its Files / Steps / "
                "DONE_CONDITION — do NOT replan or re-architect; the design is decided.")
    _emit("plan_unavailable", {"reason": (r.get("reason") if isinstance(r, dict) else "no result")})
    return None


def _maybe_plan_turn1(cwd: str, plan: dict[str, Any], kw: dict[str, Any]) -> Optional[str]:
    """Fire conductor planning ONLY on turn 1 of a new task with no PLAN.md and a
    non-trivial request. Returns the context note to inject, or None to skip cleanly.
    Kill-switch: CONDUCTOR_TURN1_PLAN=0."""
    if os.environ.get("CONDUCTOR_TURN1_PLAN", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    if not kw.get("is_first_turn"):                            # continuation / mid-task → skip
        return None
    if plan.get("steps") or (Path(cwd) / "PLAN.md").exists():  # a plan already exists → skip
        return None
    task = (kw.get("user_message") or "").strip()
    if not task or _is_trivial_lookup(task):                  # nothing to plan / trivial → skip
        return None
    return _ensure_conductor_plan(cwd, task)


# ── the conductor trigger (in-process, no subprocess) ────────────────────────
def _trigger_conductor(cwd: str, state: dict[str, Any], reason: str, step: int, context: str) -> None:
    tier = "deep" if reason == "verify_double_fail" else "standard"
    budget = state.get("escalation_budget") or {"standard": 5, "deep": 2}
    if int(budget.get(tier, 0)) <= 0:
        _emit("budget_exhausted", {"reason": reason, "tier": tier})
        return
    # SG2 — the conductor escalation IS a cloud call; if the run spend cap blocked cloud, skip
    # it (the run continues locally — never aborted, fabric/local never blocked).
    if state.get("cloud_blocked"):
        _emit("cloud_blocked", {"reason": reason, "step": step, "why": "run spend cap reached"})
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
        # SG1 — record the cloud escalation cost into the SQLite ledger so the cap + ratio
        # alert see real cloud spend (unless it was free-tier, cost 0).
        if _enforce is not None:
            try:
                _enforce.record_cost("cloud-deepseek", r.get("provider", "cloud"), r.get("model", ""),
                                     0, int(r.get("tokens", 0) or 0), float(r.get("cost_usd", 0.0) or 0.0), 0)
            except Exception:  # noqa: BLE001
                pass
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
        state["formal_write_fails"] = 0           # reset enforced-gate retry budget per step
        state.pop("checkpointed_step", None)       # allow a fresh checkpoint on the new step
        _emit("step_advance", {"step": reported})


def _extract_pytest_summary(text: str) -> str:
    for line in text.splitlines():
        if any(w in line for w in ("passed", "failed", "error")):
            return line.strip()[:120]
    return text[:120]


def _handle_done(cwd: str, state: dict[str, Any]) -> None:
    """Independently verify before accepting the agent's done declaration. The
    authoritative gate is the FULL formal ladder (verify_formal) — verified/unknown accept
    (unknown = tool/model incapacity, never block on that); counterexample/spec_rejected
    reject and continue (NEVER report a pass on a rejected spec). Falls back to the
    deterministic verify_core gate if formal_core is unavailable."""
    passed, summary = False, "verify unavailable"
    os.environ.setdefault("VERIFY_REQUIRE_PLAN", "false")  # plan already enforced upstream
    try:
        import formal_core
        fres = formal_core.verify_formal(cwd)
        kind = fres.get("result")
        summary = f"verify_formal: {kind} ({fres.get('method') or fres.get('reason','')})"[:160]
        passed = kind in ("verified", "unknown")  # unknown = can't adjudicate → don't block done
        _emit("verify_enforced", {"phase": "done", "result": kind, "method": fres.get("method")})
        # P6 — promote a done-gate counterexample/rejected-spec into the regression corpus.
        if _enforce is not None and not passed:
            try:
                _enforce.promote_counterexample(state, fres, target=cwd)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        try:
            import verify_core
            res = verify_core.verify(cwd)
            passed, summary = bool(res.get("passed")), res.get("summary", "")
        except Exception as e:  # noqa: BLE001
            summary = f"verify error: {e}"
    if passed:
        _emit("run_complete", {"turns": state.get("total_turns", 0),
                               "steps": state.get("current_step", 1)})
        # B3.5 — KG task-close memory write (once per run): record what was decided + why.
        # P2 — enforced outcome write (solved=True) closing the routing loop.
        if _enforce is not None:
            try:
                _enforce.kg_taskclose_write(cwd, state, f"completed & verified — {summary}")
                _enforce.log_run_outcome(cwd, state, solved=True)
            except Exception:  # noqa: BLE001
                pass
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
    _install_token_callbacks()  # Phase 2: fan the model's token stream into the livelog
    cwd = os.getcwd()
    state = _load_state(cwd)
    plan = _load_plan(cwd)

    # ── Mandatory conductor planning on turn 1 (deterministic, in-process) ───────
    # On the FIRST turn of a task with no PLAN.md, the conductor authors a SIGNED
    # PLAN.md (V4-Pro decomposition) BEFORE the model generates anything; the note is
    # injected into this same {"context"} re-injection the contract already uses, so
    # the model reads PLAN.md on its first pass. Best-effort: an existing PLAN.md, a
    # continuation turn, a trivial lookup, or any failure → skip (model self-directs).
    try:
        plan_note = _maybe_plan_turn1(cwd, plan, kw)
    except Exception as e:  # noqa: BLE001 — planning must NEVER drop the contract re-injection
        _emit("plan_error", {"error": str(e)[:160]})
        plan_note = None
    if plan_note:
        plan = _load_plan(cwd)  # re-read so the contract below reflects the new steps
    step = int(state.get("current_step", 1))
    total = len(plan.get("steps", []))
    sd = _get_step(plan, step)
    turns_on = int(state.get("turns_on_current_step", 0))
    guidance = state.get("pending_guidance")

    # ── v2 cooperative controls (flags written by Hermes Studio via ui/server) ──
    # These are honoured BETWEEN steps; they cannot hard-block the model call, so
    # they cooperatively instruct the agent (Hard Decision #6).
    cdir = _state_path(cwd).parent  # <cwd>/.hermes-conductor
    paused = (cdir / "pause").exists()
    steer = None
    try:
        sp = cdir / "steer.txt"
        if sp.exists():
            steer = sp.read_text(encoding="utf-8").strip() or None
            sp.unlink()
    except OSError:
        pass
    approve_gate = os.environ.get("CONDUCTOR_REQUIRE_APPROVAL") == "1"
    approved = True
    if approve_gate:
        try:
            approved = (cdir / "approve").read_text(encoding="utf-8").strip() == "1"
        except OSError:
            approved = False

    lines = [
        f"## Execution State [conductor, turn {state.get('total_turns', 0) + 1}]",
        f"Current step: {step}/{total or '?'} — {sd.get('description', '?')}",
        f"Complexity: {sd.get('complexity', 'standard')}",
        f"Turns on this step: {turns_on}",
        f"Last verify: {state.get('last_verify_result', 'not run')}",
    ]
    if plan_note:  # turn-1 conductor plan: lead with it so the model reads PLAN.md first
        lines = [plan_note, ""] + lines
    if paused:
        lines = ["## ⏸ OPERATOR PAUSE",
                 "The operator has paused this run. Do NOT begin new work. Finish only the "
                 "current action if one is mid-flight, then STOP and wait — do not start the "
                 "next step until the pause is lifted.", ""] + lines
        _emit("paused", {"step": step})
    if steer:
        lines += ["", "## Operator steer (apply NOW, non-destructive):", steer]
        _emit("steer", {"step": step, "text": steer[:200]})
    if guidance and approved:
        lines += ["", "## Frontier Guidance (from conductor — apply NOW):", str(guidance)]
        state["pending_guidance"] = None
        _emit("guidance_applied", {"step": step})
    elif guidance and not approved:
        # approval gate on but not yet approved → hold the guidance, tell the user
        lines += ["", "## Guidance awaiting operator approval — pause and request approval "
                  "before proceeding on this step."]
        _emit("awaiting_approval", {"step": step})
    if sd.get("complexity") == "HIGH" and turns_on == 0:
        lines.append("⚠ This step is HIGH complexity — call reasoning_escalation before "
                     "writing code, or set conductor_requested=true in EXECUTION_STATE.json.")
    # CoT-spiral guard: N consecutive LLM turns with NO tool call / file write / verify
    # (reset in _post_tool_call) means the model is reasoning in circles across turns —
    # the cross-turn complement to the per-call thinking_budget. Nudge it to commit to disk.
    cot = int(state.get("reasoning_only_turns", 0)) + 1
    state["reasoning_only_turns"] = cot
    if cot >= int(os.environ.get("HM_COT_SPIRAL_TURNS", "4")):
        _emit("cot_spiral", {"turns": cot, "step": step})
        lines += ["", (f"## ⚠ Reasoning spiral — {cot} turns with no tool call or file write\n"
                       "STOP analyzing. Write your current best implementation to disk NOW and "
                       "run the verifier (pytest). Iterate from the TEST OUTPUT, not from further "
                       "reasoning — a failing test is more useful than more thinking.")]
    lines += [
        "", "After this turn, update EXECUTION_STATE.json in the cwd:",
        '  {"current_step": N, "step_status": "complete"|"in_progress",',
        '   "last_verify_result": "pytest: …", "conductor_requested": false,',
        '   "done_condition_met": false}',
        "Set done_condition_met=true ONLY when pytest passes and all steps are complete. "
        "Do not replan or re-architect — execute the plan.",
    ]

    # ── Part B B2: research ENTRY gate (once per qualifying task) + queued enforcement
    # guidance (verify/watchdog feedback queued by post_tool_call). Best-effort.
    if _enforce is not None:
        task_text = "; ".join(s.get("description", "") for s in plan.get("steps", []))[:1000]
        step_desc = sd.get("description", "")
        # SG2 — per-run spend cap: set state['cloud_blocked'] when the run cap is reached so
        # cloud escalation is blocked for the rest of the run (fabric/local still allowed).
        try:
            cap_warn = _enforce.check_run_cap(state)
            if cap_warn:
                lines += ["", cap_warn]
        except Exception:  # noqa: BLE001
            pass
        # P2 — enforced route read BEFORE the model sees the task (sets state['task_class']).
        try:
            rl = _enforce.route_task(state, task_text)
            if rl:
                lines += ["", rl]
        except Exception:  # noqa: BLE001
            pass
        for fn, arg in ((_enforce.research_entry_gate, task_text),
                        (_enforce.classify_step, step_desc),      # B3.6 classify in-hook
                        (_enforce.rag_before_multifile, step_desc)):  # B3.7 RAG pre multi-file
            try:
                g = (fn(cwd, state, arg) if fn is not _enforce.classify_step
                     else fn(state, arg))
            except Exception:  # noqa: BLE001
                g = None
            if g:
                lines += ["", g]
        # P5 — DAG schedule hint (multi-file only): ready wave + parallel/isolation + conflicts.
        # P7 — committee-planning availability hint (critical planning + parallel backend up).
        for hint_fn in (_enforce.dag_schedule_hint, _enforce.committee_hint):
            try:
                h = (hint_fn(state, plan) if hint_fn is _enforce.dag_schedule_hint
                     else hint_fn(state, step_desc))
                if h:
                    lines += ["", h]
            except Exception:  # noqa: BLE001
                pass
    for g in (state.get("enforce_guidance") or []):
        lines += ["", str(g)]
    state["enforce_guidance"] = []

    state["total_turns"] = state.get("total_turns", 0) + 1
    state["turns_on_current_step"] = turns_on + 1
    _save_state(cwd, state)
    _emit("llm_call", {"step": step, "total": total, "turns_on_step": turns_on + 1,
                       "has_guidance": guidance is not None})
    return {"context": "\n".join(lines)}


def _coerce_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _extract_tokens(resp: Any) -> dict[str, int]:
    """Best-effort token counts from a hermes LLM response (dict or object).
    Tries a few common shapes; absent fields just don't appear (tok/s degrades to —)."""
    out: dict[str, int] = {}

    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    usage = _get(resp, "usage") or resp
    for src, dst in (("output_tokens", "output_tokens"), ("completion_tokens", "output_tokens"),
                     ("thinking_tokens", "thinking_tokens"), ("reasoning_tokens", "thinking_tokens"),
                     ("prompt_tokens", "prompt_tokens"), ("input_tokens", "prompt_tokens")):
        v = _coerce_int(_get(usage, src))
        if v is not None and dst not in out:
            out[dst] = v
    return out


def _post_llm_call(response: Any = None, **kw: Any) -> None:
    """Emit conductor.llm_response with token counts so the web UI's run chrome can
    compute tok/s (output tokens / wall-time between the call and this response)."""
    _remove_token_callbacks()  # Phase 2: detach the per-turn token writers
    resp = response if response is not None else kw.get("result") or kw.get("completion")
    cwd = os.getcwd()
    state = _load_state(cwd)
    toks = _extract_tokens(resp)
    elapsed_s = _coerce_int(kw.get("elapsed_s") or kw.get("duration_s")) or 0
    payload = {"step": int(state.get("current_step", 1)), "elapsed_s": elapsed_s}
    payload.update(toks)
    _emit("llm_response", payload)
    # P1 — enforced cost/latency/backend attribution for the local executor call.
    if _enforce is not None:
        try:
            _enforce.profile_executor_call(state, toks, elapsed_s * 1000)
        except Exception:  # noqa: BLE001
            pass


def _post_tool_call(tool_name: str = "", args: Optional[dict] = None, result: Any = None, **kw: Any):
    """Detect file writes + verify results; fire the conductor on stuck-detection."""
    cwd = os.getcwd()
    state = _load_state(cwd)
    state["reasoning_only_turns"] = 0  # a tool/file/verify fired → not a CoT spiral
    args = args if isinstance(args, dict) else {}
    step = int(state.get("current_step", 1))
    result_text = str(result)[:2000]

    def _queue(g: Optional[str]) -> None:
        if g:
            state.setdefault("enforce_guidance", []).append(g)

    if tool_name in ("write_file", "edit_file", "str_replace", "patch"):
        wpath = args.get("path") or args.get("file_path") or "?"
        _emit("file_write", {"step": step, "file": wpath})
        # B2.1 — fire the fast verify_formal compile/type/lint gate on the written file.
        if _enforce is not None and wpath != "?":
            try:
                _queue(_enforce.on_file_write(cwd, state, wpath))
            except Exception:  # noqa: BLE001
                pass

    if tool_name in ("terminal", "bash", "shell"):
        cmd = str(args.get("command", ""))
        if "pytest" in cmd:
            passed = "passed" in result_text and "failed" not in result_text and "error" not in result_text.lower()
            state["last_verify_result"] = _extract_pytest_summary(result_text)
            if passed:
                state["verify_consecutive_failures"] = 0
                _emit("verify_pass", {"step": step, "result": state["last_verify_result"]})
                print(f"[conductor] ✓ verify passed on step {step}", flush=True)
                # B2.2 — checkpoint AFTER a green verify (the checkpoint re-verifies and
                # refuses on RED, so it is the hard gate). Model never decides this.
                if _enforce is not None:
                    try:
                        _enforce.checkpoint_after_green(cwd, state)
                    except Exception:  # noqa: BLE001
                        pass
            else:
                fails = int(state.get("verify_consecutive_failures", 0)) + 1
                state["verify_consecutive_failures"] = fails
                _emit("verify_fail", {"step": step, "failures": fails})
                if fails >= 2 and not state.get("conductor_triggered_this_step"):
                    _trigger_conductor(cwd, state, "verify_double_fail", step, result_text[:500])
                # P3 — surface the best-of-N dispatch target (fabric→cloud, never blind local).
                if _enforce is not None:
                    try:
                        _queue(_enforce.best_of_n_hint(state))
                    except Exception:  # noqa: BLE001
                        pass

    _sync_execution_state(cwd, state)

    # B2.4 — watchdog background tick: fires unconditionally on every tool call (a
    # background-via-hook, never a model tool call). Emits a span; nudges on a spiral.
    if _enforce is not None:
        try:
            _queue(_enforce.watchdog_tick(cwd, state, reasoning_text=result_text))
        except Exception:  # noqa: BLE001
            pass

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
    # B3.5 backstop — ensure a KG task-close write even if the run ended without a
    # verified done (kg_taskclose_write is once-per-run; a prior _handle_done call no-ops).
    if _enforce is not None:
        try:
            _enforce.kg_taskclose_write(cwd, state,
                                        f"session ended at step {state.get('current_step', 1)}")
            # P2 — backstop outcome write (log_run_outcome is once-per-run; a verified done
            # already logged solved=True, so this only fires for an unfinished run → unsolved).
            _enforce.log_run_outcome(cwd, state, solved=bool(state.get("done_condition_met")),
                                     failure_class="trajectory-fixable")
            # SG3 — end-of-run ratio alert: write ratio.log + print OK/ALERT to the cockpit.
            rl = _enforce.ratio_log_run(state)
            if rl:
                print(f"[conductor] ratio {rl}", flush=True)
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
# ── Phase 2: token fan-out into the livelog ──────────────────────────────────
def _token_agent() -> Any:
    """The live hermes AIAgent (the holder of the stream callbacks)."""
    ctx = _CTX
    mgr = getattr(ctx, "_manager", None) if ctx is not None else None
    if mgr is None:
        return None
    cli = getattr(mgr, "_cli", None)
    agent = getattr(cli, "agent", None) if cli is not None else None
    return agent if agent is not None else getattr(mgr, "_agent", None)


def _gen(span: str, text: Any) -> None:
    """Write one token/reasoning delta as a gen.* span. Non-blocking-ish (the
    livelog append is cheap) and silent on empty/None."""
    if not text:
        return
    try:
        s = text if isinstance(text, str) else (getattr(text, "content", None) or "")
        if s:
            from lib import livelog
            # `text` is what feeds.py/stream.rs/feed.ts read; `content` mirrors the
            # directive's field name.
            livelog.forward(span, {"text": s, "content": s})
    except Exception:  # noqa: BLE001 - never let token logging break a turn
        pass


def _delta_writer():
    return lambda delta=None, *a, **k: _gen("gen.token", getattr(delta, "content", None) if not isinstance(delta, str) else delta)


def _thinking_writer():
    return lambda chunk=None, *a, **k: _gen("gen.thinking", chunk)


def _reasoning_writer():
    return lambda chunk=None, *a, **k: _gen("gen.reasoning", chunk)


def _install_token_callbacks() -> None:
    global _tok_warned
    agent = _token_agent()
    if agent is None:
        if not _tok_warned:
            _tok_warned = True
            _emit("error", {"reason": "token-stream: live agent not found; gen.* disabled"})
        return
    try:
        agent.stream_delta_callback = _delta_writer()
        agent.thinking_callback = _thinking_writer()
        agent.reasoning_callback = _reasoning_writer()
    except Exception:  # noqa: BLE001
        pass


def _remove_token_callbacks() -> None:
    agent = _token_agent()
    if agent is None:
        return
    for attr in ("stream_delta_callback", "thinking_callback", "reasoning_callback"):
        try:
            setattr(agent, attr, None)
        except Exception:  # noqa: BLE001
            pass


def register(ctx) -> None:
    """Hermes calls this with a PluginContext. Register the lifecycle hooks."""
    global _CTX
    _CTX = ctx  # capture so the token-callback installer can reach the live agent
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_llm_call", _post_llm_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)
