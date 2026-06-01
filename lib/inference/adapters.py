"""HTTP adapters — the ONLY place lib/inference touches the wire.

Two ``kind`` adapters: ``openai_compatible`` (every provider's /chat/completions)
and ``anthropic`` (the Messages API). Both normalize to one shape:

    {ok, text, in_tok, out_tok, cached_tok, status, headers, error}

No provider SDKs — pure httpx, exactly like the existing conductor seam, so a new
provider of an existing kind is a YAML edit, never a code change.
"""
from __future__ import annotations

from typing import Any, Optional

try:
    import httpx  # type: ignore
    _HAVE_HTTPX = True
except Exception:
    _HAVE_HTTPX = False

TIMEOUT = 600.0


def _norm(headers: Any) -> dict[str, str]:
    try:
        return {k.lower(): v for k, v in dict(headers).items()}
    except Exception:
        return {}


def call(kind: str, base_url: str, api_key: Optional[str], model: str,
         messages: list[dict[str, str]], max_tokens: int = 2048,
         timeout: float = TIMEOUT) -> dict[str, Any]:
    """Dispatch to the right adapter. Never raises — returns ok:False on any error."""
    if not _HAVE_HTTPX:
        return {"ok": False, "error": "httpx not installed", "status": 0,
                "headers": {}, "text": "", "in_tok": 0, "out_tok": 0, "cached_tok": 0}
    try:
        if kind == "anthropic":
            return _anthropic(base_url, api_key, model, messages, max_tokens, timeout)
        return _openai(base_url, api_key, model, messages, max_tokens, timeout)
    except Exception as e:                # network/timeout/parse — caller falls to next rung
        status = getattr(getattr(e, "response", None), "status_code", 0) or 0
        headers = _norm(getattr(getattr(e, "response", None), "headers", {}))
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "status": status,
                "headers": headers, "text": "", "in_tok": 0, "out_tok": 0, "cached_tok": 0}


def _openai(base_url: str, api_key: Optional[str], model: str,
            messages: list[dict[str, str]], max_tokens: int, timeout: float) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base_url.rstrip('/')}/chat/completions",
                           json=payload, headers=headers)
        rh = _norm(resp.headers)
        resp.raise_for_status()
        data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content")
    usage = data.get("usage") or {}
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
              or usage.get("prompt_cache_hit_tokens") or 0)
    return {
        "ok": text is not None and text != "",
        "text": text or "",
        "in_tok": int(usage.get("prompt_tokens", 0) or 0),
        "out_tok": int(usage.get("completion_tokens", 0) or 0),
        "cached_tok": int(cached or 0),
        "status": resp.status_code, "headers": rh, "error": None,
    }


def _anthropic(base_url: str, api_key: Optional[str], model: str,
               messages: list[dict[str, str]], max_tokens: int, timeout: float) -> dict[str, Any]:
    # Split the OpenAI-style system message out into Anthropic's top-level `system`.
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    conv = [{"role": ("assistant" if m.get("role") == "assistant" else "user"),
             "content": m.get("content", "")}
            for m in messages if m.get("role") != "system"]
    headers = {"Content-Type": "application/json",
               "anthropic-version": "2023-06-01"}
    if api_key:
        headers["x-api-key"] = api_key
    payload: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": conv}
    if system:
        payload["system"] = system
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base_url.rstrip('/')}/v1/messages",
                           json=payload, headers=headers)
        rh = _norm(resp.headers)
        resp.raise_for_status()
        data = resp.json()
    parts = data.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    usage = data.get("usage") or {}
    cached = int(usage.get("cache_read_input_tokens", 0) or 0)
    return {
        "ok": bool(text),
        "text": text,
        "in_tok": int(usage.get("input_tokens", 0) or 0) + cached,
        "out_tok": int(usage.get("output_tokens", 0) or 0),
        "cached_tok": cached,
        "status": resp.status_code, "headers": rh, "error": None,
    }
