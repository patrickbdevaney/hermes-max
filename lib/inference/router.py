"""The role router — the single entry the harness calls.

``run_role(role, messages, ...)`` walks the active mode's chain for that role and
returns the first rung that is present (key set), under the spend ceiling, and has
rate-bucket headroom. It records every success to the ledger and updates the
bucket tracker from response headers. All rungs exhausted → {ok: False,
proceed_local: True}. It NEVER raises into the harness.

The MCP layer imports only this. It never names a provider — it asks for a ROLE.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

from . import adapters, buckets, config, ledger, roles

# Optional: surface each LLM call in the live activity stream (cockpit + UI). Guarded
# so lib.inference stays importable/testable without the livelog present.
try:
    from lib import livelog as _livelog  # type: ignore
except Exception:  # noqa: BLE001
    _livelog = None  # type: ignore

# Spend ceiling → admitted provider tiers (mirror of roles._CEILING_TIERS).
_CEILING_TIERS = {
    "local":    {"local"},
    "free":     {"local", "free"},
    "full":     {"local", "free", "paid"},
    "frontier": {"local", "free", "paid", "frontier"},
}


def _apply_thinking(provider: str, kind: str, budget: int,
                    messages: list[dict[str, str]]
                    ) -> tuple[Optional[dict[str, Any]], list[dict[str, str]]]:
    """Translate a role's thinking budget into a provider-appropriate request shape
    (Fix 3). Returns (extra_body, messages). Native reasoning params are sent ONLY to
    providers known to accept them (OpenRouter `reasoning`, Anthropic `thinking`); for
    everyone else we fall back to a system instruction so an unknown field never 400s
    a provider (e.g. local vLLM)."""
    if budget <= 0:
        return None, messages
    if provider == "openrouter":
        return {"reasoning": {"max_tokens": budget}}, messages
    if kind == "anthropic":
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}, messages
    # non-native: instruct via a prepended system message (don't mutate the caller's list)
    instr = {"role": "system",
             "content": f"Think step by step, using up to {budget} tokens of "
                        "reasoning, before giving your final answer."}
    return None, [instr] + messages

# Injectable for offline tests: a callable with adapters.call's signature.
_CALL: Callable[..., dict[str, Any]] = adapters.call


def set_caller(fn: Optional[Callable[..., dict[str, Any]]]) -> None:
    """Override the wire adapter (tests). Pass None to restore the real one."""
    global _CALL
    _CALL = fn or adapters.call


def run_role(role: str, messages: list[dict[str, str]], *, max_tokens: int = 2048,
             mode: Optional[str] = None, est_tokens: Optional[int] = None,
             env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Resolve and execute a role. Returns:

        {ok, text, provider, model, model_id, usage, cost_usd, proceed_local,
         mode, role, fell}

    On total exhaustion: ok=False, proceed_local=True (never raises)."""
    env = config._effective_env(env)
    mode_name = mode or roles.active_mode_name(env)
    admitted = _CEILING_TIERS.get(roles.ceiling(mode_name, env), {"local", "free", "paid"})
    present = config.present_providers(env)
    est = est_tokens if est_tokens is not None else _estimate(messages, max_tokens)

    fell: list[dict[str, str]] = []
    for provider, model_key in roles.chain_for(role, mode_name):
        spec = config.resolve_model(provider, model_key)
        if spec is None:
            fell.append({"rung": f"{provider}.{model_key}", "why": "model undefined"})
            continue
        model_id = spec.get("id", model_key)
        if provider not in present:
            fell.append({"rung": f"{provider}.{model_key}", "why": "key absent"})
            continue
        if config.tier(provider) not in admitted:
            fell.append({"rung": f"{provider}.{model_key}", "why": "above ceiling"})
            continue
        if not buckets.has_headroom(provider, model_key, est):
            fell.append({"rung": f"{provider}.{model_key}", "why": "no bucket headroom"})
            continue

        kind = config.kind(provider)
        budget = roles.thinking_budget(role, mode_name)
        extra_body, call_msgs = _apply_thinking(provider, kind, budget, messages)

        # Surface the LLM call in the live activity stream (cockpit + UI). The
        # planner/synth calls flow through here, so this is where "LLM·plan kimi …
        # thinking: N" lines come from. Guarded — never breaks the call.
        if _livelog is not None:
            try:
                _livelog.tool_start(f"LLM·{role}", server=provider,
                                    inp={"model": model_id, "thinking_budget": budget})
            except Exception:  # noqa: BLE001
                pass

        t0 = time.time()
        res = _CALL(kind, config.base_url(provider),
                    config.api_key(provider, env), model_id, call_msgs,
                    max_tokens=max_tokens, extra_body=extra_body)
        wall_ms = int((time.time() - t0) * 1000)
        buckets.note_request(provider, model_key,
                             res.get("in_tok", 0) + res.get("out_tok", 0))
        buckets.update_from_headers(provider, model_key, res.get("headers", {}))

        if not res.get("ok"):
            if _livelog is not None:
                try:
                    _livelog.tool_fail(f"LLM·{role}",
                                       reason=res.get("error") or f"status {res.get('status')}",
                                       secs=wall_ms / 1000)
                except Exception:  # noqa: BLE001
                    pass
            if res.get("status") in (429, 413):
                buckets.note_429(provider, model_key,
                                 res.get("headers", {}).get("retry-after"))
            fell.append({"rung": f"{provider}.{model_key}",
                         "why": res.get("error") or f"status {res.get('status')}"})
            continue

        thinking_tok = int(res.get("thinking_tok", 0) or 0)
        if _livelog is not None:
            try:
                _livelog.tool_ok(f"LLM·{role}", secs=wall_ms / 1000,
                                 ret={"model": model_id, "in_tok": res["in_tok"],
                                      "out_tok": res["out_tok"], "thinking_tok": thinking_tok})
            except Exception:  # noqa: BLE001
                pass

        usd = config.cost_usd(provider, model_key, res["in_tok"], res["out_tok"],
                              res.get("cached_tok", 0))
        ledger.record(role=role, provider=provider, model=model_id,
                      in_tok=res["in_tok"], out_tok=res["out_tok"],
                      cached_tok=res.get("cached_tok", 0), cost_usd=usd,
                      wall_ms=wall_ms, mode=mode_name, thinking_tok=thinking_tok,
                      rate_headers=res.get("headers", {}))
        return {
            "ok": True, "text": res["text"], "provider": provider,
            "model": model_key, "model_id": model_id,
            "usage": {"in_tok": res["in_tok"], "out_tok": res["out_tok"],
                      "cached_tok": res.get("cached_tok", 0)},
            "cost_usd": usd, "proceed_local": False, "mode": mode_name,
            "role": role, "fell": fell,
        }

    # ── catch-all: every named rung gone → the default gateway (OpenRouter) ──────
    gw = config.get_default_gateway()
    if gw and config.gateway_present(env) and config.gateway_tier() in admitted:
        model_id = gw.get("default_model") or ""
        if model_id:
            t0 = time.time()
            res = _CALL(gw.get("kind", "openai_compatible"), gw.get("base_url"),
                        (env.get(gw.get("api_key_env", "")) or None), model_id,
                        messages, max_tokens=max_tokens)
            if res.get("ok"):
                ledger.record(role=role, provider="default_gateway", model=model_id,
                              in_tok=res["in_tok"], out_tok=res["out_tok"],
                              cached_tok=res.get("cached_tok", 0), cost_usd=0.0,
                              wall_ms=int((time.time() - t0) * 1000), mode=mode_name,
                              rate_headers=res.get("headers", {}))
                return {"ok": True, "text": res["text"], "provider": "default_gateway",
                        "model": "default", "model_id": model_id,
                        "usage": {"in_tok": res["in_tok"], "out_tok": res["out_tok"],
                                  "cached_tok": res.get("cached_tok", 0)},
                        "cost_usd": 0.0, "proceed_local": False, "mode": mode_name,
                        "role": role, "fell": fell}
            fell.append({"rung": "default_gateway", "why": res.get("error") or f"status {res.get('status')}"})

    return {"ok": False, "text": "", "provider": None, "model": None,
            "model_id": None, "usage": {}, "cost_usd": 0.0, "proceed_local": True,
            "mode": mode_name, "role": role, "fell": fell}


def _estimate(messages: list[dict[str, str]], max_tokens: int) -> int:
    """Rough token estimate for the bucket pre-check (~4 chars/token + output)."""
    chars = sum(len(m.get("content", "")) for m in messages)
    return chars // 4 + max_tokens
