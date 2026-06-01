"""Rate-bucket tracker — the 429-avoidance brain.

Maintains a live per-(provider, model_id) view of remaining RPM/TPM/RPD with
reset timestamps, seeded from inference.yaml limits and corrected from response
rate-limit headers. ``has_headroom`` is what the router PRE-CHECKS so it skips a
rung BEFORE sending — we never absorb a 429. On an actual 429 we honor
``retry-after`` and mark the bucket exhausted until reset.

State persists to ~/.hermes-max/inference/buckets.json so headroom survives across
short-lived MCP processes within a day.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

from . import config

_lock = threading.Lock()


def _state_path() -> str:
    """Read the env at use-time (not import) so overrides + tests are honored."""
    return os.path.expanduser(
        os.environ.get("INFERENCE_BUCKETS_PATH", "~/.hermes-max/inference/buckets.json"))


def _now() -> float:
    return time.time()


def _load() -> dict[str, Any]:
    try:
        with open(_state_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state: dict[str, Any]) -> None:
    try:
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _key(provider: str, model_id: str) -> str:
    return f"{provider}:{model_id}"


def _prune(rec: dict[str, Any], now: float) -> None:
    """Drop request/token timestamps outside their windows."""
    rec["req_m"] = [t for t in rec.get("req_m", []) if now - t < 60]
    rec["req_d"] = [t for t in rec.get("req_d", []) if now - t < 86400]
    rec["tok_m"] = [[t, n] for t, n in rec.get("tok_m", []) if now - t < 60]


def has_headroom(provider: str, model_key: str, est_tokens: int = 1000) -> bool:
    """True if a call to provider.model_key fits the live budget. Local/unmetered
    providers always have headroom. Conservative: unknown → allow (we still catch
    a real 429 and back off)."""
    spec = config.resolve_model(provider, model_key) or {}
    model_id = spec.get("id", model_key)
    limits = config.limits_for(provider, model_id)
    if not limits:
        return True                      # unmetered (local) or no limits declared
    now = _now()
    with _lock:
        state = _load()
        rec = state.get(_key(provider, model_id), {})
        _prune(rec, now)
        # honor an active retry-after / explicit exhaustion
        until = rec.get("blocked_until", 0)
        if until and now < until:
            return False
        rpm = limits.get("rpm")
        if rpm is not None and len(rec.get("req_m", [])) >= rpm:
            return False
        rpd = limits.get("rpd")
        if rpd is not None and len(rec.get("req_d", [])) >= rpd:
            return False
        # header-reported remaining requests win when present
        hdr_rem = rec.get("hdr_remaining_req")
        if hdr_rem is not None and hdr_rem <= 0 and now < rec.get("hdr_reset_req", 0):
            return False
        tpm = limits.get("tpm")
        if tpm is not None:
            used = sum(n for _, n in rec.get("tok_m", []))
            if used + est_tokens > tpm:
                return False
    return True


def note_request(provider: str, model_key: str, tokens: int = 0) -> None:
    """Record a fired request + its token cost into the live windows."""
    spec = config.resolve_model(provider, model_key) or {}
    model_id = spec.get("id", model_key)
    if not config.limits_for(provider, model_id):
        return
    now = _now()
    with _lock:
        state = _load()
        k = _key(provider, model_id)
        rec = state.get(k, {})
        _prune(rec, now)
        rec.setdefault("req_m", []).append(now)
        rec.setdefault("req_d", []).append(now)
        if tokens:
            rec.setdefault("tok_m", []).append([now, int(tokens)])
        state[k] = rec
        _save(state)


def update_from_headers(provider: str, model_key: str,
                        headers: dict[str, str]) -> None:
    """Ingest provider rate-limit headers (Groq/OpenRouter/Cerebras style) to
    correct the live remaining-requests view."""
    if not headers:
        return
    spec = config.resolve_model(provider, model_key) or {}
    model_id = spec.get("id", model_key)
    h = {k.lower(): v for k, v in headers.items()}
    rem = h.get("x-ratelimit-remaining-requests")
    reset = h.get("x-ratelimit-reset-requests") or h.get("x-ratelimit-reset")
    if rem is None:
        return
    now = _now()
    with _lock:
        state = _load()
        k = _key(provider, model_id)
        rec = state.get(k, {})
        try:
            rec["hdr_remaining_req"] = int(float(rem))
        except (TypeError, ValueError):
            pass
        rec["hdr_reset_req"] = now + _parse_reset(reset)
        state[k] = rec
        _save(state)


def note_429(provider: str, model_key: str, retry_after: Optional[str] = None) -> None:
    """Mark a bucket exhausted until reset on an actual 429/413."""
    spec = config.resolve_model(provider, model_key) or {}
    model_id = spec.get("id", model_key)
    now = _now()
    secs = _parse_reset(retry_after) if retry_after else 60.0
    with _lock:
        state = _load()
        k = _key(provider, model_id)
        rec = state.get(k, {})
        rec["blocked_until"] = now + max(secs, 1.0)
        state[k] = rec
        _save(state)


def _parse_reset(v: Optional[str]) -> float:
    """Parse a reset/retry-after value ('2.5s', '1m30s', '12', ISO seconds) → secs."""
    if v is None:
        return 60.0
    s = str(v).strip()
    try:
        return float(s)                  # bare seconds
    except ValueError:
        pass
    total = 0.0
    num = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
        elif ch in "ms" and num:
            total += float(num) * (60 if ch == "m" else 1)
            num = ""
    if num:
        total += float(num)
    return total or 60.0


def remaining_rpd(provider: str, model_key: str) -> Optional[int]:
    """Remaining requests-per-day for a free model (for `hm cost`), or None if
    the model is unmetered."""
    spec = config.resolve_model(provider, model_key) or {}
    model_id = spec.get("id", model_key)
    limits = config.limits_for(provider, model_id)
    rpd = limits.get("rpd")
    if rpd is None:
        return None
    now = _now()
    with _lock:
        rec = _load().get(_key(provider, model_id), {})
        _prune(rec, now)
        used = len(rec.get("req_d", []))
    return max(0, int(rpd) - used)
