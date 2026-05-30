"""Sovereign documentation ingestion: SearXNG → Crawl4AI → local distil → RAG/KG.

The whole loop is self-hosted — no Firecrawl/Tavily/Exa key. SearXNG ($SEARXNG_URL)
finds candidate URLs; a page is turned into clean markdown by trafilatura first
(local, in-process, fastest) and Crawl4AI ($CRAWL4AI_URL) only as the JS-rendering
fallback; the local chat model
($VLLM_BASE_URL) distils it to a high-signal technical note; the note lands in
mcp-codebase-rag under a `docs/<topic>` namespace (co-retrievable with code) and
its APIs land in mcp-knowledge-graph (framework→api). Every external dependency
has a local default or a graceful degradation — nothing hard-fails offline.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any

import httpx

try:
    import otel_emit  # best-effort spans to Phoenix; no-op if unavailable
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

# Shared in-server lazy-install guard (trafilatura fallback).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
try:
    from lazy_install import ensure  # type: ignore
except Exception:  # noqa: BLE001
    def ensure(name, spec=None, **_):  # type: ignore
        try:
            import importlib
            return importlib.import_module(name)
        except Exception:  # noqa: BLE001
            return None

# ── config (all local defaults) ───────────────────────────────────────────────
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080").rstrip("/")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://localhost:11235").rstrip("/")
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
VLLM_MODEL = os.environ.get("VLLM_MODEL", os.environ.get("DISTILL_MODEL", "/model"))
RAG_MCP_URL = os.environ.get("RAG_MCP_URL", "http://127.0.0.1:9102/mcp")
KG_MCP_URL = os.environ.get("KG_MCP_URL", "http://127.0.0.1:9103/mcp")

HTTP_TIMEOUT = float(os.environ.get("DOCS_HTTP_TIMEOUT", "60"))
CRAWL_TIMEOUT = float(os.environ.get("DOCS_CRAWL_TIMEOUT", "90"))
DISTILL_TIMEOUT = float(os.environ.get("DOCS_DISTILL_TIMEOUT", "300"))
# The chat model is a reasoning model — it spends a big hidden budget before the
# answer, returning content=None if max_tokens is too small. Keep this generous.
DISTILL_MAX_TOKENS = int(os.environ.get("DOCS_DISTILL_MAX_TOKENS", "6000"))


# ── search (SearXNG JSON) ─────────────────────────────────────────────────────
def search_docs(query: str, category: str | None = None, limit: int = 8) -> dict[str, Any]:
    """Search the self-hosted SearXNG for candidate doc URLs (JSON API)."""
    params: dict[str, str] = {"q": query, "format": "json"}
    if category:
        params["categories"] = category
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": "hermes-max-docs/1.0"}) as c:
            r = c.get(f"{SEARXNG_URL}/search", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "query": query, "error": f"SearXNG unavailable: {type(e).__name__}: {e}",
                "hint": f"is SearXNG up with JSON enabled? ./searXNG.sh ({SEARXNG_URL})", "results": []}
    results = [
        {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")}
        for x in (data.get("results") or [])
        if x.get("url")
    ][:limit]
    return {"ok": True, "query": query, "category": category, "count": len(results), "results": results}


# ── fetch + clean (trafilatura first, Crawl4AI fallback) ──────────────────────
def _crawl4ai_md(url: str) -> str | None:
    try:
        with httpx.Client(timeout=CRAWL_TIMEOUT) as c:
            r = c.post(f"{CRAWL4AI_URL}/md", json={"url": url})
            r.raise_for_status()
            data = r.json()
        md = data.get("markdown")
        if isinstance(md, dict):  # some versions nest {raw_markdown,fit_markdown}
            md = md.get("fit_markdown") or md.get("raw_markdown")
        return md if isinstance(md, str) and md.strip() else None
    except Exception:  # noqa: BLE001
        return None


def _trafilatura_md(url: str) -> str | None:
    traf = ensure("trafilatura")
    if traf is None:
        return None
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as c:
            html = c.get(url).text
        return traf.extract(html, include_links=False, include_comments=False) or None
    except Exception:  # noqa: BLE001
        return None


def fetch_clean(url: str) -> dict[str, Any]:
    """Fetch a URL → clean markdown. Extraction ladder, FASTEST FIRST:
      1. trafilatura — local, in-process, no container, sub-second on static HTML;
                       handles the large majority of documentation pages.
      2. Crawl4AI    — heavier (a container that renders JS); used ONLY when
                       trafilatura returns nothing (a JS-heavy / dynamic page).
    """
    md = _trafilatura_md(url)
    backend = "trafilatura"
    if md is None:
        md = _crawl4ai_md(url)
        backend = "crawl4ai"
    if md is None:
        return {"ok": False, "url": url,
                "error": "both trafilatura and Crawl4AI failed",
                "hint": f"is Crawl4AI up for JS-heavy pages? ./crawl4ai.sh ({CRAWL4AI_URL})"}
    return {"ok": True, "url": url, "backend": backend, "chars": len(md), "markdown": md}


# ── distil (local chat model) ─────────────────────────────────────────────────
_DISTILL_SYS = (
    "You are a precise technical-documentation distiller. Given raw documentation "
    "for a software framework, produce a HIGH-SIGNAL markdown note for an engineer: "
    "keep exact API signatures, function/class/method names, parameters, code "
    "blocks, configuration keys, and version-specific facts. DROP navigation, "
    "marketing, boilerplate, and repetition. Output ONLY the distilled markdown."
)


def distill(markdown: str, topic: str) -> dict[str, Any]:
    """Distil raw markdown to a high-signal note via the local chat model.
    Degrades to truncated raw markdown if the model is unset/unreachable."""
    raw = markdown[:24000]
    if not VLLM_BASE_URL:
        return {"ok": True, "distilled": False, "note": raw,
                "warning": "VLLM_BASE_URL unset — stored raw (no distil)"}
    body = {
        "model": VLLM_MODEL,
        "messages": [
            {"role": "system", "content": _DISTILL_SYS},
            {"role": "user", "content": f"Topic: {topic}\n\nRaw documentation:\n\n{raw}"},
        ],
        "temperature": 0,
        "max_tokens": DISTILL_MAX_TOKENS,
    }
    try:
        with httpx.Client(timeout=DISTILL_TIMEOUT) as c:
            r = c.post(f"{VLLM_BASE_URL}/chat/completions", json=body)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
        content = msg.get("content")
        if not content:  # reasoning model spent the whole budget thinking
            return {"ok": True, "distilled": False, "note": raw,
                    "warning": "model returned empty content (raised max_tokens needed) — stored raw"}
        return {"ok": True, "distilled": True, "note": content.strip()}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "distilled": False, "note": raw,
                "warning": f"distil failed ({type(e).__name__}) — stored raw"}


# ── MCP client helper (call RAG / KG over streamable-http) ────────────────────
async def _mcp_call_async(url: str, tool: str, args: dict) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            data = res.structuredContent or (
                json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and len(data) == 1:
                data = data["result"]
            return data


def _run_coro(coro: Any) -> Any:
    """Run a coroutine whether or not an event loop is already running in this
    thread. FastMCP runs tool handlers inside a live loop, so a bare asyncio.run()
    here raises "cannot be called from a running event loop"; the swallowed error
    made ingest_doc's rag/kg store silently no-op in the live server while passing
    main-thread smoke tests. When a loop is running, complete the coroutine on a
    dedicated worker thread with its own loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import threading
    box: dict[str, Any] = {}
    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["v"] = loop.run_until_complete(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        finally:
            loop.close()
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _mcp_call(url: str, tool: str, args: dict) -> dict[str, Any]:
    try:
        return {"ok": True, "result": _run_coro(_mcp_call_async(url, tool, args))}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── API extraction for the KG (deterministic, no extra LLM call) ──────────────
_CODE_FENCE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)
_DEFCLASS = re.compile(r"\b(?:def|class|func|fn|function|interface|struct|type)\s+([A-Za-z_]\w+)")
_INLINE = re.compile(r"`([A-Za-z_][\w.]{2,40})`")


def extract_apis(markdown: str, limit: int = 25) -> list[str]:
    apis: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        name = name.strip()
        if name and name not in seen and not name.isdigit():
            seen.add(name)
            apis.append(name)

    for fence in _CODE_FENCE.findall(markdown):
        for m in _DEFCLASS.findall(fence):
            add(m)
    for m in _INLINE.findall(markdown):
        if "(" not in m and m[0].isalpha():
            add(m)
    return apis[:limit]


# ── ingest (fetch→distil→store RAG + KG) ──────────────────────────────────────
def _looks_like_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip()))


def ingest_doc(url_or_markdown: str, topic: str) -> dict[str, Any]:
    """Fetch (if URL) → distil → store in RAG (docs/<topic>) + KG (framework→api).
    Each store step is best-effort; partial success is reported honestly."""
    topic_slug = re.sub(r"[^a-z0-9._-]+", "-", topic.lower()).strip("-") or "uncategorized"
    namespace = f"docs/{topic_slug}"
    source = ""
    if _looks_like_url(url_or_markdown):
        source = url_or_markdown.strip()
        fc = fetch_clean(source)
        if not fc.get("ok"):
            return {"ok": False, "topic": topic, "url": source, "stage": "fetch", **fc}
        markdown, backend = fc["markdown"], fc["backend"]
    else:
        markdown, backend = url_or_markdown, "inline"

    dz = distill(markdown, topic)
    note = dz["note"]
    title = next((ln.lstrip("# ").strip() for ln in note.splitlines() if ln.strip()), topic)[:120]

    # store in RAG (co-retrievable with code)
    rag = _mcp_call(RAG_MCP_URL, "index_document",
                    {"text": note, "namespace": namespace, "source": source or title, "title": title})
    rag_ok = rag.get("ok") and (rag.get("result") or {}).get("ok", False)

    # store APIs in KG (framework → api)
    apis = extract_apis(note)
    kg_writes = 0
    kg_err = None
    fw = _mcp_call(KG_MCP_URL, "record_entity",
                   {"type": "framework", "name": topic_slug, "props": {"topic": topic}})
    if fw.get("ok"):
        kg_writes += 1
        if source:
            _mcp_call(KG_MCP_URL, "record_relation",
                      {"a": topic_slug, "rel": "documented_in", "b": source})
        for api in apis:
            if _mcp_call(KG_MCP_URL, "record_relation",
                         {"a": topic_slug, "rel": "has_api", "b": api}).get("ok"):
                kg_writes += 1
    else:
        kg_err = fw.get("error")

    otel_emit.record("doc_ingested", {
        "topic": topic, "namespace": namespace, "source": source,
        "fetch_backend": backend, "distilled": dz.get("distilled", False),
        "rag_stored": bool(rag_ok), "apis": len(apis), "kg_writes": kg_writes,
    }, status="ok" if rag_ok else "error")
    return {
        "ok": True,
        "topic": topic,
        "namespace": namespace,
        "source": source,
        "fetch_backend": backend,
        "distilled": dz.get("distilled", False),
        "note_chars": len(note),
        "rag_stored": bool(rag_ok),
        "rag_detail": rag.get("result") or rag.get("error"),
        "kg_entities_written": kg_writes,
        "apis": apis,
        "warnings": [w for w in [dz.get("warning"), kg_err] if w],
    }


# ── research (orchestrate search → ingest top N → brief) ──────────────────────
def research_topic(topic: str, n: int = 3, category: str | None = None) -> dict[str, Any]:
    """The 'learn a novel framework' entry point: search official docs, ingest the
    top N, return a distilled topic brief. Fully sovereign."""
    sr = search_docs(topic, category=category, limit=max(n * 2, n))
    if not sr.get("ok"):
        return {"ok": False, "topic": topic, "stage": "search", **sr}
    picked = sr["results"][:n]
    if not picked:
        return {"ok": True, "topic": topic, "ingested": [], "note": "no search results"}
    ingested = []
    for hit in picked:
        res = ingest_doc(hit["url"], topic)
        ingested.append({"url": hit["url"], "title": hit.get("title", ""),
                         "ok": res.get("ok"), "rag_stored": res.get("rag_stored"),
                         "apis": res.get("apis", [])[:8], "warnings": res.get("warnings", [])})
    apis_all = sorted({a for it in ingested for a in it.get("apis", [])})
    otel_emit.record("framework_learned", {
        "topic": topic, "sources_ingested": len(ingested), "apis": len(apis_all),
    })
    return {
        "ok": True,
        "topic": topic,
        "sources_ingested": len(ingested),
        "namespace": f"docs/{re.sub(r'[^a-z0-9._-]+', '-', topic.lower()).strip('-')}",
        "apis_discovered": apis_all[:30],
        "ingested": ingested,
        "next": f"search_code('{topic} ...') now also returns these distilled docs",
    }


def stats() -> dict[str, Any]:
    def _up(url: str, path: str = "") -> bool:
        try:
            with httpx.Client(timeout=3) as c:
                return c.get(url + path).status_code < 500
        except Exception:  # noqa: BLE001
            return False
    return {
        "searxng": SEARXNG_URL,
        "searxng_up": _up(SEARXNG_URL),
        "crawl4ai": CRAWL4AI_URL,
        "crawl4ai_up": _up(CRAWL4AI_URL, "/health"),
        "distill_model": VLLM_BASE_URL or "(unset — stores raw)",
        "rag_mcp": RAG_MCP_URL,
        "kg_mcp": KG_MCP_URL,
    }
