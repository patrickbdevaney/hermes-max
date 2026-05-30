"""Conductor provider registry — PURE DATA + an optional config overlay.

This is piece (a) of the three-piece router (registry / resolver / executor). It
holds NO logic beyond loading an optional override file: adding a provider is ONE
entry here + ONE env var; nothing else changes.

Two hard rules, both structural:
  • Keys = PRESENCE ONLY. An env var ENABLES a rung; it never sets order. Order
    lives in ROLE_CHAINS (defaults below) or conductor.yaml (operator override).
  • US-hosted-first by construction. US hosts (DeepInfra/Fireworks/Together)
    sit ABOVE direct-provider-hosted DeepSeek-direct and SG-hosted Moonshot in the synth
    chain by default, so a present DeepInfra key is always preferred.

Model IDs below were verified live against each provider's /models on 2026-05-30.
conductor.yaml (optional) overrides per-role ORDER and per-rung MODEL strings only.
"""

from __future__ import annotations

import os
from typing import Any

# ── (a) PROVIDER REGISTRY — pure data. One entry per provider. ────────────────
# Fields: base_url, env_key_name, openai_compatible, models{steer,synth,draft},
# max_ctx, rpm, rpd, tpd (None = no documented hard limit / paid headroom),
# billing_region, trains_on_data, and price{role:{in,out}} per 1M tokens for the
# cost ledger (free tiers are 0/0).
PROVIDERS: dict[str, dict[str, Any]] = {
    # ── paid, US, no-train — the synth/steer default ─────────────────────────
    "deepinfra": {
        "base_url": "https://api.deepinfra.com/v1/openai",
        "env_key_name": "DEEPINFRA_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "deepseek-ai/DeepSeek-V4-Flash",
                   "synth": "deepseek-ai/DeepSeek-V4-Pro",
                   "draft": "deepseek-ai/DeepSeek-V4-Flash"},
        "max_ctx": 1_000_000, "rpm": None, "rpd": None, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"steer": {"in": 0.10, "out": 0.20},
                  "synth": {"in": 1.30, "out": 2.60},
                  "draft": {"in": 0.10, "out": 0.20}},
    },
    # ── US fallbacks for synth (no key for THIS operator; models overridable) ─
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "env_key_name": "FIREWORKS_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "accounts/fireworks/models/deepseek-v3p1",
                   "synth": "accounts/fireworks/models/deepseek-v3p1",
                   "draft": "accounts/fireworks/models/deepseek-v3p1"},
        "max_ctx": 160_000, "rpm": None, "rpd": None, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 0.90, "out": 0.90}},
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "env_key_name": "TOGETHER_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "deepseek-ai/DeepSeek-V3",
                   "synth": "deepseek-ai/DeepSeek-V3",
                   "draft": "deepseek-ai/DeepSeek-V3"},
        "max_ctx": 131_072, "rpm": None, "rpd": None, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 1.25, "out": 1.25}},
    },
    # ── opt-in non-US synth rungs — BELOW the US hosts by design ──────────────
    "deepseek": {  # direct-provider-hosted, direct provider terms. For THIS operator the account is unfunded.
        "base_url": "https://api.deepseek.com/v1",
        "env_key_name": "DEEPSEEK_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "deepseek-v4-flash",
                   "synth": "deepseek-v4-pro",
                   "draft": "deepseek-v4-flash"},
        "max_ctx": 1_000_000, "rpm": None, "rpd": None, "tpd": None,
        "billing_region": "CN", "trains_on_data": True,
        "price": {"steer": {"in": 0.028, "out": 0.042},
                  "synth": {"in": 0.28, "out": 0.42}},
    },
    "moonshot": {  # Singapore entity; Kimi. Long-horizon strength wasted on stateless synth.
        "base_url": "https://api.moonshot.ai/v1",
        "env_key_name": "MOONSHOT_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "kimi-k2.6", "synth": "kimi-k2.6", "draft": "kimi-k2.6"},
        "max_ctx": 256_000, "rpm": None, "rpd": None, "tpd": None,
        "billing_region": "SG", "trains_on_data": False,
        "price": {"synth": {"in": 0.60, "out": 2.50}},
    },
    # ── free, US — steer fallbacks + the parallel_draft pool ──────────────────
    "cerebras": {  # free preview; rate-limited; can change.
        "base_url": "https://api.cerebras.ai/v1",
        "env_key_name": "CEREBRAS_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "zai-glm-4.7", "synth": "zai-glm-4.7", "draft": "zai-glm-4.7"},
        "max_ctx": 64_000, "rpm": 5, "rpd": 2_400, "tpd": 1_000_000,
        "billing_region": "US", "trains_on_data": False,
        "price": {"steer": {"in": 0.0, "out": 0.0}, "draft": {"in": 0.0, "out": 0.0}},
    },
    "groq": {  # free; faster, looser limits than Cerebras.
        "base_url": "https://api.groq.com/openai/v1",
        "env_key_name": "GROQ_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "qwen/qwen3-32b", "synth": "openai/gpt-oss-120b",
                   "draft": "openai/gpt-oss-120b"},
        "max_ctx": 131_072, "rpm": 30, "rpd": 1_000, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"steer": {"in": 0.0, "out": 0.0}, "draft": {"in": 0.0, "out": 0.0}},
    },
    "gemini": {  # AI Studio OpenAI-compat; ~as low as 20 RPD on free accounts (verify your own console) — last-resort steer.
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key_name": "GEMINI_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "gemini-2.5-flash", "synth": "gemini-2.5-flash",
                   "draft": "gemini-2.5-flash"},
        "max_ctx": 1_000_000, "rpm": None, "rpd": 20, "tpd": None,
        "billing_region": "US", "trains_on_data": True,
        "price": {"steer": {"in": 0.0, "out": 0.0}},
    },
    # ── the escalate rung — Opus (no key for THIS operator => role OFF) ───────
    "anthropic": {  # OpenAI-compat layer at /v1/chat/completions.
        "base_url": "https://api.anthropic.com/v1",
        "env_key_name": "ANTHROPIC_API_KEY",
        "openai_compatible": True,
        "models": {"synth": "claude-opus-4-8", "escalate": "claude-opus-4-8"},
        "max_ctx": 200_000, "rpm": None, "rpd": None, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 15.0, "out": 75.0},
                  "escalate": {"in": 15.0, "out": 75.0}},
    },
}

# ── DEFAULT ROLE CHAINS (ordered; first PRESENT rung wins, fall through) ──────
#   synth: US-first, then opt-in non-US, then Opus at the top of the ladder.
#   steer: cheap-reliable-first — paid V4-Flash (hundredths of a cent, 1M ctx,
#          cache, reliable) BEFORE the fragile free tiers (corrected per the
#          operator's own pricing). Cerebras/Groq/Gemini are deprioritized free
#          fallbacks for when V4-Flash is absent/over-budget.
DEFAULT_ROLE_CHAINS: dict[str, list[str]] = {
    "synth": ["deepinfra", "fireworks", "together", "deepseek", "moonshot", "anthropic"],
    "steer": ["deepinfra", "cerebras", "groq", "gemini"],
    "escalate": ["anthropic"],
}

# ── DEFAULT parallel_draft POOL (UNORDERED; fan out for cross-family diversity)─
# Each entry pins a (provider, model) so one provider can contribute several
# families (Cerebras GLM + gpt-oss; Groq gpt-oss + qwen3 + llama-4). The paid
# DeepSeek-V4-Flash anchor raises the pool ceiling without a backend swap.
DEFAULT_DRAFT_POOL: list[dict[str, str]] = [
    {"provider": "cerebras", "model": "zai-glm-4.7"},
    {"provider": "cerebras", "model": "gpt-oss-120b"},
    {"provider": "groq", "model": "openai/gpt-oss-120b"},
    {"provider": "groq", "model": "qwen/qwen3-32b"},
    {"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
    {"provider": "deepinfra", "model": "deepseek-ai/DeepSeek-V4-Flash"},
]

# ── default caps (USD + draft fan-out) — overridable by env then conductor.yaml ─
DEFAULT_CAPS: dict[str, float] = {
    "usd_daily": float(os.environ.get("CONDUCTOR_USD_CAP_DAILY", "1.0")),
    "usd_monthly": float(os.environ.get("CONDUCTOR_USD_CAP_MONTHLY", "5.0")),
    "draft_max_n": float(os.environ.get("CONDUCTOR_DRAFT_MAX_N", "5")),
}

CONDUCTOR_YAML = os.path.expanduser(
    os.environ.get("CONDUCTOR_CONFIG", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "conductor.yaml"))
)


# ── stdlib-only conductor.yaml parser (no PyYAML needed on a fresh machine) ───
# Supports exactly the conductor.yaml schema we ship: top-level `roles:`,
# `draft_pool:`, `models:`, `caps:`; 2-space nesting; inline [a, b] lists; inline
# {k: v} maps; and `key: value` scalars. Anything it can't parse is ignored
# (defaults win) — the file is OPTIONAL, so a parse miss must never break startup.
def _strip(line: str) -> str:
    i = line.find("#")
    return (line[:i] if i >= 0 else line).rstrip()


def _inline_list(v: str) -> list[str]:
    return [x.strip() for x in v.strip().strip("[]").split(",") if x.strip()]


def _inline_map(v: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in v.strip().strip("{}").split(","):
        if ":" in pair:
            k, val = pair.split(":", 1)
            out[k.strip()] = val.strip()
    return out


def _parse_conductor_yaml(text: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    section: str | None = None
    subkey: str | None = None
    for raw in text.splitlines():
        line = _strip(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 0:
            if body.endswith(":"):
                section = body[:-1].strip()
                subkey = None
                if section in ("roles", "models", "caps"):
                    cfg[section] = {}
                elif section == "draft_pool":
                    cfg[section] = []
            continue
        if section == "roles" and indent == 2 and ":" in body:
            k, v = body.split(":", 1)
            cfg["roles"][k.strip()] = _inline_list(v)
        elif section == "caps" and indent == 2 and ":" in body:
            k, v = body.split(":", 1)
            try:
                cfg["caps"][k.strip()] = float(v.strip())
            except ValueError:
                pass
        elif section == "draft_pool" and body.startswith("-"):
            m = _inline_map(body[1:].strip())
            if m.get("provider") and m.get("model"):
                cfg["draft_pool"].append({"provider": m["provider"], "model": m["model"]})
        elif section == "models":
            if indent == 2 and body.endswith(":"):
                subkey = body[:-1].strip()
                cfg["models"].setdefault(subkey, {})
            elif indent >= 4 and subkey and ":" in body:
                k, v = body.split(":", 1)
                cfg["models"][subkey][k.strip()] = v.strip()
    return cfg


def load_config() -> dict[str, Any]:
    """Merge defaults < conductor.yaml (if present). Env supplies KEYS only,
    never order. Returns {role_chains, draft_pool, caps, providers} ready to use.

    Precedence (documented): hardcoded defaults < conductor.yaml overrides. A
    missing or unparseable file silently yields pure defaults (never raises)."""
    role_chains = {k: list(v) for k, v in DEFAULT_ROLE_CHAINS.items()}
    draft_pool = [dict(p) for p in DEFAULT_DRAFT_POOL]
    caps = dict(DEFAULT_CAPS)
    # deep-copy providers so a model override doesn't mutate module state
    providers = {pid: {**p, "models": dict(p["models"])} for pid, p in PROVIDERS.items()}

    overrode = False
    try:
        with open(CONDUCTOR_YAML) as f:
            ov = _parse_conductor_yaml(f.read())
        for role, chain in (ov.get("roles") or {}).items():
            if chain:
                role_chains[role] = chain
                overrode = True
        if ov.get("draft_pool"):
            draft_pool = ov["draft_pool"]
            overrode = True
        for k, v in (ov.get("caps") or {}).items():
            caps[k] = v
            overrode = True
        for pid, models in (ov.get("models") or {}).items():
            if pid in providers:
                providers[pid]["models"].update(models)
                overrode = True
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 - optional file; defaults win, never break startup
        pass

    return {"role_chains": role_chains, "draft_pool": draft_pool, "caps": caps,
            "providers": providers, "config_applied": overrode}
