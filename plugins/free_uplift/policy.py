"""FreeUpliftPlugin — ALL uplift logic lives here, nowhere else.

After a file completes and verify passes (before checkpoint), optionally spend ONE
Kimi-K2.6:free call to ask "does this implementation match its FILE SPEC and the
already-completed interfaces?" — a cheap proactive coherence check that catches
drift the local executor missed. It is useful only once the $10 OpenRouter deposit
is made and Kimi-K2.6:free is live; otherwise the plugin simply does not register.

It NEVER blocks the core loop on error, NEVER bypasses a rate limit, and is hard-
capped per file/task so it can't burn the daily free budget.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable, Optional

# repo root on path so lib.inference imports work when loaded standalone
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.inference import config, roster, run_role          # noqa: E402
from lib.inference import buckets                            # noqa: E402

_UPLIFT_SLOT = ("openrouter", "synth_free")                  # Kimi-K2.6:free
_REVIEW_PROMPT = (
    "Read the FILE SPEC in PLAN.md for this file and the implementation. Does it "
    "match exactly? Do its interfaces match already-completed files? Respond CLEAN "
    "or FLAG: <one specific issue>. Nothing else."
)


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


class FreeUpliftPlugin:
    name = "free_uplift"
    min_rpd_threshold = int(os.environ.get("FREE_UPLIFT_MIN_RPD", "200"))
    max_per_task = int(os.environ.get("FREE_UPLIFT_MAX_PER_TASK", "10"))
    max_per_file = int(os.environ.get("FREE_UPLIFT_MAX_PER_FILE", "2"))

    def __init__(self) -> None:
        self._task_calls = 0
        self._file_calls: dict[str, int] = {}

    # ── registration gate ────────────────────────────────────────────────────
    def should_register(self, env: Optional[dict[str, str]] = None) -> bool:
        """True only if ALL hold: the toggle is on, OPENROUTER_API_KEY is present,
        the Kimi slot is not deprecated, and there is daily free-RPD headroom."""
        env = env if env is not None else os.environ
        if not _truthy(env.get("INFERENCE_MODE_FREE_UPLIFT")):
            return False
        if not config.provider_present(_UPLIFT_SLOT[0], env):
            return False
        if not self._kimi_live():
            return False
        rem = buckets.remaining_rpd(*_UPLIFT_SLOT)
        if rem is not None and rem < self.min_rpd_threshold:
            return False
        return True

    def _kimi_live(self) -> bool:
        """Cheap liveness: not in KNOWN_DEPRECATED. (hm health does the full probe.)"""
        spec = config.resolve_model(*_UPLIFT_SLOT) or {}
        mid = spec.get("id", "")
        key = f"{_UPLIFT_SLOT[0]}.{_UPLIFT_SLOT[1]}"
        return key not in roster.KNOWN_DEPRECATED and mid not in roster.KNOWN_DEPRECATED

    # ── the hook ─────────────────────────────────────────────────────────────
    def post_verify_hook(self, file: str, plan: Any, completed: Any,
                         router: Optional[Callable[..., Any]] = None,
                         verify_result: Any = None) -> dict[str, Any]:
        """Returns {"proceed": True} (CLEAN / skipped) or {"proceed": False,
        "flag": "..."} (a concrete coherence issue). Never raises.

        Enhanced (Mode 3): when the cheap Kimi coherence check FLAGs an issue OR the
        verify gate passed with THIN property tests (<3), escalate to a larger free
        reasoning model (reasoning_escalation) for a deeper second opinion — frontier
        reasoning on demand, only at the moments it's warranted."""
        try:
            # hard caps — never burn the daily budget on one task/file
            if self._task_calls >= self.max_per_task:
                return {"proceed": True, "skipped": "max_per_task"}
            if self._file_calls.get(file, 0) >= self.max_per_file:
                return {"proceed": True, "skipped": "max_per_file"}
            # skip silently if the rate bucket is tight (never block on a limit)
            if not buckets.has_headroom(*_UPLIFT_SLOT, 200):
                return {"proceed": True, "skipped": "no_headroom"}

            call = router or run_role
            messages = [
                {"role": "system", "content": _REVIEW_PROMPT},
                {"role": "user", "content": self._context(file, plan, completed)},
            ]
            res = call("free_uplift", messages, max_tokens=100)
            self._task_calls += 1
            self._file_calls[file] = self._file_calls.get(file, 0) + 1

            if not res.get("ok"):
                return {"proceed": True, "skipped": "uplift_unavailable"}
            text = (res.get("text") or "").strip()
            flagged = text.upper().startswith("FLAG")
            flag = (text.split(":", 1)[1].strip() if ":" in text else text) if flagged else ""

            # Thin property coverage? verify_result may be a dict or an object.
            ptc = 0
            if isinstance(verify_result, dict):
                ptc = int(verify_result.get("property_test_count", 0) or 0)
            elif verify_result is not None:
                ptc = int(getattr(verify_result, "property_test_count", 0) or 0)

            # Deeper second opinion when coherence flags OR property tests are thin.
            if flagged or (verify_result is not None and ptc < 3):
                deep = self._deep_review(file, plan, completed)
                if flagged:
                    self._span("free_uplift_flagged", file, flag)
                    return {"proceed": False, "flag": flag, "file": file, "deep_review": deep}
                self._span("free_uplift_clean", file, "deep-reviewed (thin tests)")
                return {"proceed": True, "deep_review": deep}

            self._span("free_uplift_clean", file, "")
            return {"proceed": True}
        except Exception:
            return {"proceed": True, "skipped": "error"}

    def _deep_review(self, file: str, plan: Any, completed: Any) -> Optional[dict[str, Any]]:
        """Escalate to a larger free reasoning model for a deeper correctness review.
        Best-effort: free tier first, $0; logs an `uplift·deep` livelog line with the
        model + token count. Returns the reasoning model's verdict (or None)."""
        try:
            import conductor_core  # local import; mcp-escalation on path below
        except Exception:
            _esc = os.path.join(_ROOT, "mcp-escalation")
            if _esc not in sys.path:
                sys.path.insert(0, _esc)
            try:
                import conductor_core  # type: ignore
            except Exception:
                return None
        r = conductor_core.reasoning_escalation(
            question=("Review this implementation for correctness issues the tests "
                      "might miss (edge cases, invariants, concurrency, error paths)."),
            context=self._context(file, plan, completed), budget="standard")
        try:
            from lib import livelog
            tier = r.get("tier", "?")
            livelog.tool_ok(f"uplift·deep", secs=None,
                            ret={"file": file, "model": r.get("model"),
                                 "provider": r.get("provider"), "tier": tier,
                                 "tokens": r.get("tokens", 0)})
        except Exception:
            pass
        self._span("free_uplift_deep", file, f"{r.get('provider')}/{r.get('model')}")
        return r

    # ── helpers ──────────────────────────────────────────────────────────────
    def _context(self, file: str, plan: Any, completed: Any) -> str:
        plan_txt = plan if isinstance(plan, str) else str(plan)
        done = ", ".join(completed) if isinstance(completed, (list, tuple)) else str(completed)
        return (f"FILE: {file}\n\nPLAN.md (FILE SPEC + completed interfaces):\n"
                f"{plan_txt}\n\nALREADY-COMPLETED FILES: {done}")

    def _span(self, name: str, file: str, detail: str) -> None:
        """Best-effort observability span — never required, never raises."""
        try:
            from lib.inference import otel_emit  # type: ignore
            otel_emit.span(name, {"file": file, "detail": detail}, "ok")
        except Exception:
            pass

    def reset_task(self) -> None:
        """Call at task start to reset the per-task counters."""
        self._task_calls = 0
        self._file_calls.clear()
