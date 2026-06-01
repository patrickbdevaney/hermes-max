"""roles.yaml + modes.yaml — the harness asks for a ROLE, a MODE picks the chain.

``roles.yaml`` holds the base role→chain map. ``modes.yaml`` holds named presets
that OVERRIDE the coding/research chains for a cost/quality posture. The active
mode name is persisted to ``~/.hermes-max/mode`` (written by ``hm mode <name>``)
and falls back to ``INFERENCE_MODE`` env, then the modes.yaml ``default``.

Each mode declares an ``inference_mode`` ceiling (local | free | full | frontier)
that the router enforces against provider tiers, so a mode is both a chain swap
AND a spend ceiling.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Optional

from . import config

try:
    import yaml  # type: ignore
    _HAVE_YAML = True
except Exception:
    _HAVE_YAML = False

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mode_file() -> str:
    """Read the env at use-time (not import) so overrides + tests are honored."""
    return os.path.expanduser(os.environ.get("HERMES_MODE_FILE", "~/.hermes-max/mode"))

# Spend ceiling → the set of provider tiers it admits.
_CEILING_TIERS = {
    "local":    {"local"},
    "free":     {"local", "free"},
    "full":     {"local", "free", "paid"},
    "frontier": {"local", "free", "paid", "frontier"},
}


def _yaml_path(name: str) -> str:
    user = os.path.expanduser(f"~/.hermes-max/{name}")
    return user if os.path.exists(user) else os.path.join(_REPO_ROOT, name)


@lru_cache(maxsize=4)
def _load_yaml_cached(path: str, mtime: float) -> dict[str, Any]:
    if not _HAVE_YAML:
        raise RuntimeError("PyYAML required to parse " + path)
    with open(path) as f:
        return yaml.safe_load(f.read()) or {}


def _load(name: str) -> dict[str, Any]:
    path = _yaml_path(name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return _load_yaml_cached(path, mtime)


def _roles() -> dict[str, Any]:
    return (_load("roles.yaml").get("roles") or {})


def _modes_doc() -> dict[str, Any]:
    return _load("modes.yaml")


def all_modes() -> list[str]:
    """Mode names in modes.yaml order (appeal order: free first)."""
    return list((_modes_doc().get("modes") or {}).keys())


def mode_meta(name: str) -> dict[str, Any]:
    m = (_modes_doc().get("modes") or {}).get(name) or {}
    return {
        "name": name,
        "requires_gpu": bool(m.get("requires_gpu", False)),
        "monthly_cost": m.get("monthly_cost", "?"),
        "inference_mode": m.get("inference_mode", "full"),
        "posture": " ".join((m.get("posture") or "").split()),
        "chains": m.get("chains") or {},
    }


def default_mode() -> str:
    return _modes_doc().get("default") or (all_modes()[0] if all_modes() else "free")


def active_mode_name(env: Optional[dict[str, str]] = None) -> str:
    """Resolve the active mode: ~/.hermes-max/mode file > INFERENCE_MODE env >
    modes.yaml default. An unknown value falls back to the default."""
    env = os.environ if env is None else env
    names = set(all_modes())
    # 1. persisted mode file
    try:
        with open(_mode_file()) as f:
            v = f.read().strip()
        if v in names:
            return v
    except OSError:
        pass
    # 2. env
    v = (env.get("INFERENCE_MODE") or "").strip()
    if v in names:
        return v
    # 3. default
    return default_mode()


def set_mode(name: str) -> dict[str, Any]:
    """Persist the active mode name. Returns {ok, mode, satisfiable, warnings}.
    Always switches (even if not fully satisfiable) but reports warnings."""
    if name not in set(all_modes()):
        return {"ok": False, "error": f"unknown mode '{name}'",
                "available": all_modes()}
    os.makedirs(os.path.dirname(_mode_file()), exist_ok=True)
    with open(_mode_file(), "w") as f:
        f.write(name + "\n")
    return {"ok": True, "mode": name, **satisfiability(name)}


def ceiling(mode_name: Optional[str] = None,
            env: Optional[dict[str, str]] = None) -> str:
    name = mode_name or active_mode_name(env)
    return mode_meta(name).get("inference_mode", "full")


def _split_rung(rung: str) -> tuple[str, str]:
    """'provider.model' → (provider, model). Tolerates dotted provider names by
    splitting on the LAST dot."""
    provider, _, model = rung.rpartition(".")
    return provider, model


def chain_for(role: str, mode_name: Optional[str] = None) -> list[tuple[str, str]]:
    """Ordered [(provider, model_key)] for a role under the active mode. A mode's
    `chains` override wins; otherwise the base roles.yaml chain is used."""
    name = mode_name or active_mode_name()
    overrides = mode_meta(name).get("chains") or {}
    if role in overrides:
        rungs = overrides[role] or []
    else:
        rungs = _roles().get(role) or []
    return [_split_rung(r) for r in rungs]


def satisfiability(mode_name: Optional[str] = None,
                   env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """For a mode: which coding/research roles are satisfiable (≥1 present rung
    under the ceiling), and warnings (requires_gpu but no local; empty chains)."""
    env = os.environ if env is None else env
    name = mode_name or active_mode_name(env)
    meta = mode_meta(name)
    allowed = _CEILING_TIERS.get(meta["inference_mode"], {"local", "free", "paid"})
    present = config.present_providers(env)

    roles_status: dict[str, Optional[str]] = {}
    coding = ["code_plan", "code_execute", "code_steer", "code_repair",
              "research_fanout", "research_synth"]
    for role in coding:
        first = None
        for prov, model in chain_for(role, name):
            if prov in present and config.tier(prov) in allowed:
                first = f"{prov}.{model}"
                break
        roles_status[role] = first

    warnings: list[str] = []
    if meta["requires_gpu"] and "local_vllm" not in present:
        warnings.append(
            f"mode '{name}' requires a local vLLM executor but none is configured "
            "(local_vllm absent). Stand vLLM up, or switch to --full (no GPU needed).")
    unsatisfied = [r for r, v in roles_status.items() if v is None]
    if unsatisfied:
        warnings.append("no present provider for: " + ", ".join(unsatisfied)
                        + " → those roles degrade to local.")
    return {"roles": roles_status, "warnings": warnings,
            "satisfiable": not unsatisfied}
