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
    "cerebras": {  # free preview; the PREFERRED free draft source (30K TPM, fast).
        "base_url": "https://api.cerebras.ai/v1",
        "env_key_name": "CEREBRAS_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "zai-glm-4.7", "synth": "zai-glm-4.7", "draft": "zai-glm-4.7"},
        "max_ctx": 64_000, "rpm": 5, "rpd": 2_400, "tpd": 1_000_000,
        # TPM is the binding free-tier limit. Cerebras' ~30K TPM comfortably fits a
        # full compact draft brief, so no input cap is needed here.
        "tpm": 30_000, "draft_input_cap_tokens": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"steer": {"in": 0.0, "out": 0.0}, "draft": {"in": 0.0, "out": 0.0}},
    },
    "groq": {  # free; SECONDARY draft source — extremely tight per-MODEL TPM.
        "base_url": "https://api.groq.com/openai/v1",
        "env_key_name": "GROQ_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "qwen/qwen3-32b", "synth": "openai/gpt-oss-120b",
                   "draft": "openai/gpt-oss-120b"},
        "max_ctx": 131_072, "rpm": 30, "rpd": 1_000, "tpd": None,
        # Groq free-tier TPM is per-MODEL and tiny: a single 6K-token brief can eat
        # the whole per-minute budget. Track per model; cap draft INPUT at ~3.5K so
        # output fits inside the TPM window. Verified live: 429 after one full-brief
        # call, 413 Payload-Too-Large on qwen3-32b.
        "tpm": 8_000,
        "model_tpm": {"openai/gpt-oss-120b": 8_000, "qwen/qwen3-32b": 6_000,
                      "meta-llama/llama-4-scout-17b-16e-instruct": 8_000},
        "draft_input_cap_tokens": 3_500,
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
    # ── the FRONTIER escalate rung — Opus 4.8 (eligible ONLY in --frontier mode + ANTHROPIC_API_KEY) ─
    "anthropic": {  # OpenAI-compat layer at /v1/chat/completions.
        "base_url": "https://api.anthropic.com/v1",
        "env_key_name": "ANTHROPIC_API_KEY",
        "openai_compatible": True,
        # Opus is the FRONTIER escalate rung ONLY — deliberately NOT a synth rung,
        # so it can never fire as a synth fallthrough; the three-gated frontier
        # flow (frontier_core) is the only path to it.
        "models": {"escalate": "claude-opus-4-8"},
        "max_ctx": 1_000_000, "rpm": None, "rpd": None, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        # Opus 4.8 REGULAR pricing (verified May 2026): $5/M in, $25/M out. (Fast
        # mode $10/$50 is intentionally NOT used — cost, not latency, is the bound.)
        "price": {"escalate": {"in": 5.0, "out": 25.0}},
    },
    # ── free, US — OpenRouter Kimi-K2.6:free: the $0 PLANNER (synth) rung ──────
    # The conductor's synth chain was all paid, so in `free` posture the planner
    # never fired (the agent planned locally on the executor). This is the free,
    # present-key rung that makes "Kimi plans, the local model executes" actually
    # happen at $0. Kimi K2.6 is a strong reasoner with a 262K window — no chunking.
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key_name": "OPENROUTER_API_KEY",
        "openai_compatible": True,
        "models": {"steer": "moonshotai/kimi-k2.6:free",
                   "synth": "moonshotai/kimi-k2.6:free",
                   "draft": "moonshotai/kimi-k2.6:free"},
        "max_ctx": 262_144, "rpm": None, "rpd": 1_000, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"steer": {"in": 0.0, "out": 0.0},
                  "synth": {"in": 0.0, "out": 0.0},
                  "draft": {"in": 0.0, "out": 0.0}},
    },
    # ── the FREE-TIER PLANNER CASCADE — 5 more frontier-size $0 OpenRouter rungs ─
    # Each is its own registry rung (independent upstream model pool), so a 429 on
    # one falls through to the next BEFORE any paid token is spent. All verified to
    # exist as genuine `:free` ids in the OpenRouter catalog on 2026-06-01. (The
    # spec's minimax-m2.5:free / deepseek-v4-flash:free / deepseek-r1-0528:free are
    # NOT free on OpenRouter — substituted with real free frontier models.)
    "openrouter_qwen3coder": {  # 1M ctx, strongest free coding model
        "base_url": "https://openrouter.ai/api/v1", "env_key_name": "OPENROUTER_API_KEY",
        "openai_compatible": True,
        "models": {"synth": "qwen/qwen3-coder:free", "steer": "qwen/qwen3-coder:free",
                   "draft": "qwen/qwen3-coder:free"},
        "max_ctx": 1_048_576, "rpm": None, "rpd": 1_000, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 0.0, "out": 0.0}},
    },
    "openrouter_nemotron": {  # 120B, 1M ctx, 60% SWE-Bench, open weights
        "base_url": "https://openrouter.ai/api/v1", "env_key_name": "OPENROUTER_API_KEY",
        "openai_compatible": True,
        "models": {"synth": "nvidia/nemotron-3-super-120b-a12b:free",
                   "steer": "nvidia/nemotron-3-super-120b-a12b:free",
                   "draft": "nvidia/nemotron-3-super-120b-a12b:free"},
        "max_ctx": 1_000_000, "rpm": None, "rpd": 1_000, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 0.0, "out": 0.0}},
    },
    "openrouter_qwen3next": {  # 80B-A3B, 262K ctx
        "base_url": "https://openrouter.ai/api/v1", "env_key_name": "OPENROUTER_API_KEY",
        "openai_compatible": True,
        "models": {"synth": "qwen/qwen3-next-80b-a3b-instruct:free",
                   "steer": "qwen/qwen3-next-80b-a3b-instruct:free",
                   "draft": "qwen/qwen3-next-80b-a3b-instruct:free"},
        "max_ctx": 262_144, "rpm": None, "rpd": 1_000, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 0.0, "out": 0.0}},
    },
    "openrouter_glm": {  # GLM-4.5-Air, 131K ctx, strong reasoner
        "base_url": "https://openrouter.ai/api/v1", "env_key_name": "OPENROUTER_API_KEY",
        "openai_compatible": True,
        "models": {"synth": "z-ai/glm-4.5-air:free", "steer": "z-ai/glm-4.5-air:free",
                   "draft": "z-ai/glm-4.5-air:free"},
        "max_ctx": 131_072, "rpm": None, "rpd": 1_000, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 0.0, "out": 0.0}},
    },
    "openrouter_gptoss": {  # gpt-oss-120b, 131K ctx, open reasoning
        "base_url": "https://openrouter.ai/api/v1", "env_key_name": "OPENROUTER_API_KEY",
        "openai_compatible": True,
        "models": {"synth": "openai/gpt-oss-120b:free", "steer": "openai/gpt-oss-120b:free",
                   "draft": "openai/gpt-oss-120b:free"},
        "max_ctx": 131_072, "rpm": None, "rpd": 1_000, "tpd": None,
        "billing_region": "US", "trains_on_data": False,
        "price": {"synth": {"in": 0.0, "out": 0.0}},
    },
}

# ── spend-TIER tags (for CONDUCTOR_MODE) — free-tier vs paid providers ────────
# CONDUCTOR_MODE is a HARD cap layered ON TOP of presence-gating (resolver.py):
#   local -> NO cloud at all; free -> only these free providers; full -> all.
# Independent of which keys are present (a present DeepInfra key is IGNORED in
# `free` mode). Derived once in load_config() so adding a provider needs only its
# id here if it's free; everything else defaults to "paid".
FREE_TIER_PROVIDERS: set[str] = {
    "cerebras", "groq", "gemini",
    "openrouter", "openrouter_qwen3coder", "openrouter_nemotron",
    "openrouter_qwen3next", "openrouter_glm", "openrouter_gptoss",
}
# FRONTIER-tier providers are eligible ONLY in CONDUCTOR_MODE=frontier (never in
# `full`). Opus 4.8 (anthropic) is the only one: this is what keeps the expensive
# rung opt-in by `--frontier` and never reachable via `--full` or a synth fallthrough.
FRONTIER_TIER_PROVIDERS: set[str] = {"anthropic"}

# ── DEFAULT ROLE CHAINS (ordered; first PRESENT rung wins, fall through) ──────
#   synth: US-first, then opt-in non-US, then Opus at the top of the ladder.
#   steer: cheap-reliable-first — paid V4-Flash (hundredths of a cent, 1M ctx,
#          cache, reliable) BEFORE the fragile free tiers (corrected per the
#          operator's own pricing). Cerebras/Groq/Gemini are deprioritized free
#          fallbacks for when V4-Flash is absent/over-budget.
DEFAULT_ROLE_CHAINS: dict[str, list[str]] = {
    # synth deliberately does NOT include anthropic — Opus is escalation-only, via
    # the three-gated frontier flow, never a synth fallthrough.
    # Free-tier planner cascade: six $0 frontier rungs (independent upstream pools)
    # tried in order before any paid token — then the funded V4-Pro fallbacks.
    "synth": ["openrouter", "openrouter_qwen3coder", "openrouter_nemotron",
              "openrouter_qwen3next", "openrouter_glm", "openrouter_gptoss",
              "deepinfra", "deepseek"],
    "steer": ["openrouter", "deepinfra", "cerebras", "groq", "gemini"],
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

# Runtime config lives in $HERMES_MAX_CONFIG_DIR (default ~/.hermes-max); the repo
# ships only config/conductor.example.yaml. CONDUCTOR_CONFIG overrides the path.
# Precedence: explicit env → <CONFIG_DIR>/conductor.yaml → shipped example. The file
# is OPTIONAL — absent everywhere, the hardcoded defaults win.
def _conductor_yaml() -> str:
    explicit = os.environ.get("CONDUCTOR_CONFIG")
    if explicit:
        return os.path.expanduser(explicit)
    cfg_dir = os.path.expanduser(os.environ.get("HERMES_MAX_CONFIG_DIR") or "~/.hermes-max")
    user = os.path.join(cfg_dir, "conductor.yaml")
    if os.path.exists(user):
        return user
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "config", "conductor.example.yaml")


CONDUCTOR_YAML = _conductor_yaml()


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
    # deep-copy providers so a model override doesn't mutate module state, and
    # tag each with its spend tier (free/paid) for the CONDUCTOR_MODE cap.
    providers = {pid: {**p, "models": dict(p["models"]),
                       "tier": ("free" if pid in FREE_TIER_PROVIDERS
                                else "frontier" if pid in FRONTIER_TIER_PROVIDERS
                                else "paid")}
                 for pid, p in PROVIDERS.items()}

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
