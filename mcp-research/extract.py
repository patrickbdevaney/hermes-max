"""Stage 4 — extraction ladder (graceful degradation by page type + failure).

A single page-fetch can fail many ways: static articles want a fast CPU extractor,
JS-rendered SPAs need a real browser, blocked/complex pages and PDFs need a hosted
reader. So fetching is a LADDER, picked by page type and fallen through on failure:

  1. Trafilatura  — free, CPU, ms-fast; great on static articles. (import-guarded;
                    absent in this venv => the rung is skipped, ladder starts at 2)
  2. Crawl4AI     — the existing JS-capable extractor, via mcp-docs.fetch_clean
                    (which itself does Crawl4AI -> trafilatura inside mcp-docs).
  3. Jina Reader  — r.jina.ai, rate-limited free (JINA_API_KEY lifts it); the
                    fallback for blocked/complex pages + PDFs.

PDFs and known-JS hosts reorder the ladder (trafilatura is poor on those). Every
rung is best-effort and returns None on failure/empty so the next rung runs; if all
rungs fail the caller still has the SearXNG snippet. Never raises.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc  # rc._fetch -> mcp-docs.fetch_clean (Crawl4AI rung)

JINA_API_KEY = os.environ.get("JINA_API_KEY", "").strip()
JINA_READER = "https://r.jina.ai/"
EXTRACT_TIMEOUT = float(os.environ.get("RESEARCH_EXTRACT_TIMEOUT", "20"))
_JS_HOSTS = ("twitter.com", "x.com", "medium.com", "notion.site", "reddit.com")


# ── rungs (each: url -> markdown|None; monkeypatchable in tests) ──────────────
def _rung_trafilatura(url: str) -> str | None:
    """Fast CPU extraction. Import-guarded: trafilatura may not be installed in
    this venv (it lives in mcp-docs) — then this rung is a clean no-op."""
    try:
        import trafilatura  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        with httpx.Client(timeout=EXTRACT_TIMEOUT, follow_redirects=True) as c:
            html = c.get(url).text
        md = trafilatura.extract(html, output_format="markdown", include_links=True)
        return md.strip() if md and md.strip() else None
    except Exception:  # noqa: BLE001
        return None


def _rung_crawl4ai(url: str) -> str | None:
    """JS-capable extraction via the existing mcp-docs.fetch_clean (Crawl4AI ->
    trafilatura inside mcp-docs). None on failure/empty."""
    try:
        fc = rc._fetch(url)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(fc, dict) and fc.get("ok"):
        md = fc.get("markdown") or ""
        return md.strip() if md.strip() else None
    return None


def _rung_jina(url: str) -> str | None:
    """Hosted reader fallback for blocked/complex pages + PDFs. Rate-limited free;
    JINA_API_KEY lifts the limit. None on failure."""
    try:
        headers = {"Authorization": f"Bearer {JINA_API_KEY}"} if JINA_API_KEY else {}
        with httpx.Client(timeout=EXTRACT_TIMEOUT, follow_redirects=True) as c:
            r = c.get(JINA_READER + quote(url, safe=":/?=&%"), headers=headers)
            r.raise_for_status()
            txt = r.text
        return txt.strip() if txt and txt.strip() else None
    except Exception:  # noqa: BLE001
        return None


_RUNGS = {"trafilatura": _rung_trafilatura, "crawl4ai": _rung_crawl4ai, "jina": _rung_jina}


def _ladder_for(url: str) -> list[str]:
    u = (url or "").lower()
    if u.endswith(".pdf") or "/pdf/" in u:
        return ["jina", "crawl4ai"]            # trafilatura is poor on PDFs
    if any(h in u for h in _JS_HOSTS):
        return ["crawl4ai", "jina", "trafilatura"]  # JS-heavy -> browser first
    return ["trafilatura", "crawl4ai", "jina"]      # static default: fast first


def extract_url(url: str, prefer: list[str] | None = None) -> dict[str, Any]:
    """Run the extraction ladder for a URL, falling through on failure/empty.
    Returns {ok, url, markdown, method, attempts} — attempts records which rungs
    were tried and whether each produced content (observability for the fall-through)."""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "empty url", "url": url, "markdown": "", "attempts": []}
    order = [r for r in (prefer or _ladder_for(url)) if r in _RUNGS]
    attempts: list[dict[str, Any]] = []
    for name in order:
        try:
            md = _RUNGS[name](url)
        except Exception as e:  # noqa: BLE001
            attempts.append({"rung": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            continue
        ok = bool(md)
        attempts.append({"rung": name, "ok": ok, "chars": len(md) if md else 0})
        if ok:
            otel_emit.record("extracted", {"url": url, "method": name, "chars": len(md),
                                           "rungs_tried": len(attempts)})
            return {"ok": True, "url": url, "markdown": md, "method": name, "attempts": attempts}
    otel_emit.record("extract_failed", {"url": url, "rungs_tried": len(attempts)})
    return {"ok": False, "url": url, "markdown": "", "method": None,
            "error": "all extraction rungs failed", "attempts": attempts}


def extract_stats() -> dict[str, Any]:
    try:
        import trafilatura  # noqa: F401
        traf = "available"
    except Exception:  # noqa: BLE001
        traf = "absent (ladder starts at crawl4ai)"
    return {"trafilatura": traf, "crawl4ai": "via mcp-docs.fetch_clean",
            "jina": "keyed" if JINA_API_KEY else "keyless (rate-limited)",
            "ladder_default": ["trafilatura", "crawl4ai", "jina"]}
