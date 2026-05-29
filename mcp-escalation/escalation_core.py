"""Thin cloud router for escalating genuinely-hard, well-scoped subproblems.

Two non-negotiables, both enforced HERE in the server (not in a prompt):
  1. Default OFF. ESCALATION_ENABLED must be explicitly "true" to route anything.
  2. Hard daily USD cap. Spend is tracked in a state file and reset each day;
     once today's spend reaches the cap, escalate refuses — and a per-call
     max_tokens bounds any single call so it can't blow the cap in one shot.

Tier-3 (Opus / Claude Code) is intentionally NOT routable here — those tier
names are rejected — to avoid auth collisions with the laptop's Claude Code.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

import httpx

ENABLED = os.environ.get("ESCALATION_ENABLED", "false").strip().lower() == "true"
DAILY_USD_CAP = float(os.environ.get("ESCALATION_DAILY_USD_CAP", "1.00"))
MAX_TOKENS = int(os.environ.get("ESCALATION_MAX_TOKENS", "2048"))
TIMEOUT = float(os.environ.get("ESCALATION_TIMEOUT", "120"))
STATE_PATH = os.path.expanduser(
    os.environ.get("ESCALATION_STATE_PATH", "~/.hermes-max/escalation/spend.json")
)

# Tier-3 must never be routed through this server.
FORBIDDEN_TIERS = {"opus", "claude", "claude-code", "claude_code", "tier3", "tier-3", "tier_3"}

_lock = threading.Lock()


def _tiers() -> dict[str, dict[str, Any]]:
    """Build the tier map from env. A tier is available only if its base_url is set."""
    tiers: dict[str, dict[str, Any]] = {}
    if os.environ.get("ESCALATION_BASE_URL"):
        tiers["cheap"] = {
            "base_url": os.environ["ESCALATION_BASE_URL"].rstrip("/"),
            "api_key": os.environ.get("ESCALATION_API_KEY", ""),
            "model": os.environ.get("ESCALATION_MODEL", "deepseek-v4-flash"),
            "price_in": float(os.environ.get("ESCALATION_PRICE_IN", "0.14")),
            "price_out": float(os.environ.get("ESCALATION_PRICE_OUT", "0.28")),
        }
    if os.environ.get("ESCALATION_LONG_BASE_URL"):
        tiers["long"] = {
            "base_url": os.environ["ESCALATION_LONG_BASE_URL"].rstrip("/"),
            "api_key": os.environ.get("ESCALATION_LONG_API_KEY", ""),
            "model": os.environ.get("ESCALATION_LONG_MODEL", "kimi-k2.6"),
            "price_in": float(os.environ.get("ESCALATION_LONG_PRICE_IN", "0.60")),
            "price_out": float(os.environ.get("ESCALATION_LONG_PRICE_OUT", "2.50")),
        }
    return tiers


def _load_state() -> dict[str, Any]:
    today = date.today().isoformat()
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
        if st.get("date") != today:
            st = {"date": today, "spend_usd": 0.0, "calls": 0}
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        st = {"date": today, "spend_usd": 0.0, "calls": 0}
    return st


def _save_state(st: dict[str, Any]) -> None:
    Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def _post_chat(tier_cfg: dict[str, Any], task: str, max_tokens: int) -> dict[str, Any]:
    """Real OpenAI-compatible call. This is the seam the smoke test stubs."""
    headers = {"Content-Type": "application/json"}
    if tier_cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {tier_cfg['api_key']}"
    payload = {
        "model": tier_cfg["model"],
        "messages": [{"role": "user", "content": task}],
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(f"{tier_cfg['base_url']}/chat/completions",
                           json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _cost(tier_cfg: dict[str, Any], usage: dict[str, Any]) -> float:
    pin = usage.get("prompt_tokens", 0)
    pout = usage.get("completion_tokens", 0)
    return pin / 1e6 * tier_cfg["price_in"] + pout / 1e6 * tier_cfg["price_out"]


def escalate(task: str, tier: str = "cheap") -> dict[str, Any]:
    if not ENABLED:
        return {"ok": False, "disabled": True,
                "reason": "escalation is OFF by default; set ESCALATION_ENABLED=true to enable"}

    tier = (tier or "cheap").strip().lower()
    if tier in FORBIDDEN_TIERS:
        return {"ok": False, "error": f"tier '{tier}' is not routable here (Tier-3 stays on Claude Code)"}

    tiers = _tiers()
    if tier not in tiers:
        return {"ok": False, "error": f"tier '{tier}' unavailable",
                "available_tiers": sorted(tiers.keys())}
    cfg = tiers[tier]

    with _lock:
        st = _load_state()
        if st["spend_usd"] >= DAILY_USD_CAP:
            return {"ok": False, "cap_reached": True, "spend_usd": round(st["spend_usd"], 6),
                    "daily_cap_usd": DAILY_USD_CAP,
                    "reason": "daily escalation USD cap reached; falling back to local"}
        # Reserve nothing yet; do the call outside the lock would race the cap,
        # so we keep the call inside the lock — escalation is rare by design.
        try:
            data = _post_chat(cfg, task, MAX_TOKENS)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"escalation call failed: {e}"}

        usage = data.get("usage", {}) or {}
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            content = ""
        cost = _cost(cfg, usage)
        st["spend_usd"] += cost
        st["calls"] += 1
        _save_state(st)

    return {
        "ok": True,
        "tier": tier,
        "model": cfg["model"],
        "content": content,
        "usage": usage,
        "cost_usd": round(cost, 6),
        "spend_today_usd": round(st["spend_usd"], 6),
        "daily_cap_usd": DAILY_USD_CAP,
    }


def status() -> dict[str, Any]:
    st = _load_state()
    return {
        "enabled": ENABLED,
        "daily_cap_usd": DAILY_USD_CAP,
        "spend_today_usd": round(st["spend_usd"], 6),
        "calls_today": st["calls"],
        "max_tokens_per_call": MAX_TOKENS,
        "tiers_available": sorted(_tiers().keys()),
        "forbidden_tiers": sorted(FORBIDDEN_TIERS),
    }
