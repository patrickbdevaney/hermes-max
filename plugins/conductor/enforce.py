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
for _d in ("mcp-verify", "mcp-checkpoint", "mcp-research", "mcp-watchdog",
           "mcp-knowledge-graph", "mcp-codebase-rag", "mcp-costprofiler", "mcp-search",
           "mcp-router"):
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
# B3 soft-enforce (lifecycle point, not a hard gate)
ENF_KG = _on("CONDUCTOR_ENFORCE_KG")
ENF_CLASSIFY = _on("CONDUCTOR_ENFORCE_CLASSIFY")
ENF_RAG = _on("CONDUCTOR_ENFORCE_RAG")
ENF_PROFILE = _on("CONDUCTOR_ENFORCE_PROFILE")  # P1 cost attribution (enforced, post_llm_call)
ENF_ROUTE = _on("CONDUCTOR_ENFORCE_ROUTE")      # P2 bandit route read (pre_llm_call) + write (post-run)
_MULTIFILE_HINT = ("multiple files", "across", "refactor", "rename", "move ", "each module",
                   "every file", "all the", "throughout", "codebase-wide", "multi-file")
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


# 5 ── KG task-close memory write (soft-enforce, run-complete) ────────────────
def kg_taskclose_write(cwd: str, state: dict[str, Any], summary: str) -> None:
    """At task CLOSE, record what was decided + why into the KG, regardless of whether the
    model thought to. Captures the ambient 'we decided X about this codebase' facts the
    model reliably misses. Fires at most once per run; degrades if mcp-knowledge-graph down."""
    if not ENF_KG or state.get("kg_taskclose_done"):
        return
    state["kg_taskclose_done"] = True
    kg = _mod("kg_core")
    if kg is None:
        _emit("kg_taskclose_write", {"skipped": "kg_core unavailable"})
        return
    try:
        name = f"task:{Path(cwd).name}@step{state.get('current_step', 1)}"
        kg.record_entity("task", name, props={
            "summary": (summary or "")[:1000], "steps": state.get("current_step", 1),
            "turns": state.get("total_turns", 0), "verified": state.get("last_verify_result", "")[:120]})
    except Exception as e:  # noqa: BLE001
        _emit("kg_taskclose_write", {"error": str(e)[:160]})
        return
    _emit("kg_taskclose_write", {"entity": name})


# 6 ── classification in the hook (soft-enforce, pre_llm_call) ─────────────────
def classify_step(state: dict[str, Any], step_desc: str) -> Optional[str]:
    """Run the criticality/novelty classification IN THE HOOK before the model sees the
    step, so it can't dodge the conductor by self-classifying a step as 'easy'. Once per
    step. Returns a line to inject. Degrades to None without the classifier."""
    if not ENF_CLASSIFY or not (step_desc or "").strip():
        return None
    step = int(state.get("current_step", 1))
    if state.get("classified_step") == step:
        return None
    state["classified_step"] = step
    crit = _mod("criticality")
    if crit is None:
        return None
    try:
        c = crit.criticality_classify(step_desc, "python")
    except Exception:  # noqa: BLE001
        return None
    _emit("classification_prefired", {"step": step, "critical": c.get("critical"),
                                      "dimensions": c.get("dimensions"), "method": c.get("method")})
    if c.get("critical"):
        return ("## Classification (enforced, in-hook)\nThis step is CRITICAL "
                f"({', '.join(c.get('dimensions', []))}) — implement defensively and expect the "
                "verify_formal gate to demand a strong, mutation-surviving property before done.")
    return None


# 7 ── RAG retrieval before a multi-file edit (soft-enforce, step start) ───────
def rag_before_multifile(cwd: str, state: dict[str, Any], step_desc: str) -> Optional[str]:
    """At the START of a step that looks like a multi-file edit, fire a RAG retrieval pass
    to surface relevant prior patterns before implementation — even when the model 'knows
    the codebase'. Once per step; degrades if mcp-codebase-rag is down."""
    if not ENF_RAG or not (step_desc or "").strip():
        return None
    if not any(h in step_desc.lower() for h in _MULTIFILE_HINT):
        return None
    step = int(state.get("current_step", 1))
    if state.get("rag_step") == step:
        return None
    state["rag_step"] = step
    rag = _mod("rag_core")
    if rag is None:
        _emit("rag_pre_multifile", {"skipped": "rag_core unavailable", "step": step})
        return None
    try:
        res = rag.search_code(step_desc, k=5)
    except Exception as e:  # noqa: BLE001
        _emit("rag_pre_multifile", {"error": str(e)[:160], "step": step})
        return None
    hits = res.get("results") or res.get("hits") or []
    _emit("rag_pre_multifile", {"step": step, "hits": len(hits)})
    if not hits:
        return None
    lines = []
    for h in hits[:5]:
        loc = h.get("path") or h.get("source") or h.get("symbol") or "?"
        snip = (h.get("snippet") or h.get("text") or "")[:160].replace("\n", " ")
        lines.append(f"- `{loc}`: {snip}")
    return ("## Prior patterns (enforced RAG, multi-file edit)\nRelevant existing code to "
            "match before editing across files:\n" + "\n".join(lines))


# P2 ── bandit route (enforced read pre_llm_call, write post-run) ─────────────
def route_task(state: dict[str, Any], task_text: str) -> Optional[str]:
    """ENFORCED READ: at task start, classify the task and pick a backend BEFORE the model
    sees it (so the executor cannot self-route around the policy). Sets state['task_class']
    and state['route']; returns an advisory line. Default route is local-serial-free; an
    escalate=True flag (consumed by P3/P7) only when warranted + uplift-positive. Once/task."""
    if not ENF_ROUTE or state.get("route_done"):
        return None
    if not (task_text or "").strip():
        return None
    state["route_done"] = True
    rc = _mod("router_core")
    if rc is None:
        state["task_class"] = "code_execute"
        return None
    try:
        d = rc.route(task_text)
    except Exception:  # noqa: BLE001
        state["task_class"] = "code_execute"
        return None
    state["task_class"] = d.get("task_class", "code_execute")
    state["route"] = {"backend": d.get("backend"), "escalate": bool(d.get("escalate"))}
    notes = []
    try:
        notes = rc.recall_notes(state["task_class"], 2)
    except Exception:  # noqa: BLE001
        pass
    _emit("route_selected", {"task_class": state["task_class"], "backend": d.get("backend"),
                             "escalate": d.get("escalate"), "difficulty": d.get("difficulty")})
    line = (f"## Route (enforced)\nTask class **{state['task_class']}** → default backend "
            f"**{d.get('backend')}** ({d.get('reason')}).")
    if notes:
        line += "\nPrior experience on this task class:\n" + "\n".join(f"- {n}" for n in notes)
    return line


def log_run_outcome(cwd: str, state: dict[str, Any], solved: bool,
                    failure_class: str = "") -> None:
    """ENFORCED WRITE: at run close, log the outcome (profiler reads it) + update the bandit,
    so each run improves routing. Once per run; cost pulled from the profiler rollup if any."""
    if not ENF_ROUTE or state.get("outcome_logged"):
        return
    state["outcome_logged"] = True
    rc = _mod("router_core")
    if rc is None:
        return
    tc = state.get("task_class") or "code_execute"
    backend = (state.get("route") or {}).get("backend", "local-serial")
    cost = 0.0
    prof = _mod("profiler_core")
    if prof is not None:
        try:
            cost = prof.report("today", tc).get("total_usd", 0.0)
        except Exception:  # noqa: BLE001
            pass
    try:
        rc.log_outcome(tc, backend, solved, cost_usd=cost,
                       failure_class=(failure_class or "route-fixable") if not solved else "")
    except Exception:  # noqa: BLE001
        return
    _emit("outcome_logged", {"task_class": tc, "backend": backend, "solved": solved})


# P1 ── cost/latency/backend attribution (enforced, post_llm_call) ────────────
def profile_executor_call(state: dict[str, Any], toks: dict[str, int],
                          wall_ms: int) -> None:
    """Attribute the local executor's call to the local-serial backend (the external hermes
    loop bypasses the lib/inference ledger). Enforced so nothing escapes accounting; emits a
    span attribute set. ~$0 (sunk hardware). Degrades to a no-op if the profiler is absent."""
    if not ENF_PROFILE:
        return
    prof = _mod("profiler_core")
    if prof is None:
        return
    tc = state.get("task_class") or "code_execute"
    in_tok = int(toks.get("prompt_tokens", 0) or 0)
    out_tok = int(toks.get("output_tokens", 0) or 0)
    try:
        prof.log_call("local-serial", tc, in_tok, out_tok, 0.0, int(wall_ms),
                      provider="local_vllm", source="executor")
        _emit("cost_attributed", prof.span_attrs("local-serial", tc, in_tok, out_tok, 0.0, int(wall_ms)))
    except Exception:  # noqa: BLE001
        pass


def enforce_stats() -> dict[str, Any]:
    return {"verify": ENF_VERIFY, "checkpoint": ENF_CHECKPOINT, "research": ENF_RESEARCH,
            "watchdog": ENF_WATCHDOG, "kg": ENF_KG, "classify": ENF_CLASSIFY, "rag": ENF_RAG,
            "write_min_bytes": WRITE_MIN_BYTES, "verify_max_retries": VERIFY_MAX_RETRIES,
            "cores": {n: _mod(n) is not None for n in
                      ("formal_core", "checkpoint_core", "research_core", "watchdog_core",
                       "kg_core", "criticality", "rag_core")}}
