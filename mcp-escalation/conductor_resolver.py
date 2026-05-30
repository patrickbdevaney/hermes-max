"""Conductor presence resolver — piece (b) of the three-piece router.

ONE pure function family: given a role's chain (or the draft pool) and the set of
present API keys, return only the rungs whose key is set, IN ORDER. This is what
makes "use as many or as few keys as you have" literally true — and it is the
piece that is unit-tested across {0, 1, several} keys.

No network, no logic beyond `os.environ.get(env_key_name) is truthy`. A role is
ACTIVE iff it resolves to >=1 present rung; otherwise it is OFF and the caller
proceeds local-only.
"""

from __future__ import annotations

from typing import Any

# ── CONDUCTOR_MODE — a HARD spend-tier cap, read from the env dict that is ─────
# ALREADY passed to every resolver call (so no call-site threading is needed).
#   local -> use NO cloud provider at all (the driver runs fully local/sovereign)
#   free  -> permit only free-tier providers (Cerebras/Groq/Gemini)
#   full  -> permit every provider (the default; presence-gating alone governs)
# A provider is permitted iff its `tier` is in the mode's allowed set AND its key
# is present. Unknown/unset mode -> "full" (backward-compatible: pre-mode behaviour).
MODE_ALLOWED_TIERS: dict[str, set[str]] = {
    "local": set(),
    "free": {"free"},
    "full": {"free", "paid"},
}


def _present(env_key_name: str, env: dict[str, str]) -> bool:
    return bool((env.get(env_key_name) or "").strip())


def current_mode(env: dict[str, str]) -> str:
    """The active CONDUCTOR_MODE (lower-cased), defaulting to 'full'."""
    m = (env.get("CONDUCTOR_MODE") or "full").strip().lower()
    return m if m in MODE_ALLOWED_TIERS else "full"


def _mode_ok(prov: dict[str, Any], mode: str) -> bool:
    allowed = MODE_ALLOWED_TIERS.get(mode, MODE_ALLOWED_TIERS["full"])
    return prov.get("tier", "paid") in allowed


def resolve_chain(chain: list[str], providers: dict[str, dict[str, Any]],
                  env: dict[str, str]) -> list[str]:
    """Return the provider_ids in `chain`, in order, whose key is present, whose
    spend tier is permitted by the active CONDUCTOR_MODE, and which exist in the
    registry. Unknown provider_ids are skipped silently."""
    mode = current_mode(env)
    out: list[str] = []
    for pid in chain:
        prov = providers.get(pid)
        if prov and _mode_ok(prov, mode) and _present(prov.get("env_key_name", ""), env):
            out.append(pid)
    return out


def resolve_pool(pool: list[dict[str, str]], providers: dict[str, dict[str, Any]],
                 env: dict[str, str]) -> list[dict[str, str]]:
    """Return the (provider, model) pool entries whose provider key is present and
    whose spend tier is permitted by the active CONDUCTOR_MODE. Order is preserved
    but the pool is semantically UNORDERED (fanned out)."""
    mode = current_mode(env)
    out: list[dict[str, str]] = []
    for entry in pool:
        prov = providers.get(entry.get("provider", ""))
        if prov and _mode_ok(prov, mode) and _present(prov.get("env_key_name", ""), env):
            out.append(entry)
    return out


def role_active(chain: list[str], providers: dict[str, dict[str, Any]],
                env: dict[str, str]) -> bool:
    """A role is ON iff >=1 of its rungs has a present key."""
    return len(resolve_chain(chain, providers, env)) > 0


def active_roles(role_chains: dict[str, list[str]], providers: dict[str, dict[str, Any]],
                 env: dict[str, str]) -> dict[str, bool]:
    """Map {role: active?} — the at-a-glance 'what kind of help is on' view."""
    return {role: role_active(chain, providers, env) for role, chain in role_chains.items()}
