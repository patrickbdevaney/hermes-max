"""Tier-2 config surface: key capture, masked key status, non-secret config writes,
and a live connection probe. The secret never round-trips to the browser — it goes
in via POST /api/keys/{provider}, straight to the secret store; only a {present}
boolean ever comes back out.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from lib.inference import config, roles

from . import secrets_store


# ── native directory picker (Fix 3) ───────────────────────────────────────────
# The UI is served from this Python backend on the user's own machine, so we can
# pop the OS-native folder chooser on their display. zenity (GNOME) first, then
# kdialog (KDE), then yad. Returns {path} on selection, {cancelled} if dismissed,
# {error} if no dialog tool / no display.
def _dialog_tool() -> Optional[str]:
    for t in ("zenity", "kdialog", "yad"):
        if shutil.which(t):
            return t
    return None


def browse_dir(start: str | None = None) -> dict[str, Any]:
    """Open the OS native directory picker and return the chosen path. Best-effort:
    needs a dialog tool AND a display (headless → graceful error → text entry)."""
    tool = _dialog_tool()
    if not tool:
        return {"path": None, "error": "no dialog tool found",
                "hint": "install zenity for directory browsing: sudo apt install zenity"}
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return {"path": None, "error": "no display available (headless session)"}
    home = os.path.expanduser(start or "~")
    if tool == "zenity":
        cmd = ["zenity", "--file-selection", "--directory",
               "--title=Select project directory", f"--filename={home}/"]
    elif tool == "kdialog":
        cmd = ["kdialog", "--getexistingdirectory", home]
    else:  # yad
        cmd = ["yad", "--file", "--directory", "--title=Select project directory"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return {"path": None, "error": f"dialog failed: {e}"}
    if r.returncode == 0:
        path = r.stdout.strip()
        return {"path": path or None} if path else {"path": None, "cancelled": True}
    # non-zero return = user cancelled/closed the dialog
    return {"path": None, "cancelled": True}

# Every env var that could hold a provider secret — used for masked status and for
# injecting keychain-held keys into a launched agent.
def secret_env_vars() -> list[str]:
    out: list[str] = []
    for p in config.providers().values():
        ev = p.get("api_key_env")
        if ev and ev not in ("null",) and ev not in out:
            out.append(ev)
    gw = config.get_default_gateway() or {}
    if gw.get("api_key_env") and gw["api_key_env"] not in out:
        out.append(gw["api_key_env"])
    return out


def _api_key_env(provider: str) -> Optional[str]:
    if provider == "default_gateway":
        return (config.get_default_gateway() or {}).get("api_key_env")
    p = config.get_provider(provider)
    if not p:
        return None
    ev = p.get("api_key_env")
    return ev if ev not in (None, "", "null") else None


# ── GET /api/keys/status ──────────────────────────────────────────────────────
def keys_status() -> dict[str, Any]:
    """Per-provider {present:bool} ONLY — never the secret. Plus the env-var name
    (non-secret) and tier, so the wizard can label and prioritise."""
    providers = []
    for name, p in config.providers().items():
        ev = p.get("api_key_env")
        keyless = ev in (None, "", "null")
        providers.append({
            "name": name,
            "api_key_env": None if keyless else ev,
            "keyless": keyless,
            "tier": config.tier(name),
            "present": True if keyless else secrets_store.has_secret(ev),
        })
    return {
        "backend": secrets_store.backend(),
        "backend_label": secrets_store.backend_label(),
        "is_keychain": secrets_store.is_keychain(),
        "providers": providers,
    }


# ── POST /api/keys/{provider} ─────────────────────────────────────────────────
def store_key(provider: str, value: str) -> dict[str, Any]:
    ev = _api_key_env(provider)
    if ev is None:
        if config.get_provider(provider) is None and provider != "default_gateway":
            return {"ok": False, "error": f"unknown provider: {provider}"}
        return {"ok": False, "error": f"{provider} needs no API key (keyless/local)"}
    if not value or not value.strip():
        return {"ok": False, "error": "empty key"}
    # Hand the raw value straight to the store; never log or echo it.
    return secrets_store.set_secret(ev, value.strip())


# ── POST /api/config (non-secret only) ────────────────────────────────────────
def apply_config(body: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    applied: list[str] = []
    if "mode" in body and body["mode"]:
        res = roles.set_mode(str(body["mode"]))
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "bad mode"),
                    "available": res.get("available", [])}
        warnings += res.get("warnings", [])
        applied.append("mode")
    if body.get("vllm_base_url"):
        # Non-secret endpoint config lives in .env alongside everything else.
        secrets_store.set_plain_env("VLLM_BASE_URL", str(body["vllm_base_url"]).strip())
        applied.append("vllm_base_url")
    from . import feeds
    return {"ok": True, "applied": applied, "warnings": warnings,
            "config": feeds.config_payload()}


# ── POST /api/test-connection ─────────────────────────────────────────────────
def _redact(text: str, secret: Optional[str]) -> str:
    if secret and secret in text:
        text = text.replace(secret, "***")
    return text[:300]


def test_connection(provider: str) -> dict[str, Any]:
    """Live probe of a provider's OpenAI-/Anthropic-compatible models endpoint.
    Returns {ok, latency_ms, model, status} — the key is sent as a header and is
    NEVER returned (and is redacted from any error text)."""
    if provider == "default_gateway":
        p = config.get_default_gateway() or {}
        base = p.get("base_url")
    else:
        p = config.get_provider(provider)
        if not p:
            return {"ok": False, "error": f"unknown provider: {provider}"}
        base = config.base_url(provider)
    if not base:
        hint = "set VLLM_BASE_URL" if provider == "local_vllm" else "no base_url configured"
        return {"ok": False, "error": hint}

    ev = _api_key_env(provider)
    key = secrets_store._resolve(ev) if ev else None
    if ev and not key:
        return {"ok": False, "error": "no key configured for this provider"}

    kind = p.get("kind", "openai_compatible")
    # A real User-Agent + Accept: several providers (Groq, Cerebras, OpenRouter)
    # sit behind Cloudflare, which 403s the default `Python-urllib` UA (code 1010).
    headers = {"User-Agent": "hermes-max-ui/1.0 (+https://github.com/hermes-max)",
               "Accept": "application/json"}
    if kind == "anthropic":
        url = base.rstrip("/") + "/v1/models"
        headers.update({"x-api-key": key or "", "anthropic-version": "2023-06-01"})
    else:
        url = base.rstrip("/") + "/models"
        if key:
            headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            latency_ms = int((time.monotonic() - t0) * 1000)
            raw = r.read(20000)
            model = _first_model(raw)
            return {"ok": True, "latency_ms": latency_ms, "model": model,
                    "status": r.status}
    except urllib.error.HTTPError as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        detail = ""
        try:
            detail = _redact(e.read(2000).decode("utf-8", "replace"), key)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "status": e.code, "latency_ms": latency_ms,
                "error": _redact(f"HTTP {e.code} {e.reason}", key), "detail": detail}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": _redact(f"unreachable: {e}", key)}


def _first_model(raw: bytes) -> Optional[str]:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    items = data.get("data") or data.get("models") or []
    if items and isinstance(items, list):
        first = items[0]
        if isinstance(first, dict):
            return first.get("id") or first.get("name")
    return None
