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
    shipped = os.path.join(_REPO_ROOT, "config", "inference.example.yaml")
    if os.path.exists(shipped):
        return shipped
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


def provider_present(name: str, env: Optional[dict[str, str]] = None) -> bool:
    """A provider is usable iff: its key is present (or keyless), its endpoint is set
    (when `base_url_env`), and — for a `discover_model` provider — the endpoint is
    reachable with a discoverable model. So local_vllm is present only when
    VLLM_BASE_URL is set AND /v1/models answers."""
    p = get_provider(name)
    if not p:
        return False
    key_env = p.get("api_key_env")
    if key_env not in (None, "", "null"):
        if not (_effective_env(env).get(key_env) or "").strip():
            return False
    if p.get("base_url_env") and not base_url(name, env):
        return False
    if p.get("discover_model"):
        mkey = next(iter((p.get("models") or {}).keys()), "driver")
        if resolve_model(name, mkey) is None:
            return False
    return True


def present_providers(env: Optional[dict[str, str]] = None) -> set[str]:
    """Names of providers that are usable right now (key + endpoint + reachability)."""
    return {n for n in providers() if provider_present(n, env)}


# ── default gateway (the catch-all cloud fallback) ────────────────────────────
def get_default_gateway() -> Optional[dict[str, Any]]:
    return raw().get("default_gateway")


def gateway_present(env: Optional[dict[str, str]] = None) -> bool:
    gw = get_default_gateway()
    if not gw:
        return False
    key_env = gw.get("api_key_env")
    if key_env in (None, "", "null"):
        return True
    return bool((_effective_env(env).get(key_env) or "").strip())


def gateway_tier() -> str:
    gw = get_default_gateway() or {}
    cost = gw.get("cost") or {}
    if float(cost.get("in_per_mtok", 0) or 0) == 0.0 and float(cost.get("out_per_mtok", 0) or 0) == 0.0:
        return "free"
    return "paid"


def tier(name: str) -> str:
    """Classify a provider for the spend ceiling: local | free | paid | frontier."""
    p = get_provider(name) or {}
    if (p.get("privacy") == "local") or (p.get("api_key_env") in (None, "", "null")):
        return "local"
    if p.get("kind") == "anthropic":
        return "frontier"
    cost = p.get("cost") or {}
    # collect rates from a flat block OR a per-model map (cost.<slot>.in_per_mtok)
    rates: list[Any] = []
    nested = [v for v in cost.values() if isinstance(v, dict)] if isinstance(cost, dict) else []
    if nested:
        for v in nested:
            rates += [v.get("in_per_mtok", 0), v.get("out_per_mtok", 0)]
    else:
        rates = [cost.get("in_per_mtok", 0), cost.get("out_per_mtok", 0)]
    return "free" if all(float(x or 0) == 0.0 for x in rates) else "paid"


def resolve_model(provider: str, model_key: str) -> Optional[dict[str, Any]]:
    """Return {id, ctx, ...} for provider.model_key, or None if undefined. For a
    `discover_model` provider the id is filled at runtime from /v1/models; an
    unreachable endpoint yields None (the rung is treated as absent)."""
    p = get_provider(provider) or {}
    spec = (p.get("models") or {}).get(model_key)
    if spec is None:
        return None
    if p.get("discover_model"):
        mid = discover_model(provider)
        if not mid:
            return None
        return {**spec, "id": mid}
    return spec


def base_url(provider: str, env: Optional[dict[str, str]] = None) -> Optional[str]:
    """Endpoint for a provider. `base_url_env` (preferred) reads it from the env so
    the URL is never a YAML literal; otherwise the (env-expanded) `base_url` field."""
    p = get_provider(provider) or {}
    if p.get("base_url_env"):
        return (_effective_env(env).get(p["base_url_env"]) or "").strip() or None
    return p.get("base_url")


# ── local-model auto-discovery (GET ${VLLM_BASE_URL}/models → models[0].id) ────
_discover_cache: dict[str, Optional[str]] = {}
_discover_override: Optional[Any] = None       # tests inject: fn(base_url) -> id|None


def set_discover(fn: Optional[Any]) -> None:
    """Inject a discovery function for tests (fn(base_url)->id|None). None resets to
    the real HTTP discovery."""
    global _discover_override
    _discover_override = fn
    _discover_cache.clear()


def _http_discover(base: str) -> Optional[str]:
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/models", timeout=2) as r:
            data = json.load(r)
        models = data.get("data") or data.get("models") or []
        return (models[0].get("id") if models else None)
    except Exception:
        return None


def discover_model(provider: str, env: Optional[dict[str, str]] = None) -> Optional[str]:
    """Discover the served model id for a `discover_model: true` provider. Cached by
    base_url; returns None if the endpoint is unset/unreachable (→ tier absent)."""
    p = get_provider(provider) or {}
    if not p.get("discover_model"):
        return None
    base = base_url(provider, env)
    if not base:
        return None
    if _discover_override is not None:
        return _discover_override(base)
    if base not in _discover_cache:
        _discover_cache[base] = _http_discover(base)
    return _discover_cache[base]


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
    """Pick the right price block. Supports per-model cost maps
    (``cost: { planner: {...}, driver: {...} }``), the legacy ``cost_flash`` driver
    tier, and a flat ``cost`` block — in that order."""
    p = get_provider(provider) or {}
    c = p.get("cost") or {}
    if isinstance(c.get(model_key), dict):        # per-model: cost.<slot>
        return c[model_key]
    if model_key == "driver" and isinstance(p.get("cost_flash"), dict):
        return p["cost_flash"]                    # legacy flash tier
    return c                                       # flat (free providers, etc.)


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
