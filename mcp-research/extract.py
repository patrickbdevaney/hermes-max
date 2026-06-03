"""Stage 4 — extraction ladder (graceful degradation by page type + failure).

A single page-fetch can fail many ways: static articles want a fast CPU extractor,
JS-rendered SPAs need a real browser, blocked/complex pages and PDFs need a hosted
reader. So fetching is a LADDER, picked by page type and fallen through on failure:

  1. Trafilatura  — free, CPU, ms-fast; great on static articles. (import-guarded;
                    absent in this venv => the rung is skipped)
  2. Lightpanda   — B-0: Zig CDP browser, ~16x less RAM / ~9x faster than Chrome, via
                    its standalone `fetch --dump markdown` CLI. No-op if binary absent.
  3. Obscura      — B-1: Rust/V8 CDP browser, via its standalone `fetch` CLI (+stealth).
                    No-op if binary absent.
  4. Crawl4AI     — B-2: the existing heavy Chromium extractor, via mcp-docs.fetch_clean
                    (which itself does Crawl4AI -> trafilatura inside mcp-docs).
  5. Jina Reader  — r.jina.ai, rate-limited free (JINA_API_KEY lifts it); the hosted
                    last-resort for blocked/complex pages + PDFs.

The lightweight CDP browsers (Lightpanda, Obscura) lead the browser tier so heavy
Chromium (Crawl4AI) is reached only when both come back thin — the big wall-clock win.

PDFs and known-JS hosts reorder the ladder (trafilatura is poor on those). Every
rung is best-effort and returns None on failure/empty so the next rung runs; if all
rungs fail the caller still has the SearXNG snippet. Never raises.
"""
from __future__ import annotations

import os
import threading
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
# Optional external fast scraper (Appendix A): a browserless HTTP service that takes
# ?url= and returns clean text/markdown. When set it is Tier A's first try.
FAST_SCRAPER_URL = os.environ.get("FAST_SCRAPER_URL", "").strip().rstrip("/")
FAST_SCRAPER_TIMEOUT = float(os.environ.get("FAST_SCRAPER_TIMEOUT", "8"))
# Tier-A "thin content" floor (Phase 4.3): a static fetch that returns LESS than this
# many chars is treated as a miss so the ladder falls through to the JS browser — this
# is what keeps Chromium the EXCEPTION (most static pages clear the floor on Tier A).
THIN_TEXT_CHARS = int(os.environ.get("RESEARCH_MIN_STATIC_CHARS", "500"))
_JS_HOSTS = ("twitter.com", "x.com", "medium.com", "notion.site", "reddit.com")
# Per-tier concurrency: Tier A (fast_http/trafilatura) rides the wide _fetch_many fan-out
# (RESEARCH_SCRAPE_CONCURRENCY, default 10). The browser tier (Crawl4AI) is the EXPENSIVE
# one — each call spins a headless Chromium in mcp-docs — so it is throttled to a tight
# global bound regardless of how many fan-out workers fall through to it at once. Without
# this, a JS-heavy result set could trigger up to RESEARCH_SCRAPE_CONCURRENCY simultaneous
# browser contexts and stall/OOM mcp-docs.
CRAWL4AI_CONCURRENCY = max(1, int(os.environ.get("RESEARCH_CRAWL4AI_CONCURRENCY", "2")))
_BROWSER_SEM = threading.BoundedSemaphore(CRAWL4AI_CONCURRENCY)
# ── Tier B browser chain: lightweight CDP browsers FIRST, heavy Chromium last ──
# B-0 Lightpanda (Zig, ~16x less RAM / ~9x faster than Chrome) and B-1 Obscura (Rust,
# V8) are single static binaries with a standalone `fetch … --dump markdown` CLI — a
# clean sync subprocess rung, no playwright/CDP client needed. Each is a clean no-op
# when its binary is absent, so the ladder degrades B-0 → B-1 → B-2 (Crawl4AI) → jina
# with zero new Python deps. Per-URL spawn is fine: instant startup is their whole point.
LIGHTPANDA_BIN = os.path.expanduser(os.environ.get("LIGHTPANDA_BIN", "~/hermes-max/bin/lightpanda"))
OBSCURA_BIN = os.path.expanduser(os.environ.get("OBSCURA_BIN", "~/hermes-max/bin/obscura"))
OBSCURA_STEALTH = os.environ.get("OBSCURA_STEALTH", "1") not in ("0", "false", "")
BROWSER_FETCH_TIMEOUT = float(os.environ.get("RESEARCH_BROWSER_FETCH_TIMEOUT", "30"))
# Per-rung success counts (observability) — which browser tier actually served each page.
_tier_counts: dict[str, int] = {}


def _bin_ok(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _cli_browser_fetch(binary: str, url: str, extra: list[str]) -> str | None:
    """One-shot CLI browser fetch: `<binary> fetch <url> --dump markdown [extra]` →
    clean markdown on stdout, None on absence/empty/timeout. Subprocess-timeout-bounded;
    stderr (progress noise) discarded. Used by the Lightpanda + Obscura rungs."""
    if not _bin_ok(binary):
        return None
    try:
        import subprocess
        r = subprocess.run([binary, "fetch", url, "--dump", "markdown", *extra],
                           capture_output=True, text=True, timeout=BROWSER_FETCH_TIMEOUT)
        out = (r.stdout or "").strip()
        return out or None
    except Exception:  # noqa: BLE001 — absence/timeout/crash → next rung
        return None


def _rung_lightpanda(url: str) -> str | None:
    """Tier B-0: Lightpanda (Zig CDP browser) via its standalone `fetch` CLI."""
    return _cli_browser_fetch(LIGHTPANDA_BIN, url, [])


def _rung_obscura(url: str) -> str | None:
    """Tier B-1: Obscura (Rust/V8 CDP browser) via its standalone `fetch` CLI.
    --stealth (anti-detection + tracker blocking) on by default; OBSCURA_STEALTH=0 off."""
    return _cli_browser_fetch(OBSCURA_BIN, url, ["--stealth"] if OBSCURA_STEALTH else [])


# Rungs whose "success" requires clearing the thin-content floor: the browserless static
# tiers AND the lightweight browsers (a near-empty Lightpanda render must fall through to
# Obscura, then Crawl4AI). The heavy/hosted fallbacks (crawl4ai, jina) accept any non-empty
# body — they are the last resort and don't get a second try.
_STATIC_RUNGS = ("fast_http", "trafilatura")
_FLOOR_RUNGS = _STATIC_RUNGS + ("lightpanda", "obscura")


def _fast_http_fetch(url: str) -> str | None:
    """Tier-A.0: external fast scraper (FAST_SCRAPER_URL) — milliseconds, no browser.
    Clean no-op when the env var is unset, so the sovereign default is unchanged."""
    if not FAST_SCRAPER_URL:
        return None
    try:
        with httpx.Client(timeout=FAST_SCRAPER_TIMEOUT, follow_redirects=True) as c:
            r = c.get(FAST_SCRAPER_URL, params={"url": url})
            r.raise_for_status()
            txt = r.text
        return txt.strip() if txt and txt.strip() else None
    except Exception:  # noqa: BLE001
        return None


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
    trafilatura inside mcp-docs). Calls rc._fetch_docs — the RAW browser rung — never
    rc._fetch, so the tiered ladder (whose Tier B is this rung) can't recurse. None on
    failure/empty. The browser call is bounded by _BROWSER_SEM so a wide fetch fan-out
    never spawns more than RESEARCH_CRAWL4AI_CONCURRENCY concurrent Chromium contexts."""
    try:
        with _BROWSER_SEM:
            fc = rc._fetch_docs(url)
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


_RUNGS = {"fast_http": _fast_http_fetch, "trafilatura": _rung_trafilatura,
          "lightpanda": _rung_lightpanda, "obscura": _rung_obscura,
          "crawl4ai": _rung_crawl4ai, "jina": _rung_jina}


def _ladder_for(url: str) -> list[str]:
    u = (url or "").lower()
    if u.endswith(".pdf") or "/pdf/" in u:
        return ["jina", "crawl4ai"]            # trafilatura/JS-DOM browsers are poor on PDFs
    if any(h in u for h in _JS_HOSTS):
        # JS-heavy -> browser first, lightest CDP browser leading: Lightpanda -> Obscura
        # -> Crawl4AI(Chromium) -> jina, then the static tiers as a long shot.
        return ["lightpanda", "obscura", "crawl4ai", "jina", "fast_http", "trafilatura"]
    # static default: browserless HTTP-first (fast scraper, then trafilatura); when both
    # come back thin, fall through the Tier-B browser chain lightest-first:
    # Lightpanda (B-0) -> Obscura (B-1) -> Crawl4AI/Chromium (B-2) -> jina (hosted reader).
    return ["fast_http", "trafilatura", "lightpanda", "obscura", "crawl4ai", "jina"]


def extract_url(url: str, prefer: list[str] | None = None) -> dict[str, Any]:
    """Run the extraction ladder for a URL, falling through on failure/empty.
    Returns {ok, url, markdown, method, attempts} — attempts records which rungs
    were tried and whether each produced content (observability for the fall-through)."""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "empty url", "url": url, "markdown": "", "attempts": []}
    order = [r for r in (prefer or _ladder_for(url)) if r in _RUNGS]
    attempts: list[dict[str, Any]] = []
    last_thin: str | None = None  # best browserless body that was under the floor
    for name in order:
        try:
            md = _RUNGS[name](url)
        except Exception as e:  # noqa: BLE001
            attempts.append({"rung": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            continue
        # Floor-gated tiers (static + lightweight browsers) must clear the thin floor;
        # thin → fall through to the next, heavier rung. crawl4ai/jina accept any body.
        thin = bool(md) and name in _FLOOR_RUNGS and len(md) < THIN_TEXT_CHARS
        if thin and (last_thin is None or len(md) > len(last_thin)):
            last_thin = md
        ok = bool(md) and not thin
        attempts.append({"rung": name, "ok": ok, "chars": len(md) if md else 0,
                         "thin": thin})
        if ok:
            _tier_counts[name] = _tier_counts.get(name, 0) + 1
            otel_emit.record("extracted", {"url": url, "method": name, "chars": len(md),
                                           "rungs_tried": len(attempts)})
            return {"ok": True, "url": url, "markdown": md, "method": name, "attempts": attempts}
    # No rung cleared the floor. A thin-but-real static body still beats the bare
    # SearXNG snippet, so return it (flagged) rather than nothing.
    if last_thin:
        otel_emit.record("extracted", {"url": url, "method": "static_thin",
                                       "chars": len(last_thin), "rungs_tried": len(attempts)})
        return {"ok": True, "url": url, "markdown": last_thin, "method": "static_thin",
                "thin": True, "attempts": attempts}
    otel_emit.record("extract_failed", {"url": url, "rungs_tried": len(attempts)})
    return {"ok": False, "url": url, "markdown": "", "method": None,
            "error": "all extraction rungs failed", "attempts": attempts}


def extract_stats() -> dict[str, Any]:
    try:
        import trafilatura  # noqa: F401
        traf = "available"
    except Exception:  # noqa: BLE001
        traf = "absent (ladder starts at crawl4ai)"
    return {"fast_http": FAST_SCRAPER_URL or "unset (trafilatura is Tier A)",
            "trafilatura": traf, "crawl4ai": "via mcp-docs.fetch_clean",
            "jina": "keyed" if JINA_API_KEY else "keyless (rate-limited)",
            "thin_floor_chars": THIN_TEXT_CHARS,
            "crawl4ai_concurrency": CRAWL4AI_CONCURRENCY,
            "lightpanda_available": _bin_ok(LIGHTPANDA_BIN),
            "obscura_available": _bin_ok(OBSCURA_BIN),
            "browser_tier_counts": {k: _tier_counts.get(k, 0)
                                    for k in ("lightpanda", "obscura", "crawl4ai", "jina")},
            "ladder_default": ["fast_http", "trafilatura", "lightpanda", "obscura",
                               "crawl4ai", "jina"]}
