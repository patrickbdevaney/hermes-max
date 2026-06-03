"""enforce.py — Part B B2: lifecycle ENFORCEMENT of the high-value MCPs.

The driver LLM skips verification / checkpointing / research exactly when it feels
confident — so we fire them DETERMINISTICALLY from the conductor's hooks. The model's
only influence is the content, never whether the call happens. Four capabilities are
hard-enforced here (the B0 3/3 set):

  1. verify_formal  — fast compile/type/lint gate (rungs 0-1) on every file write, and
                      the FULL ladder at the done gate (handled in __init__._handle_done).
                      Four-value handling: verified/unknown → proceed; counterexample →
                      block-with-guidance (bounded retries, then surface); spec_rejected →
                      downgrade-and-flag (NEVER report a pass). Never wedges the loop.
  2. checkpoint     — fire automatically AFTER a green verify, before advancing. The
                      checkpoint itself re-verifies (mcp-verify) and refuses on RED, so it
                      is the hard gate; the model never decides whether to checkpoint.
  3. research entry — fire deep_research ONCE at task start for a task the novelty
                      classifier marks `synthesis` (novel external knowledge), before
                      implementation. Still corpus-first-gated inside (instant if covered).
  4. watchdog       — run unconditionally as a background-via-hook tick on every tool
                      call (spiral/stall/budget). Never a model tool call.

Every enforced fire emits an OTel/livelog span and degrades gracefully: a down MCP is
logged and skipped, never crashes the agent loop. All imports are lazy + guarded — this
module runs inside the Hermes venv alongside the plugin.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

# repo root + the cores we reach (sibling dirs). __init__.py already added repo root +
# mcp-escalation + mcp-verify; add the rest here so a missing dir degrades to None.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
for _d in ("mcp-verify", "mcp-checkpoint", "mcp-research", "mcp-watchdog"):
    _p = os.path.join(_REPO, _d)
    if _p not in sys.path:
        sys.path.append(_p)

# ── enable flags (all default ON; env-toggleable for ablation) ────────────────
def _on(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off")


ENF_VERIFY = _on("CONDUCTOR_ENFORCE_VERIFY")
ENF_CHECKPOINT = _on("CONDUCTOR_ENFORCE_CHECKPOINT")
ENF_RESEARCH = _on("CONDUCTOR_ENFORCE_RESEARCH")
ENF_WATCHDOG = _on("CONDUCTOR_ENFORCE_WATCHDOG")
WRITE_MIN_BYTES = int(os.environ.get("CONDUCTOR_VERIFY_WRITE_MIN_BYTES", "64"))
VERIFY_MAX_RETRIES = int(os.environ.get("CONDUCTOR_VERIFY_MAX_RETRIES", "2"))
_SRC_EXT = (".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go")


def _emit(event: str, data: dict[str, Any]) -> None:
    try:
        from lib import livelog
        livelog.forward(f"conductor.{event}", data, status="ok")
    except Exception:  # noqa: BLE001 — observability must never break the loop
        pass


# ── lazy guarded core handles ─────────────────────────────────────────────────
def _mod(name: str):
    try:
        return __import__(name)
    except Exception:  # noqa: BLE001
        return None


# 1 ── verify_formal: fast write-time gate (rungs 0-1) ─────────────────────────
def on_file_write(cwd: str, state: dict[str, Any], path: str) -> Optional[str]:
    """Fire the fast compile/type/lint gate on a source write. Returns guidance text to
    inject next turn on a hard compile/type failure, else None. Bounded + non-wedging."""
    if not ENF_VERIFY or not path:
        return None
    p = Path(path)
    if p.suffix.lower() not in _SRC_EXT:
        return None
    try:
        if p.is_file() and p.stat().st_size < WRITE_MIN_BYTES:
            return None
    except OSError:
        return None
    fc = _mod("formal_core")
    if fc is None:
        _emit("verify_enforced", {"skipped": "formal_core unavailable", "file": path})
        return None
    try:
        res = fc.compile_gate(path)
    except Exception as e:  # noqa: BLE001
        _emit("verify_enforced", {"error": str(e)[:160], "file": path})
        return None
    kind = res.get("result")
    _emit("verify_enforced", {"phase": "write", "file": path, "result": kind,
                              "stage": res.get("stage"), "advisories": res.get("advisories")})
    if kind == "counterexample":
        n = int(state.get("formal_write_fails", 0)) + 1
        state["formal_write_fails"] = n
        if n <= VERIFY_MAX_RETRIES:
            return (f"## Verification gate (enforced)\nThe {res.get('method')} {res.get('stage')} "
                    f"check FAILED on `{Path(path).name}`:\n{str(res.get('trace',''))[:600]}\n"
                    "Fix this compile/type error before continuing — do not proceed on broken code.")
        # bounded: stop nagging after retries, surface once, never wedge
        _emit("verify_enforced", {"phase": "write", "file": path, "surfaced": True, "fails": n})
        return None
    state["formal_write_fails"] = 0
    return None


# 2 ── checkpoint after a green verify ─────────────────────────────────────────
def checkpoint_after_green(cwd: str, state: dict[str, Any]) -> None:
    """Fire a verified checkpoint after the conductor observes a green verify. The
    checkpoint re-verifies via mcp-verify and refuses on RED — so it is the hard gate.
    Fires at most once per step (state flag), degrades if mcp-checkpoint is down."""
    if not ENF_CHECKPOINT:
        return
    step = int(state.get("current_step", 1))
    if state.get("checkpointed_step") == step:
        return  # already checkpointed this green step
    cp = _mod("checkpoint_core")
    if cp is None:
        _emit("checkpoint_enforced", {"skipped": "checkpoint_core unavailable", "step": step})
        return
    try:
        r = cp.checkpoint(label=f"step {step} verified-green", verify=True, repo_path=cwd)
    except Exception as e:  # noqa: BLE001
        _emit("checkpoint_enforced", {"error": str(e)[:160], "step": step})
        return
    if r.get("checkpointed"):
        state["checkpointed_step"] = step
    _emit("checkpoint_enforced", {"step": step, "checkpointed": bool(r.get("checkpointed")),
                                  "sha": r.get("sha"), "reason": r.get("reason")})


# 3 ── research entry gate (once per qualifying task, at task start) ───────────
def research_entry_gate(cwd: str, state: dict[str, Any], task_text: str) -> Optional[str]:
    """At task start, if the novelty classifier marks the task `synthesis` (novel external
    knowledge), fire deep_research ONCE before implementation and inject a digest. Still
    corpus-first-gated inside deep_research (instant if already covered). One fire/task."""
    if not ENF_RESEARCH or state.get("research_entry_done"):
        return None
    if not (task_text or "").strip():
        return None  # no task signal yet (plan not ready) — try again next turn
    state["research_entry_done"] = True  # mark before firing so a failure never re-fires
    rc = _mod("research_core")
    if rc is None:
        return None
    try:
        cls = rc.classify_research_need(task_text)
    except Exception:  # noqa: BLE001
        return None
    if cls.get("class") != "synthesis" or cls.get("block"):
        _emit("research_entry_gate", {"fired": False, "class": cls.get("class")})
        return None
    _emit("research_entry_gate", {"fired": True, "class": "synthesis"})
    try:
        out = rc.deep_research(task_text)
    except Exception as e:  # noqa: BLE001
        _emit("research_entry_gate", {"error": str(e)[:160]})
        return None
    if not isinstance(out, dict) or not out.get("ok"):
        return None
    digest = (out.get("report_md") or out.get("note") or "")[:1500]
    if not digest:
        return None
    _emit("research_entry_gate", {"sources": out.get("sources_explored"),
                                  "from_corpus": out.get("answered_from_corpus", False)})
    return ("## Entry research (enforced, once for this task)\n"
            "Prior/external knowledge gathered before implementation:\n" + digest)


# 4 ── watchdog: background-via-hook tick ──────────────────────────────────────
def watchdog_tick(cwd: str, state: dict[str, Any], reasoning_text: str = "") -> Optional[str]:
    """Unconditional liveness check fired every tool call (not a model tool). Detects CoT
    spirals via the watchdog; emits a background span. Returns a nudge if a spiral is
    detected, else None. Degrades to a no-op if mcp-watchdog is down."""
    if not ENF_WATCHDOG:
        return None
    wd = _mod("watchdog_core")
    if wd is None:
        return None
    try:
        spiral = wd.check_spiral(reasoning_text) if reasoning_text else {"spiral": False}
    except Exception:  # noqa: BLE001
        return None
    _emit("watchdog_background", {"step": int(state.get("current_step", 1)),
                                  "spiral": bool(spiral.get("spiral"))})
    if spiral.get("spiral"):
        return ("## Watchdog (enforced)\nRepetitive reasoning detected — you appear to be "
                "looping. Change approach or escalate; do not repeat the last attempt.")
    return None


def enforce_stats() -> dict[str, Any]:
    return {"verify": ENF_VERIFY, "checkpoint": ENF_CHECKPOINT, "research": ENF_RESEARCH,
            "watchdog": ENF_WATCHDOG, "write_min_bytes": WRITE_MIN_BYTES,
            "verify_max_retries": VERIFY_MAX_RETRIES,
            "cores": {n: _mod(n) is not None for n in
                      ("formal_core", "checkpoint_core", "research_core", "watchdog_core")}}
