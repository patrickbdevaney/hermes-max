"""inference.yaml loader — the backend bazaar as DATA.

The SOLE place that knows which providers exist, their base URLs, model ids,
costs and rate limits. Everything else asks this module. A provider whose
``api_key_env`` is unset in the environment is treated as ABSENT (silently
skipped) — never an error. With nothing but ``local_vllm`` present the system is
fully local and free.

No provider SDKs are imported here or anywhere in lib/inference except the thin
HTTP adapters; this module is pure config.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Optional

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(obj: Any) -> Any:
    """Recursively expand ${VAR} / ${VAR:-default} in any string value, read from
    the live environment. Keeps endpoints out of the YAML literal — base URLs and
    the local model id come from env (VLLM_BASE_URL, DEEPINFRA_BASE_URL, ...)."""
    if isinstance(obj, str):
        def sub(m: "re.Match[str]") -> str:
            return os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else "")
        return _ENV_RE.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    return obj

try:                                     # PyYAML is the normal path …
    import yaml  # type: ignore
    _HAVE_YAML = True
except Exception:                        # … but never hard-fail on a bare machine.
    _HAVE_YAML = False

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _config_path() -> str:
    """Active config: ~/.hermes-max/inference.yaml if present, else the shipped
    example (which doubles as the recommended default constellation)."""
    explicit = os.environ.get("INFERENCE_CONFIG")
    if explicit and os.path.exists(os.path.expanduser(explicit)):
        return os.path.expanduser(explicit)
    user = os.path.expanduser("~/.hermes-max/inference.yaml")
    if os.path.exists(user):
        return user
    return os.path.join(_REPO_ROOT, "inference.example.yaml")


@lru_cache(maxsize=1)
def _raw_cached(path: str, mtime: float) -> dict[str, Any]:
    with open(path) as f:
        text = f.read()
    if _HAVE_YAML:
        return yaml.safe_load(text) or {}
    raise RuntimeError(
        "PyYAML is required to parse inference.yaml. Install it: pip install pyyaml")


_DOTENV_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


@lru_cache(maxsize=4)
def _dotenv_cached(path: str, mtime: float) -> dict[str, str]:
    """Parse NAME=value lines from a .env (inline ` # comment` stripped, quotes
    trimmed). hermes-max stores keys in .env, not the exported environment, so the
    fabric must consult it — mirrors `hm`'s own _key_present."""
    out: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = _DOTENV_RE.match(line)
                if not m:
                    continue
                val = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip('"').strip("'")
                if val:
                    out[m.group(1)] = val
    except OSError:
        pass
    return out


def _dotenv() -> dict[str, str]:
    path = os.environ.get("HMX_ENV_FILE") or os.path.join(_REPO_ROOT, ".env")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    return _dotenv_cached(path, mtime)


def _effective_env(env: Optional[dict[str, str]]) -> dict[str, str]:
    """Tests pass an explicit env (used verbatim). Real callers (env=None) get the
    .env file as a fallback under the live os.environ (live env wins)."""
    if env is not None:
        return env
    merged = dict(_dotenv())
    merged.update(os.environ)
    return merged


def raw() -> dict[str, Any]:
    """Full parsed inference.yaml with ${ENV} expanded against the LIVE environment
    (cached parse, expanded per-call so env changes are honored)."""
    path = _config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return _expand(_raw_cached(path, mtime))


def providers() -> dict[str, Any]:
    return (raw().get("providers") or {})


def get_provider(name: str) -> Optional[dict[str, Any]]:
    return providers().get(name)


def _key_present(provider: dict[str, Any], env: Optional[dict[str, str]] = None) -> bool:
    """A provider is present if its api_key_env is null (local) OR set+non-empty
    in the live env or the repo .env."""
    env = _effective_env(env)
    key_env = provider.get("api_key_env")
    if key_env in (None, "", "null"):
        return True                       # local / keyless rung
    return bool((env.get(key_env) or "").strip())


def present_providers(env: Optional[dict[str, str]] = None) -> set[str]:
    """Names of providers whose key is present (or keyless). Missing key → absent."""
    return {n for n, p in providers().items() if _key_present(p, env)}


def provider_present(name: str, env: Optional[dict[str, str]] = None) -> bool:
    p = get_provider(name)
    return bool(p) and _key_present(p, env)


def tier(name: str) -> str:
    """Classify a provider for the spend ceiling: local | free | paid | frontier."""
    p = get_provider(name) or {}
    if (p.get("privacy") == "local") or (p.get("api_key_env") in (None, "", "null")):
        return "local"
    if p.get("kind") == "anthropic":
        return "frontier"
    cost = p.get("cost") or {}
    if float(cost.get("in_per_mtok", 0) or 0) == 0.0 and float(cost.get("out_per_mtok", 0) or 0) == 0.0:
        return "free"
    return "paid"


def resolve_model(provider: str, model_key: str) -> Optional[dict[str, Any]]:
    """Return {id, ctx, ...} for provider.model_key, or None if undefined."""
    p = get_provider(provider) or {}
    return (p.get("models") or {}).get(model_key)


def base_url(provider: str) -> Optional[str]:
    p = get_provider(provider) or {}
    return p.get("base_url")


def kind(provider: str) -> str:
    p = get_provider(provider) or {}
    return p.get("kind", "openai_compatible")


def api_key(provider: str, env: Optional[dict[str, str]] = None) -> Optional[str]:
    env = _effective_env(env)
    p = get_provider(provider) or {}
    key_env = p.get("api_key_env")
    if key_env in (None, "", "null"):
        return None
    return env.get(key_env)


def _cost_block(provider: str, model_key: str) -> dict[str, float]:
    """Pick the right price block. A provider may ship a flash tier (`cost_flash`)
    for its cheaper/driver model; the primary `cost` block covers everything else."""
    p = get_provider(provider) or {}
    if model_key == "driver" and isinstance(p.get("cost_flash"), dict):
        return p["cost_flash"]
    return p.get("cost") or {}


def cost_usd(provider: str, model_key: str, in_tok: int, out_tok: int,
             cached_tok: int = 0) -> float:
    """USD for a call given token counts. Cached input is priced at the provider's
    cache-hit rate when reported; free providers return 0.0 (callers still record
    the real token counts)."""
    c = _cost_block(provider, model_key)
    in_rate = float(c.get("in_per_mtok", 0.0) or 0.0)
    out_rate = float(c.get("out_per_mtok", 0.0) or 0.0)
    cache_rate = float(c.get("cache_hit_in_per_mtok", in_rate) or 0.0)
    fresh_in = max(0, int(in_tok) - int(cached_tok))
    usd = (fresh_in * in_rate
           + int(cached_tok) * cache_rate
           + int(out_tok) * out_rate) / 1_000_000.0
    return round(usd, 6)


def limits_for(provider: str, model_id: str) -> dict[str, Any]:
    """Rate limits for a (provider, model_id): per-model buckets win over the
    provider-wide `limits` block. Empty dict = unmetered (e.g. local)."""
    p = get_provider(provider) or {}
    per_model = p.get("limits_per_model") or {}
    if model_id in per_model:
        return per_model[model_id]
    return p.get("limits") or {}
