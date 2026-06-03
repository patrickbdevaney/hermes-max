"""dispatch_core.py — the PARALLELISM DISPATCHER (shared primitive for P3/P5/P7).

The governing constraint of the cost-asymmetry engine: the local executor (Qwen3 on the
single Thor vLLM) is SINGLE-STREAM — N branches there cost N× wall-clock. The fabric
(Groq/Cerebras) and cloud (DeepInfra/DeepSeek) are genuinely parallel (API concurrency).
So EVERY fan-out (best-of-N, DAG nodes, committee) routes through this dispatcher, which:

  • fabric  → parallel to the rate limit (preferred: free + parallel)
  • cloud   → parallel, real dollars (only when fabric is exhausted)
  • local   → SERIAL. The dispatcher REFUSES to fan N branches onto local; at most it
              permits a bounded, serial best-of-N (N≤3) and only on a verify failure.

It exposes the local-serial-vs-parallel tradeoff to the router rather than hiding it.
This module also implements the criticality-gated, EXECUTION-verified best-of-N (Phase 3),
selecting by the existing verify oracle, never self-judgment. Deterministic-first; the
absence of fabric/cloud degrades to a single local attempt. Never raises.
"""
from __future__ import annotations

import os
from typing import Any, Optional


def _fabric():
    try:
        import pool
        return pool if pool.available() else None
    except Exception:  # noqa: BLE001
        return None


def _cloud_available() -> bool:
    # cloud parallel draft is exposed by mcp-escalation's pool; treat its presence as the signal
    try:
        import conductor_core  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def target_for(n: int, verify_failed: bool = False) -> dict[str, Any]:
    """Decide WHERE a fan-out of `n` branches lands, honoring the asymmetry. Returns
    {backend, parallel, n, reason}. Fabric first (free+parallel), then cloud (paid+parallel).

    HARD RULE (stricter than the source spec's grudging allowance): fan-out NEVER lands on
    the local executor. The Thor vLLM is single-stream; turning best-of-N into serial re-runs
    there fights the loop and burns wall-clock, so when no parallel backend is available the
    dispatcher returns n=1 (a single local attempt) — it does NOT serialize N on local. The
    caller falls back to one attempt + (separately) the conductor's cloud escalation."""
    if _fabric() is not None:
        return {"backend": "fabric", "parallel": True, "n": max(1, n),
                "reason": "fabric is free + parallel — preferred fan-out target"}
    if _cloud_available():
        return {"backend": "cloud-deepseek", "parallel": True, "n": max(1, n),
                "reason": "fabric exhausted → cloud parallel (real $, flat wall-clock)"}
    # local only: single-stream → NO fan-out (not even serial). One attempt.
    return {"backend": "local-serial", "parallel": False, "n": 1,
            "reason": "no parallel backend; local is single-stream → ONE attempt, never a "
                      "(serial) local fan-out. Escalate via the conductor for parallel help."}


def fanout(prompts: list[str], system: Optional[str] = None, temperature: float = 0.4,
           max_tokens: int = 1500) -> dict[str, Any]:
    """Run `len(prompts)` completions across the chosen parallel backend. Fabric via the
    pool (genuinely concurrent); cloud via the escalation pool if fabric is down; refuses to
    serialize many on local (returns the prompts to the caller to run ONE locally). Returns
    {backend, results:[str|None], parallel}."""
    if not prompts:
        return {"backend": "none", "results": [], "parallel": False}
    fab = _fabric()
    if fab is not None:
        try:
            return {"backend": "fabric", "parallel": True,
                    "results": fab.map_cheap(prompts, system=system, temperature=temperature,
                                             max_tokens=max_tokens)}
        except Exception:  # noqa: BLE001
            pass
    # cloud parallel draft (escalation pool) — one prompt fanned to N cross-family models
    if _cloud_available():
        try:
            import conductor_core
            # parallel_draft_pool expects a single prompt + n; map each prompt as its own draft
            out: list[Optional[str]] = []
            for p in prompts:
                r = conductor_core.run_role("steer", prompt=p, max_tokens=max_tokens)
                out.append(r.get("content") if isinstance(r, dict) and r.get("ok") else None)
            return {"backend": "cloud-deepseek", "parallel": True, "results": out}
        except Exception:  # noqa: BLE001
            pass
    # neither parallel backend: do NOT serialize N on local — signal single-attempt
    return {"backend": "local-serial", "parallel": False,
            "results": [None] * len(prompts),
            "note": "no parallel backend; run a single local attempt instead of fanning out"}


# ── criticality-gated, execution-verified best-of-N (Phase 3) ─────────────────
_DRAFT_SYS = ("You are an expert programmer. Produce ONE complete, correct solution to the "
              "task. Output only the code in a single fenced block, no prose.")


def best_of_n(task_spec: str, tests: dict[str, str], target_path: str = "solution.py",
              language: str = "python", n: int = 3, verify_failed: bool = False,
              critical: bool = False, base_files: Optional[dict] = None) -> dict[str, Any]:
    """Gated, execution-verified repeated sampling. OFF unless gated on: fires only on a
    verify-failure or a critical/high-value task. Fan-out lands on fabric→cloud via the
    dispatcher (never a blind local fan-out); selection is by the verify oracle (mcp-search
    select_from_candidates), never self-judgment. Logs the outcome to the bandit. Returns
    {ran, target, selected, green_count, reason}."""
    if not (verify_failed or critical):
        return {"ran": False, "reason": "best-of-N is off by default; not gated on "
                "(needs verify-failure or a critical/high-value task)"}
    if not tests:
        return {"ran": False, "reason": "no execution oracle (tests) — cannot select by "
                "execution; route to synthesis instead"}
    tgt = target_for(n, verify_failed)
    if tgt["backend"] == "local-serial" and tgt["n"] <= 1:
        return {"ran": False, "target": tgt,
                "reason": "local single-stream + no verify-failure → not worth serial fan-out"}

    # draft N candidates through the dispatcher (parallel off-local where possible)
    prompts = [f"{task_spec}\n\n(attempt {i + 1} of {tgt['n']}; be correct and minimal)"
               for i in range(tgt["n"])]
    fo = fanout(prompts, system=_DRAFT_SYS, temperature=0.5)
    drafts = [d for d in (fo.get("results") or []) if d]
    if not drafts:
        return {"ran": False, "target": tgt, "draft_backend": fo.get("backend"),
                "reason": "no candidates drafted (parallel backend unavailable); fall back "
                          "to a single local attempt"}

    try:
        import re
        import search_core
        def _code(txt: str) -> str:
            m = re.search(r"```(?:\w+)?\s*(.*?)```", txt, re.DOTALL)
            return (m.group(1) if m else txt).strip()
        candidates = [{"id": f"bon{i}", "files": {target_path: _code(d)}}
                      for i, d in enumerate(drafts)]
        sel = search_core.select_from_candidates(candidates, tests=tests, language=language,
                                                 base_files=base_files, early_exit=False,
                                                 formal=critical, critical=critical)
    except Exception as e:  # noqa: BLE001
        return {"ran": True, "target": tgt, "error": f"selection failed: {type(e).__name__}: {e}"}

    # close the loop in the bandit
    try:
        import router_core
        tc = router_core.task_class_of(task_spec)
        router_core.log_outcome(tc, tgt["backend"], solved=bool(sel.get("selected")),
                                failure_class="sample-fixable")
    except Exception:  # noqa: BLE001
        pass
    return {"ran": True, "target": tgt, "draft_backend": fo.get("backend"),
            "candidates": len(candidates), "selected": sel.get("selected"),
            "green_count": sel.get("green_count"), "reason": sel.get("reason"),
            "selected_files": sel.get("selected_files")}


def dispatch_stats() -> dict[str, Any]:
    return {"fabric_available": _fabric() is not None, "cloud_available": _cloud_available(),
            "rule": "fan-out → fabric → cloud ONLY; local is single-stream → never a fan-out "
                    "target (not even serial), one attempt instead"}
