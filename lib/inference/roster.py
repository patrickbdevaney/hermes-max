"""Model roster validation — verify configured ids at `hm up` / `hm health`.

For each model slot in inference.yaml: check KNOWN_DEPRECATED, then probe the
provider's /models endpoint where available (cached 1h) and confirm the configured
id is live. WARN, never error — a deprecated or unconfirmed slot is a one-line
inference.yaml edit (no code change), so the system starts anyway.

Probing:
  • openai_compatible providers → GET {base_url}/models (groq base already ends
    /openai/v1, so {base}/models is correct)
  • local_vllm → reuse the cached discovery result (a discovered id IS live)
  • anthropic / cerebras → no /models endpoint → 'unconfirmed' (rely on
    KNOWN_DEPRECATED + a first-call 404)
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Optional

from . import config

# Populate as providers retire models, e.g. {"groq.synth_oss": "gpt-oss-120b pulled 2026-..."}.
KNOWN_DEPRECATED: dict[str, str] = {}

_CACHE = os.path.expanduser(
    os.environ.get("INFERENCE_ROSTER_CACHE", "~/.hermes-max/inference/roster_cache.json"))
_TTL = 3600.0

_probe_override: Optional[Any] = None           # tests inject: fn(base_url, api_key)->set[str]|None


def set_probe(fn: Optional[Any]) -> None:
    global _probe_override
    _probe_override = fn


def _load_cache() -> dict[str, Any]:
    try:
        with open(_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
        with open(_CACHE, "w") as f:
            json.dump(c, f)
    except Exception:
        pass


def _http_models(base: str, api_key: Optional[str]) -> Optional[set[str]]:
    import urllib.request
    try:
        req = urllib.request.Request(base.rstrip("/") + "/models")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.load(r)
        items = data.get("data") or data.get("models") or []
        ids = {m.get("id") for m in items if isinstance(m, dict) and m.get("id")}
        return ids or None
    except Exception:
        return None


def _probe(provider: str, env: Optional[dict[str, str]]) -> Optional[set[str]]:
    """Return the set of live model ids for a provider (cached 1h), or None if the
    provider has no probe-able /models endpoint or it was unreachable."""
    if config.kind(provider) == "anthropic":
        return None
    base = config.base_url(provider, env)
    if not base:
        return None
    if _probe_override is not None:
        return _probe_override(base, config.api_key(provider, env))
    cache = _load_cache()
    ent = cache.get(provider)
    now = time.time()
    if ent and (now - ent.get("ts", 0)) < _TTL:
        ids = ent.get("models")
        return set(ids) if ids else None
    ids = _http_models(base, config.api_key(provider, env))
    cache[provider] = {"ts": now, "models": sorted(ids) if ids else None}
    _save_cache(cache)
    return ids


def _slots(provider: str) -> dict[str, Any]:
    return (config.get_provider(provider) or {}).get("models") or {}


def validate(env: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    """One row per (provider, slot): {provider, slot, model_id, status, note}.
    status ∈ confirmed | deprecated | missing | unconfirmed | absent."""
    rows: list[dict[str, Any]] = []
    names = list(config.providers().keys())
    if config.get_default_gateway():
        names.append("default_gateway")

    for provider in names:
        gateway = provider == "default_gateway"
        present = config.gateway_present(env) if gateway else config.provider_present(provider, env)
        slots = ({"default": {}} if gateway else _slots(provider))
        probed = None if gateway else (_probe(provider, env) if present else None)

        for slot in slots:
            key = f"{provider}.{slot}"
            if gateway:
                mid = (config.get_default_gateway() or {}).get("default_model", "?")
            elif (config.get_provider(provider) or {}).get("discover_model"):
                mid = config.discover_model(provider, env) or "(undiscovered)"
            else:
                mid = (slots.get(slot) or {}).get("id", "?")

            if not present:
                status, note = "absent", "key/endpoint absent — rung inactive"
            elif key in KNOWN_DEPRECATED or mid in KNOWN_DEPRECATED:
                status, note = "deprecated", KNOWN_DEPRECATED.get(key) or KNOWN_DEPRECATED.get(mid, "")
            elif (config.get_provider(provider) or {}).get("discover_model"):
                status = "confirmed" if mid != "(undiscovered)" else "missing"
                note = "auto-discovered from /v1/models"
            elif probed is None:
                status, note = "unconfirmed", "no /models endpoint — verify manually"
            elif mid in probed:
                status, note = "confirmed", "live in provider /models"
            else:
                status, note = "missing", "NOT in provider /models — update id in inference.yaml"
            rows.append({"provider": provider, "slot": slot, "model_id": mid,
                         "status": status, "note": note})
    return rows


_MARK = {"confirmed": "✓", "deprecated": "✗", "missing": "✗",
         "unconfirmed": "•", "absent": "·"}


def format_report(rows: list[dict[str, Any]], warn_only: bool = False) -> str:
    out: list[str] = []
    for r in rows:
        if warn_only and r["status"] in ("confirmed", "absent"):
            continue
        mark = _MARK.get(r["status"], "?")
        line = f"  {mark} {r['provider']}.{r['slot']:<12} {r['model_id']:<36} {r['status']}"
        out.append(line)
        if r["status"] in ("missing", "deprecated") and r["note"]:
            out.append(f"      → {r['note']}")
    if not out:
        out.append("  ✓ all configured model ids confirmed (or inactive)")
    return "\n".join(out)


def has_problems(rows: list[dict[str, Any]]) -> bool:
    return any(r["status"] in ("missing", "deprecated") for r in rows)


def main(argv: list[str]) -> int:
    warn_only = "--warn-only" in argv
    rows = validate()
    if warn_only:
        problems = [r for r in rows if r["status"] in ("missing", "deprecated")]
        if problems:
            print("  ⚠ ROSTER: configured model ids need attention:")
            print(format_report(problems))
        return 0
    print(format_report(rows))
    return 1 if has_problems(rows) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
