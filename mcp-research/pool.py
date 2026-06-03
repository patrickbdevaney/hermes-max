"""Phase 3 — sovereign multi-PROVIDER cheap-inference pool (env-only; NO keys committed).

The one entry point every cheap fan-out step calls: `map_cheap(prompts) -> results`
(ordered, bounded, concurrent). The key correction from the research: Groq free-tier
limits are per-ORGANIZATION, not per-key — stacking keys on one org does NOT multiply
throughput. So real concurrency = the SUM of safe per-PROVIDER lanes (Groq + Cerebras
+ local Qwen), each lane one rate-limited org; keys within a provider only spread that
org's limit and survive a single bad key.

Degradation ladder (all three preserved — keeps the repo forkable + sovereign):
  no keys      → local Qwen ($VLLM_BASE_URL), sequential single lane
  one provider → single rate-limited lane
  multi-provider → wide parallel (sum of lanes)

No framework. httpx only (already a dep). Reads env exclusively:
  GROQ_API_KEYS=k1,k2,…   CEREBRAS_API_KEYS=…   VLLM_BASE_URL (always a lane if set)
  POOL_{GROQ,CEREBRAS,LOCAL}_CONCURRENCY, {GROQ,CEREBRAS,VLLM}_MODEL
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

POOL_TIMEOUT = float(os.environ.get("POOL_TIMEOUT", "120"))


def _keys(env: str) -> list[str]:
    return [k.strip() for k in os.environ.get(env, "").split(",") if k.strip()]


class _Lane:
    """One provider = one rate-limited org lane (keys share its limit)."""
    def __init__(self, name: str, base_url: str, model: str, keys: list, concurrency: int):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keys = keys or [None]
        self.concurrency = max(1, concurrency)
        self._rr = 0
        self._cooldown: dict[Any, float] = {}  # key → monotonic ts to skip until (429 backoff)


def lanes() -> list[_Lane]:
    out: list[_Lane] = []
    g = _keys("GROQ_API_KEYS")
    if g:
        out.append(_Lane("groq", "https://api.groq.com/openai/v1",
                          os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                          g, int(os.environ.get("POOL_GROQ_CONCURRENCY", "4"))))
    c = _keys("CEREBRAS_API_KEYS")
    if c:
        out.append(_Lane("cerebras", "https://api.cerebras.ai/v1",
                         os.environ.get("CEREBRAS_MODEL", "llama-3.3-70b"),
                         c, int(os.environ.get("POOL_CEREBRAS_CONCURRENCY", "2"))))
    v = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
    if v:
        out.append(_Lane("local", v, os.environ.get("VLLM_MODEL", os.environ.get("DISTILL_MODEL", "/model")),
                         [os.environ.get("VLLM_API_KEY")], int(os.environ.get("POOL_LOCAL_CONCURRENCY", "2"))))
    return out


async def _one(lane: _Lane, client: httpx.AsyncClient, messages: list[dict],
               temperature: float, max_tokens: int) -> str | None:
    """One completion on a lane: round-robin keys, skip cooled-down keys, back off
    on 429 (honouring retry-after), track remaining-requests where returned."""
    for _ in range(len(lane.keys)):
        key = lane.keys[lane._rr % len(lane.keys)]
        lane._rr += 1
        if lane._cooldown.get(key, 0.0) > time.monotonic():
            continue
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            r = await client.post(f"{lane.base_url}/chat/completions", headers=headers,
                                  json={"model": lane.model, "messages": messages,
                                        "temperature": temperature, "max_tokens": max_tokens})
            if r.status_code == 429:
                lane._cooldown[key] = time.monotonic() + float(r.headers.get("retry-after", "2") or 2)
                continue
            # proactively cool a near-empty key (Groq returns these headers)
            rem = r.headers.get("x-ratelimit-remaining-requests")
            if rem is not None:
                try:
                    if int(rem) <= 1:
                        lane._cooldown[key] = time.monotonic() + 5.0
                except ValueError:
                    pass
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content")
            return content.strip() if content else None
        except Exception:  # noqa: BLE001 — try the next key / lane handles None
            continue
    return None


def _select_lanes(cloud_only: bool = False) -> list[_Lane]:
    """All lanes (groq, cerebras, local) or — when cloud_only — just the paid/cloud
    lanes, so helper inference can reserve the local vLLM endpoint for the agent's
    main execution driver. Returns [] when cloud_only and no cloud lane exists; the
    caller is then expected to fall back to its own local path."""
    ls = lanes()
    return [l for l in ls if l.name != "local"] if cloud_only else ls


def cloud_available() -> bool:
    """True iff at least one NON-local (Groq/Cerebras) lane is configured."""
    return any(l.name != "local" for l in lanes())


async def complete_one_async(messages: list[dict], temperature: float = 0.2,
                             max_tokens: int = 1200, cloud_only: bool = False) -> str | None:
    """A SINGLE chat completion routed across the lanes in priority order (groq →
    cerebras → local), returning the first non-empty reply. With cloud_only it never
    touches the local lane (returns None if no cloud lane → caller falls back)."""
    ls = _select_lanes(cloud_only)
    if not ls:
        return None
    async with httpx.AsyncClient(timeout=POOL_TIMEOUT) as client:
        for lane in ls:
            out = await _one(lane, client, messages, temperature, max_tokens)
            if out:
                return out
    return None


def complete_one(messages: list[dict], temperature: float = 0.2,
                 max_tokens: int = 1200, cloud_only: bool = False) -> str | None:
    """Sync wrapper for complete_one_async (loop-safe via _run)."""
    return _run(complete_one_async(messages, temperature, max_tokens, cloud_only))


async def map_cheap_async(prompts: list[str], system: str | None = None,
                          temperature: float = 0.2, max_tokens: int = 1200,
                          cloud_only: bool = False) -> list[str | None]:
    ls = _select_lanes(cloud_only)
    results: list[str | None] = [None] * len(prompts)
    if not ls or not prompts:
        return results
    idx_iter = iter(range(len(prompts)))
    lock = asyncio.Lock()

    async def worker(lane: _Lane, client: httpx.AsyncClient) -> None:
        while True:
            async with lock:
                try:
                    i = next(idx_iter)
                except StopIteration:
                    return
            msgs = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompts[i]}]
            results[i] = await _one(lane, client, msgs, temperature, max_tokens)

    async with httpx.AsyncClient(timeout=POOL_TIMEOUT) as client:
        # sum of per-provider safe concurrency = total in-flight (the real parallelism)
        tasks = [asyncio.create_task(worker(lane, client)) for lane in ls for _ in range(lane.concurrency)]
        await asyncio.gather(*tasks)
    return results


def _run(coro: Any) -> Any:
    """Complete a coroutine whether or not a loop is already running (FastMCP holds
    one). Mirrors research_core._run_coro to avoid the 'smoke passes, live fails' trap."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import threading
    box: dict[str, Any] = {}

    def _w() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["v"] = loop.run_until_complete(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        finally:
            loop.close()
    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def map_cheap(prompts: list[str], system: str | None = None,
              temperature: float = 0.2, max_tokens: int = 1200,
              cloud_only: bool = False) -> list[str | None]:
    """Ordered, bounded, concurrent cheap completions across the provider lanes.
    Returns [None]*n when no (selected) lane is configured (caller keeps its
    deterministic path). cloud_only excludes the local vLLM lane."""
    if not prompts:
        return []
    return _run(map_cheap_async(prompts, system, temperature, max_tokens, cloud_only))


def available() -> bool:
    return bool(lanes())


def pool_stats() -> dict[str, Any]:
    ls = lanes()
    return {
        "lanes": [{"name": l.name, "model": l.model, "keys": len(l.keys), "concurrency": l.concurrency} for l in ls],
        "total_concurrency": sum(l.concurrency for l in ls),
        "mode": "none" if not ls else ("single-lane" if len(ls) == 1 else "multi-provider"),
    }
