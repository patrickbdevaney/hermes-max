"""SOTA-pattern local deep-research: plan → develop → explore → verify → synthesize.

Free and fully sovereign. It orchestrates the EXISTING stack — SearXNG + Crawl4AI/
trafilatura (via mcp-docs), the local chat model ($VLLM_BASE_URL), and mcp-codebase-
rag / mcp-knowledge-graph for compounding — into the canonical four-stage deep-
research architecture as bounded, deterministic tools. It is NOT a framework import
(no local-deep-research / LangChain): the research shows a well-configured agent +
SearXNG beats pre-packaged frameworks, so the value-bearing PATTERNS are built here
as native MCP tools.

Engineered against the four named failure modes of open deep-research:
  * echo-chamber retrieval     -> query diversity + URL & n-gram content dedup
  * source-quality / SEO bias  -> authority-aware re-ranking (primary > content farm)
  * planning hallucination     -> external checkable PLAN + intermediate verify_claims
  * sub-agent overspawning      -> hard per-query / per-loop / total source caps

Every backend is reached over the network and degrades gracefully: SearXNG down ->
explore returns nothing (reported); Crawl4AI down -> mcp-docs falls back to
trafilatura; reranker unset -> authority-heuristic-only ranking; $VLLM_BASE_URL
unset -> deterministic (non-LLM) plan/queries/synthesis. Nothing hard-fails.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

try:
    import otel_emit  # best-effort spans to Phoenix; no-op if unavailable
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

try:
    import heartbeat  # watchdog liveness stamp around long inference (no wd import)
except Exception:  # noqa: BLE001
    class _NoHB:
        @staticmethod
        def beat(*_a, **_k):
            return None
    heartbeat = _NoHB()  # type: ignore

import session_state  # per-session research budget/cooldown + exhaustion gate

try:
    import rank as _rank  # embeddings + cosine for intra-request saturation (Phase 1)
except Exception:  # noqa: BLE001
    _rank = None  # type: ignore

try:
    import pool as _pool  # multi-provider cheap fan-out (Phase 3); None → _llm path
except Exception:  # noqa: BLE001
    _pool = None  # type: ignore

# ── config (all local defaults; the chat endpoint is the only "model" dep) ────
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
VLLM_MODEL = os.environ.get("VLLM_MODEL", os.environ.get("DISTILL_MODEL", "/model"))
DOCS_MCP_URL = os.environ.get("DOCS_MCP_URL", "http://127.0.0.1:9109/mcp")
RAG_MCP_URL = os.environ.get("RAG_MCP_URL", "http://127.0.0.1:9102/mcp")
KG_MCP_URL = os.environ.get("KG_MCP_URL", "http://127.0.0.1:9103/mcp")
# Optional cross-encoder rerank endpoint (shared with mcp-codebase-rag). When set,
# explore re-orders candidate sources by it ON TOP of the authority heuristic.
RERANK_BASE_URL = os.environ.get("RERANK_BASE_URL", "").rstrip("/")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "/model")

LLM_TIMEOUT = float(os.environ.get("RESEARCH_LLM_TIMEOUT", "300"))
# Reasoning model: it spends a big hidden budget before the answer (content=None if
# max_tokens is too small). Keep generous (see memory: vllm-reasoning-model).
LLM_MAX_TOKENS = int(os.environ.get("RESEARCH_LLM_MAX_TOKENS", "6000"))

# Bounds — the overspawning guard. Conservative by default, all configurable.
MAX_RESEARCH_LOOPS = int(os.environ.get("MAX_RESEARCH_LOOPS", "3"))
# Phase 1: deep_research is now a QUALITY-GATED wave loop, not a blunt pass count.
# A wave = develop→explore→retain for the current gap set; we stop on full
# coverage, saturation, or the wall budget. The env name is kept for compat.
RESEARCH_MAX_WAVES = int(os.environ.get("RESEARCH_MAX_WAVES", os.environ.get("MAX_RESEARCH_LOOPS", "3")))
MAX_SUBGOALS = int(os.environ.get("RESEARCH_MAX_SUBGOALS", "5"))
QUERIES_PER_SUBGOAL = int(os.environ.get("RESEARCH_QUERIES_PER_SUBGOAL", "4"))
MAX_SOURCES_PER_QUERY = int(os.environ.get("RESEARCH_MAX_SOURCES_PER_QUERY", "3"))
MAX_TOTAL_SOURCES = int(os.environ.get("RESEARCH_MAX_TOTAL_SOURCES", "8"))
# Phase 4.1 — wide fan-out CEILINGS (the clamp limits, not the defaults). Now that
# coverage-gating + the relevance cascade can exploit breadth, the env knobs can dial
# retained sources to the 20–40 the spec targets and per-query candidates wider; the
# real production numbers are set per-deployment (Thor) via env, defaults stay modest.
_PER_QUERY_CEIL = int(os.environ.get("RESEARCH_PER_QUERY_CEIL", "16"))
_TOTAL_CEIL = int(os.environ.get("RESEARCH_TOTAL_CEIL", "60"))
# Candidate fan per query = per-query-cap × this (raises the 300–500 candidate pool).
RESEARCH_CANDIDATE_FANOUT = int(os.environ.get("RESEARCH_CANDIDATE_FANOUT", "3"))
# Bounded scrape-worker pool — concurrent page fetches (tune to backend throughput;
# the spec's 10–16 sweet spot for Crawl4AI headless pages on the Thor). 1 = sequential.
RESEARCH_SCRAPE_CONCURRENCY = max(1, int(os.environ.get("RESEARCH_SCRAPE_CONCURRENCY", "10")))
# The loop is now scrape-bound (wider coverage), so the wall budget is larger.
WALL_BUDGET_S = float(os.environ.get("RESEARCH_WALL_BUDGET_S", "900"))
MIN_INDEPENDENT_SOURCES = int(os.environ.get("RESEARCH_MIN_SOURCES", "2"))
# Saturation thresholds (Phase 1, ported from banyan's intra-session signals).
RESEARCH_MARGINAL_GAIN_FLOOR = float(os.environ.get("RESEARCH_MARGINAL_GAIN_FLOOR", "0.15"))
RESEARCH_DRIFT_COSINE = float(os.environ.get("RESEARCH_DRIFT_COSINE", "0.93"))
# Relevance cascade COARSE rung (Phase 3.2): embed candidate snippets vs the query
# BEFORE any LLM/cross-encoder sees them; drop the off-topic tail. Skipped wholesale
# when no EMBED_BASE_URL (rank._embed → None) so the keyless path is unchanged.
RESEARCH_RELEVANCE_FLOOR = float(os.environ.get("RESEARCH_RELEVANCE_FLOOR", "0.30"))

# ── Adaptive-retrieval CORPUS-FIRST gate (gbrain "brain-first lookup") ────────
# Before the expensive cascade, deep_research asks the RAG store whether prior
# research already covers the question. Gate on the EXTERNAL corpus signal (chunks
# above a similarity threshold), never the model's own confidence.
RESEARCH_CORPUS_HIT_THRESHOLD = float(os.environ.get("RESEARCH_CORPUS_HIT_THRESHOLD", "0.75"))
RESEARCH_CORPUS_MIN_CHUNKS = int(os.environ.get("RESEARCH_CORPUS_MIN_CHUNKS", "2"))
RESEARCH_CORPUS_NS_PREFIX = os.environ.get("RESEARCH_CORPUS_NS_PREFIX", "docs/research")
RESEARCH_CORPUS_FIRST = os.environ.get("RESEARCH_CORPUS_FIRST", "1") not in ("0", "false", "False")
# R-Stage 3 — exhaustion-first ladder + parametric pre-screen (both env-gated).
RESEARCH_BLOCK_PARAMETRIC = os.environ.get("RESEARCH_BLOCK_PARAMETRIC", "1") not in ("0", "false", "False")
RESEARCH_EXHAUSTION_GATE = os.environ.get("RESEARCH_EXHAUSTION_GATE", "1") not in ("0", "false", "False")
# Phase 5 — novel capabilities. Adversarial + temporal are cheap/deterministic → ON;
# ensemble triples retrieval cost and cross-run needs the KG/corpus up → OFF by default.
RESEARCH_ADVERSARIAL = os.environ.get("RESEARCH_ADVERSARIAL", "1") not in ("0", "false", "False")
RESEARCH_TEMPORAL = os.environ.get("RESEARCH_TEMPORAL", "1") not in ("0", "false", "False")
RESEARCH_ENSEMBLE = os.environ.get("RESEARCH_ENSEMBLE", "0") not in ("0", "false", "False")
RESEARCH_CROSS_RUN = os.environ.get("RESEARCH_CROSS_RUN", "0") not in ("0", "false", "False")
RESEARCH_ADVERSARIAL_CLAIMS = int(os.environ.get("RESEARCH_ADVERSARIAL_CLAIMS", "4"))

# Similarity thresholds (Jaccard over word-shingles).
QUERY_DUP_THRESHOLD = float(os.environ.get("RESEARCH_QUERY_DUP_THRESHOLD", "0.8"))
CONTENT_DUP_THRESHOLD = float(os.environ.get("RESEARCH_CONTENT_DUP_THRESHOLD", "0.85"))

STATE_DIR = os.path.expanduser(os.environ.get("RESEARCH_STATE_DIR", "~/.hermes-max/research"))


# ── domain authority heuristic (counters SEO/source-quality bias) ─────────────
# Higher = more primary/authoritative. Used to re-rank candidate sources so a
# primary doc/paper/official repo outranks an SEO content farm for the same query.
_AUTH_TLDS = {".gov": 3, ".edu": 3, ".mil": 3, ".int": 3}
_AUTH_DOMAINS_HIGH = (
    "arxiv.org", "github.com", "gitlab.com", "python.org", "docs.python.org",
    "readthedocs.io", "rust-lang.org", "golang.org", "go.dev", "nodejs.org",
    "developer.mozilla.org", "kubernetes.io", "pytorch.org", "tensorflow.org",
    "w3.org", "ietf.org", "rfc-editor.org", "iso.org", "nist.gov", "acm.org",
    "ieee.org", "nature.com", "sciencedirect.com", "springer.com", "pubmed.ncbi.nlm.nih.gov",
    "openreview.net", "aclanthology.org", "neurips.cc", "huggingface.co",
)
_AUTH_DOMAINS_LOW = (  # SEO/content-farm-ish — downranked, never dropped outright
    "w3schools.com", "geeksforgeeks.org", "tutorialspoint.com", "javatpoint.com",
    "medium.com", "quora.com", "pinterest.com", "slideshare.net", "scribd.com",
    "answers.com", "ehow.com", "wikihow.com", "coursehero.com", "studocu.com",
)


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:  # noqa: BLE001
        return ""


def authority_score(url: str) -> int:
    """0..3 authority for a URL: primary/official/paper high, SEO farm low."""
    host = _domain(url)
    if not host:
        return 1
    for tld, sc in _AUTH_TLDS.items():
        if host.endswith(tld):
            return sc
    if any(host == d or host.endswith("." + d) or d in host for d in _AUTH_DOMAINS_HIGH):
        return 3
    if any(host == d or host.endswith("." + d) for d in _AUTH_DOMAINS_LOW):
        return 0
    # Official-looking docs subdomains get a bump.
    if host.startswith("docs.") or host.startswith("developer.") or ".docs." in host:
        return 2
    return 1


# ── text similarity (echo-chamber dedup) ──────────────────────────────────────
def _shingles(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if (a | b) else 0.0


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        path = p.path.rstrip("/")
        return f"{p.netloc.lower().removeprefix('www.')}{path}".lower()
    except Exception:  # noqa: BLE001
        return url.strip().lower()


# ── MCP client helper (call docs / rag / kg over streamable-http) ─────────────
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
    """Run a coroutine to completion whether or not an event loop is ALREADY
    running in this thread. FastMCP executes tool handlers inside a live event
    loop, so a bare asyncio.run() here raises "asyncio.run() cannot be called
    from a running event loop" — and because _mcp_call swallowed that error,
    EVERY MCP-to-MCP call (search_docs/fetch_clean/ingest_doc) silently returned
    nothing in the live server while passing in main-thread smoke tests (which
    have no running loop). That is the exact "smoke passes, live agent fails"
    trap. When a loop is running we complete the coroutine on a dedicated worker
    thread with its own fresh loop; otherwise asyncio.run() is fine."""
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


# ── injectable backends (so smoke tests can run with NO live services) ────────
# These wrap the existing sovereign loop (mcp-docs) and the chat model. Tests
# monkeypatch them to assert the failure-mode invariants deterministically.
def _search(query: str, limit: int = 8, category: str | None = None) -> list[dict[str, Any]]:
    """SearXNG candidate URLs via mcp-docs.search_docs. -> [{title,url,content}]."""
    r = _mcp_call(DOCS_MCP_URL, "search_docs", {"query": query, "category": category, "limit": limit})
    if not r.get("ok"):
        return []
    res = (r.get("result") or {})
    return res.get("results", []) if isinstance(res, dict) else []


def _fetch_docs(url: str) -> dict[str, Any]:
    """RAW Crawl4AI rung: clean markdown via mcp-docs.fetch_clean (Crawl4AI ->
    trafilatura inside mcp-docs). This is the browser tier — extract.py's crawl4ai
    rung calls THIS (never _fetch) so the tiered ladder can't recurse into itself."""
    r = _mcp_call(DOCS_MCP_URL, "fetch_clean", {"url": url})
    if not r.get("ok"):
        return {"ok": False, "url": url, "error": r.get("error", "fetch failed")}
    res = r.get("result") or {}
    return res if isinstance(res, dict) else {"ok": False, "url": url}


# Tiered fetch is on by default (Phase 4.3): browserless HTTP-first (Tier A), Chromium
# only when static comes back thin. Set RESEARCH_TIERED_FETCH=0 to force the old
# straight-to-Crawl4AI path. Either way _fetch_docs is the browser rung underneath.
RESEARCH_TIERED_FETCH = os.environ.get("RESEARCH_TIERED_FETCH", "1") not in ("0", "false", "")


def _fetch(url: str) -> dict[str, Any]:
    """Fetch + clean a page. By default routes through the TIERED extraction ladder
    (extract.extract_url): fast browserless static first, reserving Chromium for pages
    that actually need JS — the biggest wall-clock win on a wide run. Falls back to the
    raw Crawl4AI rung if the ladder import is unavailable or disabled. The `backend`
    field carries which rung produced the body (observability)."""
    if RESEARCH_TIERED_FETCH:
        try:
            import extract as _extract  # local import: avoids import-time circularity
            res = _extract.extract_url(url)
            if isinstance(res, dict) and res.get("ok"):
                return {"ok": True, "url": url, "markdown": res.get("markdown", ""),
                        "backend": res.get("method"), "thin": res.get("thin", False)}
            # ladder exhausted -> fall through to the raw browser rung below
        except Exception:  # noqa: BLE001
            pass
    return _fetch_docs(url)


def _fetch_many(urls: list[str]) -> list[dict[str, Any]]:
    """Fetch many URLs through a BOUNDED scrape-worker pool (Phase 4.1), preserving
    input order. Concurrency is capped at RESEARCH_SCRAPE_CONCURRENCY so we exploit
    breadth without melting Crawl4AI/SearXNG. Sequential when the cap is 1 or there is
    a single URL. Calls the module-level `_fetch` (so test monkeypatches still apply);
    any per-URL failure becomes a soft {ok:False} rather than sinking the batch."""
    if not urls:
        return []
    workers = min(RESEARCH_SCRAPE_CONCURRENCY, len(urls))
    if workers <= 1:
        return [_fetch(u) for u in urls]
    from concurrent.futures import ThreadPoolExecutor
    out: list[dict[str, Any]] = [{"ok": False, "url": u} for u in urls]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch, u): i for i, u in enumerate(urls)}
        for fut, i in futs.items():
            try:
                out[i] = fut.result()
            except Exception:  # noqa: BLE001
                out[i] = {"ok": False, "url": urls[i], "error": "fetch raised"}
    return out


# Current logical phase, set by the public pipeline functions, used only to LABEL
# the heartbeat in the live log (plan / verify / synthesis / distill / ...). The
# heartbeat fires around EVERY _llm call regardless of the label.
_HB_PHASE = "inference"


def _llm(messages: list[dict], max_tokens: int = LLM_MAX_TOKENS, temperature: float = 0.2) -> str | None:
    """Chat completion via $VLLM_BASE_URL. None if unset/unreachable/empty (the
    reasoning model can spend its whole budget thinking -> content=None).

    A single synthesis/verify/distill inference here can legitimately run minutes
    with no other signal — the finish-line killer. So every blocking call stamps a
    watchdog heartbeat immediately BEFORE it starts and (via finally) immediately
    AFTER it returns or raises. check_stall(task_id=...) then sees a fresh heartbeat
    and never kills a slow-but-alive inference. See heartbeat.py / watchdog_core."""
    if not VLLM_BASE_URL:
        return None
    body = {"model": VLLM_MODEL, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    heartbeat.beat("deep_research", progress=f"{_HB_PHASE}: inference start")
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as c:
            r = c.post(f"{VLLM_BASE_URL}/chat/completions", json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content")
        return content.strip() if content else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        # AFTER the inference returns/raises — proves we reached the finish line.
        heartbeat.beat("deep_research", progress=f"{_HB_PHASE}: inference done")


def _rerank(query: str, documents: list[str]) -> list[int] | None:
    """Optional cross-encoder re-order; None if endpoint unset/unreachable."""
    if not RERANK_BASE_URL or not documents:
        return None
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{RERANK_BASE_URL}/rerank",
                       json={"model": RERANK_MODEL, "query": query,
                             "documents": [d[:2000] for d in documents]})
            r.raise_for_status()
            payload = r.json()
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        if not isinstance(results, list) or not results:
            return None
        order = sorted(results, key=lambda x: x.get("relevance_score", x.get("score", 0.0)), reverse=True)
        out = [int(x["index"]) for x in order if 0 <= int(x.get("index", -1)) < len(documents)]
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _json_from_llm(text: str | None) -> Any:
    """Pull the first JSON array/object out of an LLM reply (tolerant of fences)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    blob = m.group(1).strip() if m else text.strip()
    for candidate in (blob, re.search(r"(\[.*\]|\{.*\})", blob, re.DOTALL)):
        if candidate is None:
            continue
        s = candidate if isinstance(candidate, str) else candidate.group(1)
        try:
            return json.loads(s)
        except Exception:  # noqa: BLE001
            continue
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-")[:80] or "research"


# ── STAGE 1: plan_research ────────────────────────────────────────────────────
_PLAN_SYS = (
    "You are a research planner. Decompose the user's question into 2-5 focused, "
    "COMPLEMENTARY sub-goals (not overlapping), and a short ordered roadmap of how "
    "findings will support the final synthesis. Return STRICT JSON: "
    '{"subgoals": ["...", "..."], "roadmap": "..."}. No prose outside the JSON.'
)


def plan_research(question: str) -> dict[str, Any]:
    """Decompose a question into checkable sub-goals + roadmap, written to external
    PLAN state (so the plan itself is inspectable — planning hallucination is most
    damaging here). Degrades to a single-subgoal plan with no LLM."""
    global _HB_PHASE
    _HB_PHASE = "plan"
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}
    subgoals: list[str] = []
    roadmap = ""
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _PLAN_SYS},
         {"role": "user", "content": question}], temperature=0.2))
    if isinstance(parsed, dict):
        subgoals = [str(s).strip() for s in (parsed.get("subgoals") or []) if str(s).strip()]
        roadmap = str(parsed.get("roadmap", "")).strip()
    if not subgoals:
        subgoals = [question]  # graceful: the question is its own sub-goal
        roadmap = roadmap or "single-pass lookup (no decomposition available)"
    subgoals = subgoals[:MAX_SUBGOALS]

    slug = _slug(question)
    plan_md = (f"# Research plan\n\n**Question:** {question}\n\n## Sub-goals\n"
               + "".join(f"{i + 1}. {s}\n" for i, s in enumerate(subgoals))
               + f"\n## Roadmap\n{roadmap}\n")
    plan_path = ""
    try:
        d = os.path.join(STATE_DIR, slug)
        os.makedirs(d, exist_ok=True)
        plan_path = os.path.join(d, "PLAN.md")
        with open(plan_path, "w") as f:
            f.write(plan_md)
    except Exception:  # noqa: BLE001 - external state is best-effort
        plan_path = ""
    otel_emit.record("research_planned", {"question": question, "subgoals": len(subgoals),
                                          "llm": bool(VLLM_BASE_URL)})
    return {"ok": True, "question": question, "slug": slug, "subgoals": subgoals,
            "roadmap": roadmap, "plan_md": plan_md, "plan_path": plan_path}


# ── STAGE 2: develop_queries (diversity → counters echo chamber) ──────────────
_QUERY_SYS = (
    "Generate diverse, COMPLEMENTARY web-search queries for the sub-goal. Maximize "
    "SOURCE diversity, not just string diversity, two ways:\n"
    "1. PERSPECTIVE (STORM): adopt 2-4 distinct expert personas relevant to the topic "
    "(e.g. implementer, security-auditor, economist, end-user) and write one query "
    "from each — different personas surface different sources.\n"
    "2. ABSTRACTION: vary altitude across three levels — LANDSCAPE (broad overview / "
    "state of the art), MECHANISM (how it works / specific behaviour), and FRONTIER "
    "(failure modes, criticism, head-to-head comparison/benchmark).\n"
    "Prefer queries likely to surface primary/official sources, NOT near-duplicates. "
    "Return a STRICT JSON array of strings only."
)


def _dedup_queries(queries: list[str]) -> list[str]:
    out: list[str] = []
    shings: list[set[str]] = []
    for q in queries:
        q = q.strip()
        if not q:
            continue
        sh = _shingles(q, n=2)
        if any(_jaccard(sh, s) >= QUERY_DUP_THRESHOLD for s in shings):
            continue  # too similar to one we already kept -> diversity guard
        out.append(q)
        shings.append(sh)
    return out


def develop_queries(subgoal: str, n: int = QUERIES_PER_SUBGOAL) -> dict[str, Any]:
    """Diverse, complementary queries (deduped by n-gram similarity) for a sub-goal."""
    subgoal = (subgoal or "").strip()
    if not subgoal:
        return {"ok": False, "error": "empty subgoal", "queries": []}
    n = max(1, min(int(n), 8))
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _QUERY_SYS},
         {"role": "user", "content": f"Sub-goal: {subgoal}\nGenerate {n} queries."}],
        temperature=0.5))
    queries = [str(q).strip() for q in parsed if str(q).strip()] if isinstance(parsed, list) else []
    if not queries:
        # graceful deterministic fallback: three abstraction altitudes + variants,
        # mirroring the perspective/abstraction intent without a model.
        queries = [
            f"{subgoal} overview",                 # landscape
            f"how does {subgoal} work",            # mechanism
            f"{subgoal} failure modes limitations",  # frontier
            f"{subgoal} documentation",
            f"{subgoal} best practices",
        ]
    queries = _dedup_queries(queries)[:n]
    otel_emit.record("queries_developed", {"subgoal": subgoal, "n": len(queries)})
    return {"ok": True, "subgoal": subgoal, "queries": queries}


# ── STAGE 3: explore (dedup + authority rank + bounded breadth) ───────────────
def _relevance_prefilter(query: str, candidates: list[dict[str, Any]],
                         keep_min: int) -> tuple[list[dict[str, Any]], int]:
    """COARSE rung of the relevance cascade (Phase 3.2). Embeds the query + each
    candidate's (title+snippet) in ONE batched call, scores cosine, and drops the
    tail below RESEARCH_RELEVANCE_FLOOR — but never below `keep_min` survivors, so a
    query is never starved. Pure no-op (returns candidates, 0) when:
      • the embed backend is unavailable (rank is None / no EMBED_BASE_URL), or
      • there is nothing to gain (≤ keep_min candidates).
    This is the only near-free filter that runs BEFORE the cross-encoder/LLM, so
    every downstream (paid) rung sees a smaller, on-topic set."""
    if _rank is None or len(candidates) <= max(1, keep_min):
        return candidates, 0
    texts = [f"{c.get('title','')} {c.get('content','')}"[:800] for c in candidates]
    embs = _rank._embed([query] + texts)
    if not embs or len(embs) != len(texts) + 1:
        return candidates, 0  # backend down or shape mismatch → leave untouched
    qv, cvs = embs[0], embs[1:]
    scored = sorted(
        ((_rank._cosine(qv, cv), c) for cv, c in zip(cvs, candidates)),
        key=lambda t: t[0], reverse=True)
    kept = [c for s, c in scored if s >= RESEARCH_RELEVANCE_FLOOR]
    if len(kept) < keep_min:                       # never starve the query
        kept = [c for _, c in scored[:keep_min]]
    return kept, len(candidates) - len(kept)


def explore(queries: list[str], seen_urls: list[str] | None = None,
            max_sources_per_query: int = MAX_SOURCES_PER_QUERY,
            max_total: int = MAX_TOTAL_SOURCES,
            category: str | None = None) -> dict[str, Any]:
    """Iterative web exploration over the sovereign loop. Applies URL + n-gram
    content dedup (break echo chambers), authority-aware re-ranking (primary >
    SEO farm; optional cross-encoder on top), and HARD breadth caps (no
    overspawning). Returns fetched sources with clean markdown + provenance."""
    queries = [q for q in (queries or []) if q and q.strip()]
    if not queries:
        return {"ok": False, "error": "no queries", "sources": []}
    max_sources_per_query = max(1, min(int(max_sources_per_query), _PER_QUERY_CEIL))
    max_total = max(1, min(int(max_total), _TOTAL_CEIL))

    seen_norm: set[str] = {_normalize_url(u) for u in (seen_urls or [])}
    seen_shingles: list[set[str]] = []
    sources: list[dict[str, Any]] = []
    echo_blocked = 0
    low_authority_filtered = 0
    relevance_filtered = 0
    fetch_attempts = 0

    for q in queries:
        if len(sources) >= max_total:
            break
        candidates = _search(q, limit=max(max_sources_per_query * RESEARCH_CANDIDATE_FANOUT, 8), category=category)
        # COARSE relevance rung (Phase 3.2): near-free embedding cosine pre-filter.
        # Embed the candidate snippets + query in ONE call and drop the off-topic
        # tail BEFORE the cross-encoder/LLM ever sees them. Guarded: a None embed
        # backend (no EMBED_BASE_URL) leaves `candidates` untouched. Never zeroes a
        # query — we keep at least the top `max_sources_per_query` by cosine.
        candidates, _rf = _relevance_prefilter(q, candidates, max_sources_per_query)
        relevance_filtered += _rf
        # authority-aware re-rank of candidates for THIS query (primary first).
        for c in candidates:
            c["_authority"] = authority_score(c.get("url", ""))
        order = sorted(range(len(candidates)), key=lambda i: candidates[i]["_authority"], reverse=True)
        rr = _rerank(q, [f"{candidates[i].get('title','')} {candidates[i].get('content','')}"
                         for i in order])
        if rr:  # blend: cross-encoder order, but keep authority as the primary key
            order = [order[j] for j in rr]
        # SELECTION pass (no fetch): URL-dedup + authority-filter, pick up to this
        # query's budget. URLs are reserved in seen_norm here so the concurrent batch
        # (and later queries) never double-fetch the same page.
        budget = min(max_total - len(sources), max_sources_per_query)
        selected: list[dict[str, Any]] = []
        for i in order:
            if len(selected) >= budget:
                break
            cand = candidates[i]
            url = cand.get("url", "")
            if not url:
                continue
            nu = _normalize_url(url)
            if nu in seen_norm:           # URL-level dedup -> break echo chamber
                echo_blocked += 1
                continue
            if cand["_authority"] == 0 and any(s["authority"] >= 2 for s in sources):
                # we already have primary sources; skip a known content farm
                low_authority_filtered += 1
                continue
            seen_norm.add(nu)
            selected.append(cand)
        if not selected:
            continue
        fetch_attempts += len(selected)
        # CONCURRENT fetch through the bounded scrape pool, then process IN ORDER so
        # content-shingle dedup + caps stay deterministic regardless of fetch timing.
        fetched_all = _fetch_many([c["url"] for c in selected])
        for cand, fetched in zip(selected, fetched_all):
            if len(sources) >= max_total:
                break
            url = cand["url"]
            md = fetched.get("markdown", "") if fetched.get("ok") else ""
            sh = _shingles(md or cand.get("content", ""), n=3)
            if md and any(_jaccard(sh, prev) >= CONTENT_DUP_THRESHOLD for prev in seen_shingles):
                echo_blocked += 1          # near-duplicate CONTENT across a different URL
                continue
            if sh:
                seen_shingles.append(sh)
            sources.append({
                "url": url,
                "title": cand.get("title", ""),
                "domain": _domain(url),
                "authority": cand["_authority"],
                "query": q,
                "fetched": bool(fetched.get("ok")),
                "backend": fetched.get("backend"),
                "snippet": (cand.get("content", "") or "")[:500],
                "markdown": md[:20000],
                "chars": len(md),
            })

    otel_emit.record("sources_explored", {
        "queries": len(queries), "sources": len(sources), "fetch_attempts": fetch_attempts,
        "echo_chamber_blocked": echo_blocked, "low_authority_filtered": low_authority_filtered,
        "relevance_prefiltered": relevance_filtered,
    })
    if echo_blocked:
        otel_emit.record("echo_chamber_blocked", {"count": echo_blocked})
    if low_authority_filtered:
        otel_emit.record("low_authority_filtered", {"count": low_authority_filtered})
    if relevance_filtered:
        otel_emit.record("relevance_prefiltered", {"count": relevance_filtered})
    return {"ok": True, "queries": queries, "count": len(sources), "sources": sources,
            "seen_urls": sorted(seen_norm), "echo_chamber_blocked": echo_blocked,
            "low_authority_filtered": low_authority_filtered,
            "relevance_prefiltered": relevance_filtered}


# ── STAGE 4a: verify_claims (the differentiator — ≥2 independent sources) ──────
_VERIFY_SYS = (
    "You are a fact-checker. For the claim and the candidate source excerpt, answer "
    "with STRICT JSON {\"label\": \"supports\"|\"contradicts\"|\"neutral\"}. "
    "'supports' only if the excerpt clearly backs the claim."
)


def _label_support(claim: str, snippet: str) -> str:
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _VERIFY_SYS},
         {"role": "user", "content": f"Claim: {claim}\n\nSource excerpt:\n{snippet[:2000]}"}],
        temperature=0, max_tokens=2000))
    if isinstance(parsed, dict):
        lab = str(parsed.get("label", "")).lower().strip()
        if lab in ("supports", "contradicts", "neutral"):
            return lab
    return "unchecked"


# Phase 2.2 — BATCHED entailment. The cost that makes commercial services skip
# verification is nearly free for us: pack many (claim, source) pairs into one
# cheap call. A ~50-claim × 2-source report ≈ 100 pairs → a handful of batched
# calls, seconds across the pool, ≈$0.
_VERIFY_BATCH_SYS = (
    "You are a fact-checker. For EACH numbered (claim, source excerpt) pair, decide "
    "whether the excerpt SUPPORTS the claim. Return a STRICT JSON array aligned to the "
    "inputs by index: [{\"i\": 0, \"label\": \"supports\"|\"contradicts\"|\"neutral\"}, ...]. "
    "Mark 'supports' only if the excerpt clearly backs the claim."
)
_VERIFY_BATCH_SIZE = int(os.environ.get("RESEARCH_VERIFY_BATCH", "8"))


def _label_support_batch(pairs: list[tuple[str, str]]) -> list[str]:
    """Label many (claim, snippet) pairs. Packs RESEARCH_VERIFY_BATCH pairs per
    cheap call; when the multi-provider pool is configured the chunk-calls fan out
    CONCURRENTLY across lanes (Phase 3) — else sequential _llm. Deterministic
    fallback ('unchecked') when no model. Order-preserving."""
    out: list[str] = ["unchecked"] * len(pairs)
    if not pairs or not (VLLM_BASE_URL or (_pool and _pool.available())):
        return out
    chunks = [pairs[s:s + _VERIFY_BATCH_SIZE] for s in range(0, len(pairs), _VERIFY_BATCH_SIZE)]
    prompts = ["\n\n".join(f"[{i}] CLAIM: {c}\nEXCERPT:\n{s[:1200]}" for i, (c, s) in enumerate(ch)) for ch in chunks]
    if _pool and _pool.available():
        replies = _pool.map_cheap(prompts, system=_VERIFY_BATCH_SYS, temperature=0, max_tokens=2000)
    else:
        replies = [_llm([{"role": "system", "content": _VERIFY_BATCH_SYS}, {"role": "user", "content": p}],
                        temperature=0, max_tokens=2000) for p in prompts]
    for ci, reply in enumerate(replies):
        parsed = _json_from_llm(reply)
        base = ci * _VERIFY_BATCH_SIZE
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                i = item.get("i")
                lab = str(item.get("label", "")).lower().strip()
                if isinstance(i, int) and 0 <= i < len(chunks[ci]) and lab in ("supports", "contradicts", "neutral"):
                    out[base + i] = lab
    return out


def verify_claims(claims: list[dict], min_sources: int = MIN_INDEPENDENT_SOURCES) -> dict[str, Any]:
    """Cross-check each material claim against >= min_sources INDEPENDENT sources
    (independent = distinct domain, post-dedup). Flags single-sourced / conflicting
    rather than asserting them. Intermediate verification — a wrong plan/finding is
    caught HERE, before synthesis (planning-hallucination guard). When the chat
    model is available it also entails each (claim, source) pair; otherwise it
    counts independent-domain support deterministically."""
    global _HB_PHASE
    _HB_PHASE = "verify"
    claims = claims or []
    # ── pass 1: build per-claim one-vote-per-domain source sets (no LLM yet) ──
    per_claim: list[dict] = []
    pairs: list[tuple[str, str]] = []          # flat (claim, snippet) for batched entailment
    pair_ref: list[tuple[int, str]] = []       # back-ref: (claim_index, domain)
    for c in claims:
        claim = str(c.get("claim", "")).strip()
        srcs = c.get("sources", []) or []
        if not claim:
            continue
        ci = len(per_claim)
        by_domain: dict[str, dict] = {}
        for s in srcs:
            if isinstance(s, str):
                url, snip = s, ""
            elif isinstance(s, dict):
                url, snip = s.get("url", ""), s.get("snippet", s.get("markdown", ""))
            else:
                continue
            dom = _domain(url)
            if not dom or dom in by_domain:
                continue  # one vote per domain -> independence
            by_domain[dom] = {"url": url, "label": "unchecked", "snippet": snip}
            if snip:
                pair_ref.append((ci, dom))
                pairs.append((claim, snip))
        per_claim.append({"claim": claim, "by_domain": by_domain})

    # ── pass 2: ONE batched entailment sweep over all (claim, source) pairs ──
    labels = _label_support_batch(pairs)
    for (ci, dom), lab in zip(pair_ref, labels):
        per_claim[ci]["by_domain"][dom]["label"] = lab

    # ── pass 3: assemble verdicts ──
    out: list[dict[str, Any]] = []
    for pc in per_claim:
        claim = pc["claim"]
        by_domain = pc["by_domain"]
        contradicts = sum(1 for d in by_domain.values() if d["label"] == "contradicts")
        independent = len(by_domain)
        support_n = len([d for d in by_domain.values() if d["label"] in ("supports", "unchecked")])
        if contradicts and support_n:
            status = "conflicting"
        elif support_n >= min_sources:
            status = "well-supported"
        else:
            status = "single-sourced"
        out.append({
            "claim": claim, "status": status,
            "independent_sources": independent,
            "support_count": support_n,
            "contradictions": contradicts,
            "sources": [d["url"] for d in by_domain.values()],
            "entailed": any(d["label"] == "supports" for d in by_domain.values()),
        })
    otel_emit.record("claims_verified", {
        "claims": len(out),
        "well_supported": sum(1 for o in out if o["status"] == "well-supported"),
        "single_sourced": sum(1 for o in out if o["status"] == "single-sourced"),
        "conflicting": sum(1 for o in out if o["status"] == "conflicting"),
    })
    return {"ok": True, "verified": out, "min_sources": min_sources}


# ── STAGE 4b: synthesize (citation-backed report) ─────────────────────────────
_SYNTH_SYS = (
    "You are a research synthesizer. Using ONLY the verified findings, write a "
    "structured markdown report that answers the question. EVERY claim must cite its "
    "source URL inline like [1], with a numbered Sources list at the end. Clearly "
    "label what is well-supported vs single-sourced vs conflicting. Preserve quotes, "
    "code, and figures VERBATIM (compress, do not paraphrase technical content). End "
    "with a short 'Confidence & gaps' section. Do NOT invent facts or sources."
)

# ── Phase 2.1: hierarchical map→reduce ───────────────────────────────────────
# MAP (cheap/local, per cluster): dense, citation-TAGGED evidence briefs that
# CARRY CHUNK-IDS THROUGH (so reduce + verify always resolve to source).
# REDUCE (the ONE frontier call): the long-context conductor/escalation tier
# takes the briefs + plan + verdicts and writes the report — everything before
# reduce is cheap/local; reduce is the model-tier boundary.
RESEARCH_BRIEF_CLUSTER = int(os.environ.get("RESEARCH_BRIEF_CLUSTER", "12"))
_MAP_SYS = (
    "Summarize the verified findings into a DENSE, citation-TAGGED evidence brief. "
    "For each finding keep: the claim, its supporting source ids in [brackets] EXACTLY "
    "as given (never drop or invent ids), and its verdict label (well-supported / "
    "single-sourced / conflicting). Compact markdown bullets, no preamble."
)


def _evidence_briefs(question: str, findings: list[dict], idx: dict[str, int]) -> list[str]:
    """Leaf summaries — one per cluster of findings. Deterministic structured brief
    (claim → [source ids] → verdict); a cheap MAP call densifies it (ids preserved),
    falling back to the structured form. Reduce sees briefs, never raw pages."""
    briefs: list[str] = []
    for start in range(0, len(findings), RESEARCH_BRIEF_CLUSTER):
        group = findings[start:start + RESEARCH_BRIEF_CLUSTER]
        structured = "\n".join(
            f"- ({f.get('status')}) {f.get('claim')} "
            + " ".join(f"[{idx[u]}]" for u in f.get("sources", []) if u in idx)
            for f in group)
        dens = _llm([{"role": "system", "content": _MAP_SYS},
                     {"role": "user", "content": f"Question: {question}\n\nFindings:\n{structured}"}],
                    temperature=0.1, max_tokens=2000)
        briefs.append(dens.strip() if dens else structured)
    return briefs


def _reduce(question: str, briefs: list[str], citations: list[str], plan: dict | None):
    """The single frontier long-context call: conductor steer/escalation tier first
    (better long-context synthesis), local frontier next, deterministic last
    (handled by the caller). Returns (report_md|None, backend)."""
    roadmap = (plan or {}).get("roadmap", "")
    user = (f"Question: {question}\n\nPlan/roadmap: {roadmap}\n\n"
            f"Evidence briefs (citation-tagged, verdict-labelled):\n" + "\n\n".join(briefs)
            + "\n\nSources (numbered):\n" + "\n".join(f"[{i + 1}] {u}" for i, u in enumerate(citations)))
    # rung 1 — conductor steer (cheap cloud / frontier long-context)
    try:
        r = _mcp_call(ESCALATION_MCP_URL, "conductor_steer",
                      {"prompt": f"{_SYNTH_SYS}\n\n{user}", "max_tokens": LLM_MAX_TOKENS})
        res = (r.get("result") or {}) if isinstance(r, dict) else {}
        if r.get("ok") and isinstance(res, dict) and not res.get("proceed_local") and res.get("content"):
            return str(res["content"]).strip(), "conductor_steer"
    except Exception:  # noqa: BLE001
        pass
    # rung 2 — local frontier
    rep = _llm([{"role": "system", "content": _SYNTH_SYS}, {"role": "user", "content": user}],
               temperature=0.2)
    if rep:
        return rep, "local"
    # rung 3 — deterministic (caller builds the cited-bullet fallback)
    return None, "deterministic"


def synthesize(question: str, verified_findings: list[dict], plan: dict | None = None) -> dict[str, Any]:
    """Compile a structured, citation-backed report distinguishing well-supported /
    single-sourced / conflicting findings. Degrades (no LLM) to a deterministic
    cited bullet list — still honest, still every-claim-to-a-URL."""
    global _HB_PHASE
    _HB_PHASE = "synthesis"
    verified_findings = verified_findings or []
    citations: list[str] = []
    seen_c: set[str] = set()
    for f in verified_findings:
        for u in f.get("sources", []):
            if u and u not in seen_c:
                seen_c.add(u)
                citations.append(u)

    confidence = "low"
    well = sum(1 for f in verified_findings if f.get("status") == "well-supported")
    if verified_findings:
        ratio = well / len(verified_findings)
        confidence = "high" if ratio >= 0.66 else ("medium" if ratio >= 0.33 else "low")
    gaps = [f["claim"] for f in verified_findings if f.get("status") != "well-supported"][:10]

    # MAP → REDUCE: dense citation-tagged briefs (cheap/local), then ONE frontier
    # reduce call over the briefs + plan + verdicts (not raw pages).
    idx = {u: i + 1 for i, u in enumerate(citations)}
    briefs = _evidence_briefs(question, verified_findings, idx)
    report, reduce_backend = _reduce(question, briefs, citations, plan)
    if not report:  # deterministic, still-cited fallback
        lines = [f"# Research brief: {question}", ""]
        for f in verified_findings:
            cites = " ".join(f"[{idx[u]}]" for u in f.get("sources", []) if u in idx)
            lines.append(f"- ({f.get('status')}) {f.get('claim')} {cites}")
        lines += ["", "## Sources"] + [f"[{i + 1}] {u}" for i, u in enumerate(citations)]
        lines += ["", f"## Confidence & gaps", f"Confidence: {confidence}.",
                  "Gaps (not well-supported): " + ("; ".join(gaps) if gaps else "none")]
        report = "\n".join(lines)
        synthesized = False
    else:
        synthesized = True
    # ── R-Stage 4: GAP ANALYSIS, not confidence ──────────────────────────────
    # The old confidence=low/high was misread by the agent as "retry". Replace it
    # with gbrain-style quality metrics + an `actionable` flag + a `gap_note` that
    # says what's covered and what isn't. A low-corroboration synthesis is STILL
    # actionable (claims are single-sourced by design after echo-chamber dedup) —
    # the agent proceeds and notes the gaps, exactly like gbrain's "heads up: the
    # brain doesn't know X yet". The ONLY non-actionable case is a genuinely
    # empty/broken result (no sources AND no claims). The agent NEVER retries
    # deep_research on the quality score; it proceeds, or uses a lighter tool for a
    # specific follow-up. The real quality gate is the verify gate on the code.
    claims_total = len(verified_findings)
    claims_corroborated = sum(1 for f in verified_findings if f.get("status") == "well-supported")
    claims_single_sourced = sum(1 for f in verified_findings if f.get("status") == "single-sourced")
    claims_conflicting = sum(1 for f in verified_findings if f.get("status") == "conflicting")
    citation_count = len(citations)
    unsupported_rate = (round((claims_single_sourced + claims_conflicting) / claims_total, 3)
                        if claims_total else 0.0)
    actionable = bool(report) and (citation_count > 0 or claims_total > 0)

    if not actionable:
        gap_note = ("NOT actionable — research returned no usable sources or claims "
                    "(0 citations, 0 claims). Use a lighter targeted tool or proceed "
                    "from parametric knowledge; do not re-run deep_research.")
    else:
        bits = [f"covers {claims_total} claim(s); {claims_corroborated} corroborated "
                f"(>=2 independent sources), {claims_single_sourced} single-sourced, "
                f"{claims_conflicting} conflicting; {citation_count} citation(s)"]
        if gaps:
            bits.append("not fully corroborated: " + "; ".join(gaps[:5]))
        bits.append("proceed with implementation and note these as risks; do NOT "
                    "re-run deep_research — use a lighter tool for any specific follow-up")
        gap_note = ". ".join(bits)

    if actionable and report and "Research sufficiency" not in report:
        report += (f"\n\n> _Research sufficiency: **{'actionable' if actionable else 'not actionable'}**. "
                   f"{gap_note}_")
    otel_emit.record("report_synthesized", {
        "question": question, "citations": citation_count, "claims_total": claims_total,
        "claims_corroborated": claims_corroborated, "unsupported_rate": unsupported_rate,
        "actionable": actionable, "llm": synthesized, "reduce_backend": reduce_backend})
    return {"ok": True, "question": question, "report_md": report, "synthesized": synthesized,
            "reduce_backend": reduce_backend,
            "citations": citations,
            # gap-analysis quality metrics (NOT a retry-triggering confidence)
            "actionable": actionable, "gap_note": gap_note,
            "citation_count": citation_count, "claims_total": claims_total,
            "claims_corroborated": claims_corroborated,
            "claims_single_sourced": claims_single_sourced,
            "claims_conflicting": claims_conflicting, "unsupported_rate": unsupported_rate,
            "gaps": gaps}


# ── claim extraction (sources -> candidate claims grouped by support) ─────────
_EXTRACT_SYS = (
    "Extract the atomic, checkable factual claims from the sources that help answer "
    "the question. For EACH claim, list the source URLs (from those provided) that "
    "support it. Return STRICT JSON: [{\"claim\": \"...\", \"source_urls\": [\"...\"]}]. "
    "Only use the provided URLs; do not invent."
)


def _extract_claims(question: str, sources: list[dict]) -> list[dict]:
    if not sources:
        return []
    catalog = "\n\n".join(
        f"URL: {s['url']}\nTITLE: {s.get('title','')}\nCONTENT:\n{(s.get('markdown') or s.get('snippet',''))[:4000]}"
        for s in sources)[:24000]
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _EXTRACT_SYS},
         {"role": "user", "content": f"Question: {question}\n\nSources:\n{catalog}"}],
        temperature=0.1))
    by_url = {s["url"]: s for s in sources}
    claims: list[dict] = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim", "")).strip()
            urls = [u for u in (item.get("source_urls") or []) if u in by_url]
            if claim:
                claims.append({"claim": claim,
                               "sources": [{"url": u, "snippet": (by_url[u].get("markdown") or by_url[u].get("snippet", ""))[:2000]} for u in urls]})
    if not claims:  # deterministic fallback: one claim per source (its title)
        for s in sources:
            title = s.get("title") or s.get("snippet", "")[:120]
            if title:
                claims.append({"claim": title, "sources": [{"url": s["url"], "snippet": s.get("snippet", "")}]})
    return claims


# ── R-Stage 3: rule-based research-need classifier (NO LLM) ───────────────────
# Pre-screens a question into parametric / targeted / synthesis on cheap lexical
# signals (NOT the model's self-judgment). Parametric (textbook algorithms, "how
# does X work", standard patterns) warrants NO research tool — implement from
# parametric knowledge. Only synthesis (novel/recent/multi-source/exact-spec)
# warrants Tier-4 deep_research.
_SYNTHESIS_SIGNALS = (
    "current state", "state of the art", "state-of-the-art", "latest", "recent",
    "newest", "compare", " vs ", "versus", "trade-off", "tradeoff", "survey",
    "landscape", "ecosystem", "which is better", "best approach", "best practice",
    "emerging", "novel", "cutting edge", "2024", "2025", "2026", "benchmark",
    "test vector", "test-vector", "specification", "whitepaper", "rfc ", "eip ",
    "erc ", "protocol spec", "primary literature", "reconcile", "triangulate",
)
_PARAMETRIC_ALGOS = (
    "miller-rabin", "miller rabin", "quicksort", "merge sort", "mergesort",
    "binary search", "bubble sort", "insertion sort", "dijkstra", "bellman-ford",
    "breadth-first", "depth-first", "bfs", "dfs", "a-star", "a*", "dynamic programming",
    "memoization", "hash table", "hash map", "linked list", "binary tree", "heap sort",
    "fibonacci", "sieve of eratosthenes", "euclidean algorithm", "gcd", "fizzbuzz",
    "two pointer", "sliding window", "kmp", "rabin-karp", "union-find", "topological sort",
    "newton's method", "gradient descent", "linear regression", "k-means",
)
# NOTE: bare definitional frames ("what is a/the X") are NOT listed here. A
# definitional question only warrants Tier-0 when X is a KNOWN textbook algorithm
# — and that case is already caught by _PARAMETRIC_ALGOS above. A generic "what is
# a <topic>" may well be a novel/external subject outside pretraining (e.g. "what
# is a Merkle tree in cryptography" is a legitimate, source-backable research ask),
# so we must NOT hard-block it on the frame alone. Only implement-style frames —
# where the model is asked to PRODUCE textbook code it already knows — stay here.
_PARAMETRIC_FRAMES = (
    "how does", "how do i implement", "how to implement", "implement a", "implement the",
    "explain how", "write a function",
    "standard way to", "common pattern", "textbook",
)
_TARGETED_SIGNALS = (
    "what version", "which version", "api", "parameter", "return value", "syntax",
    "flag", "option", "default value", "exact value", "signature", "endpoint",
    "config", "environment variable", "error code", "status code",
)


def classify_research_need(question: str) -> dict[str, Any]:
    """Return {class: parametric|targeted|synthesis, signals, block} for `question`.
    Precedence: a synthesis signal wins (open-ended/novel/recent → allow Tier 4);
    else a textbook-algorithm or how-does-X-work frame → parametric (block research);
    else a precise-fact frame → targeted (lighter tools, not Tier 4); else default
    synthesis-eligible (the agent chose research; don't over-block)."""
    q = (question or "").lower()
    # Precedence: synthesis wins; then DEFINITE parametric (named textbook algorithm);
    # then targeted (precise fact — a broad "what is the" frame must NOT mask a
    # "return value"/"exact value" lookup); then generic parametric frames; else
    # default synthesis-eligible (don't over-block a deliberate research call).
    syn = [s.strip() for s in _SYNTHESIS_SIGNALS if s in q]
    if syn:
        return {"class": "synthesis", "signals": syn[:5], "block": False}
    algo = [s for s in _PARAMETRIC_ALGOS if s in q]
    if algo:
        return {"class": "parametric", "signals": algo[:5], "block": True}
    tgt = [s for s in _TARGETED_SIGNALS if s in q]
    if tgt:
        return {"class": "targeted", "signals": tgt[:5], "block": False}
    frame = [s for s in _PARAMETRIC_FRAMES if s in q]
    if frame:
        return {"class": "parametric", "signals": frame[:5], "block": True}
    return {"class": "synthesis", "signals": [], "block": False}


# ── M-Stage 5: CitationAgent pass ─────────────────────────────────────────────
# Anthropic's multi-agent research system runs a final CitationAgent after the
# research loop: it checks each claim in the report against the source documents and
# flags claims not directly supported. Run it ONCE after synthesis. Route to the
# conductor's steer tier (cheap cloud, better attribution) when available; fall back
# to the local model. Conservative: mark a claim unsupported if the source is ambiguous.
ESCALATION_MCP_URL = os.environ.get(
    "ESCALATION_MCP_URL", f"http://127.0.0.1:{os.environ.get('MCP_ESCALATION_PORT', '9105')}/mcp")
CITATION_VERIFY = os.environ.get("RESEARCH_CITATION_VERIFY", "1") not in ("0", "false", "False")

_CITE_SYS = (
    "You are a citation auditor. Given a synthesized research REPORT and the SOURCE "
    "passages it was built from, check EACH factual claim in the report against the "
    "sources. Be CONSERVATIVE: mark a claim unsupported if no source directly and "
    "unambiguously supports it. Return STRICT JSON only: {\"supported_claims\": <int>, "
    "\"unsupported_claims\": [\"<claim text>\", ...], \"source_attribution\": "
    "{\"<claim text>\": \"<source url or id>\"}}. No prose outside the JSON."
)


def _citation_verify(report_md: str, sources: list[dict]) -> dict[str, Any]:
    """Audit the report's claims against the sources. Tries the conductor steer tier
    first (better attribution), falls back to the local model. Returns
    {supported_claims, unsupported_claims, source_attribution, sources_checked,
    backend}. Best-effort: on any failure returns an empty/neutral result so the
    research run never fails on the citation pass."""
    if not report_md or not sources:
        return {"supported_claims": 0, "unsupported_claims": [], "source_attribution": {},
                "sources_checked": 0, "backend": "skipped"}
    catalog = "\n\n".join(
        f"[{i + 1}] {s.get('url','')} :: {(s.get('markdown') or s.get('title') or s.get('snippet',''))[:1500]}"
        for i, s in enumerate(sources))[:18000]
    user = f"REPORT:\n{report_md[:12000]}\n\nSOURCES:\n{catalog}"
    backend = "local"
    raw = None
    # 1) conductor steer (cheap cloud) — better attribution than the local 35B
    try:
        r = _mcp_call(ESCALATION_MCP_URL, "conductor_steer",
                      {"prompt": f"{_CITE_SYS}\n\n{user}", "max_tokens": 1500})
        res = (r.get("result") or {}) if isinstance(r, dict) else {}
        if r.get("ok") and isinstance(res, dict) and not res.get("proceed_local") and res.get("content"):
            raw, backend = res["content"], "conductor_steer"
    except Exception:  # noqa: BLE001
        raw = None
    # 2) local fallback
    if not raw:
        raw = _llm([{"role": "system", "content": _CITE_SYS},
                    {"role": "user", "content": user}], temperature=0)
        backend = "local"
    parsed = _json_from_llm(raw)
    if not isinstance(parsed, dict):
        return {"supported_claims": 0, "unsupported_claims": [], "source_attribution": {},
                "sources_checked": len(sources), "backend": backend, "parse_failed": True}
    unsupported = [str(c) for c in (parsed.get("unsupported_claims") or []) if str(c).strip()]
    attribution = parsed.get("source_attribution") if isinstance(parsed.get("source_attribution"), dict) else {}
    try:
        supported = int(parsed.get("supported_claims") or 0)
    except Exception:  # noqa: BLE001
        supported = 0
    return {"supported_claims": supported, "unsupported_claims": unsupported,
            "source_attribution": attribution, "sources_checked": len(sources), "backend": backend}


# ── PHASE 1: in-request iterative coverage loop ──────────────────────────────
# Coverage = fraction of sub-goals with >= MIN_INDEPENDENT_SOURCES independent
# domains (one vote per domain, the verify-gate independence rule). Each retained
# source is tagged with its sub-goal (via the query→subgoal map) in the loop.
def _coverage_state(subgoals: list[str], sources: list[dict]) -> dict[str, set]:
    cov: dict[str, set] = {sg: set() for sg in subgoals}
    for s in sources:
        sg = s.get("_subgoal")
        dom = s.get("domain") or _domain(s.get("url", ""))
        if sg in cov and dom:
            cov[sg].add(dom)
    return cov


def _coverage_fraction(cov: dict[str, set]) -> float:
    if not cov:
        return 0.0
    covered = sum(1 for d in cov.values() if len(d) >= MIN_INDEPENDENT_SOURCES)
    return covered / len(cov)


def _assess_saturation(new_sources: list[dict], prior_total: int, centroid):
    """Port banyan's intra-session signals to intra-request: marginal-gain decline
    (this wave added < floor of prior total) OR embedding drift (new chunks too
    close to the run's evidence centroid). Returns (saturated, new_centroid, info)."""
    new_unique = len(new_sources)
    gain = (new_unique / prior_total) if prior_total else 1.0
    marginal = prior_total > 0 and gain < RESEARCH_MARGINAL_GAIN_FLOOR
    drift = False
    new_centroid = centroid
    if _rank is not None and new_sources:
        texts = [(s.get("markdown") or s.get("snippet", ""))[:2000] for s in new_sources]
        try:
            vecs = _rank._embed(texts)
        except Exception:  # noqa: BLE001
            vecs = None
        if vecs:
            mean = [sum(col) / len(vecs) for col in zip(*vecs)]
            if centroid is not None:
                sims = [_rank._cosine(v, centroid) for v in vecs]
                if sims and (sum(sims) / len(sims)) >= RESEARCH_DRIFT_COSINE:
                    drift = True
            new_centroid = mean if centroid is None else [0.7 * a + 0.3 * b for a, b in zip(centroid, mean)]
    return (marginal or drift), new_centroid, {
        "marginal_gain": round(gain, 3), "marginal_saturated": marginal, "drift_saturated": drift}


_REFLECT_SYS = (
    "You are a research gap analyst (STORM expert-questioning + Self-Ask). Given the "
    "sub-goals, the evidence retained so far (domains per sub-goal), and which "
    "sub-goals are under-covered, return STRICT JSON: {\"uncovered_subgoals\": [...], "
    "\"unresolved_contradictions\": [...], \"followup_queries\": [\"targeted query for "
    "the gap\", ...]}. followup_queries must target the GAPS specifically — do not "
    "restate covered ground. No prose outside the JSON."
)


def reflect_gaps(subgoals: list[str], sources: list[dict], coverage: dict[str, set]) -> dict[str, Any]:
    """After a wave, decide what's still missing and propose targeted follow-up
    queries. Cheap, short-context (the canonical 'mildly intelligent rote call').
    Deterministic fallback: a sub-goal is uncovered if < MIN_INDEPENDENT_SOURCES
    independent domains; follow-ups are its abstraction-altitude variants."""
    uncovered = [sg for sg in subgoals if len(coverage.get(sg, set())) < MIN_INDEPENDENT_SOURCES]
    evidence = "\n".join(
        f"- {sg}: {len(coverage.get(sg, set()))} domain(s) [{', '.join(sorted(coverage.get(sg, set()))[:4])}]"
        for sg in subgoals)
    parsed = _json_from_llm(_llm(
        [{"role": "system", "content": _REFLECT_SYS},
         {"role": "user", "content":
            f"Sub-goals + coverage:\n{evidence}\n\nUnder-covered: {uncovered}\n"
            "Propose follow-up queries that close the gaps."}],
        temperature=0.3, max_tokens=2000))
    if isinstance(parsed, dict):
        fq = [str(q).strip() for q in (parsed.get("followup_queries") or []) if str(q).strip()]
        if fq:
            unc = [str(s).strip() for s in (parsed.get("uncovered_subgoals") or []) if str(s).strip()] or uncovered
            contra = [str(c).strip() for c in (parsed.get("unresolved_contradictions") or []) if str(c).strip()]
            return {"uncovered_subgoals": unc, "unresolved_contradictions": contra,
                    "followup_queries": _dedup_queries(fq)[:6]}
    # deterministic: abstraction-altitude variants of each uncovered sub-goal
    fq = []
    for sg in uncovered:
        fq += [f"{sg} overview", f"how does {sg} work", f"{sg} failure modes limitations"]
    return {"uncovered_subgoals": uncovered, "unresolved_contradictions": [],
            "followup_queries": _dedup_queries(fq)[:6]}


# ── ORCHESTRATOR: deep_research ───────────────────────────────────────────────
def deep_research(question: str, max_loops: int = MAX_RESEARCH_LOOPS,
                  max_total_sources: int = MAX_TOTAL_SOURCES,
                  category: str | None = None, compound: bool = True) -> dict[str, Any]:
    """plan -> (develop -> explore -> verify) x bounded loops -> synthesize.

    Single-threaded by default (no sub-agent overspawning); bounded by max_loops,
    total-source cap, and a wall-clock budget. Writes the final brief + key
    entities into RAG/KG so a later related run starts ahead (compounding)."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}
    t0 = time.monotonic()
    max_loops = max(1, min(int(max_loops), 8))

    # ── TIER-0 PARAMETRIC pre-screen (R-Stage 3; cheapest gate, no tool call) ─
    # A textbook algorithm / "how does X work" / standard pattern warrants NO
    # research tool — implement from parametric knowledge. Hard-block it from the
    # cascade. Gates on a cheap lexical signal, not the model's self-judgment.
    cls = classify_research_need(question)
    otel_emit.record("query_classification", {
        "tool": "deep_research", "class": cls["class"],
        "signals": ", ".join(cls["signals"]) or None, "blocked": cls["block"] and RESEARCH_BLOCK_PARAMETRIC})
    if cls["block"] and RESEARCH_BLOCK_PARAMETRIC:
        return {"ok": False, "gated": True, "gate_reason": "parametric",
                "classification": cls["class"], "signals": cls["signals"],
                "error": ("This is a textbook/parametric topic (signals: "
                          f"{', '.join(cls['signals'])}) — implement directly from "
                          "parametric knowledge; no research tool is warranted."),
                "use_instead": "implement from parametric knowledge (Tier 0 — no tool call)"}

    # ── CORPUS-FIRST gate (adaptive retrieval / gbrain brain-first lookup) ────
    # Ask the RAG store whether prior research already covers this question. A hit
    # (>= MIN_CHUNKS chunks above the similarity THRESHOLD in the research namespace)
    # answers instantly from the corpus and SKIPS the expensive cascade entirely.
    if RESEARCH_CORPUS_FIRST:
        pc = _mcp_call(RAG_MCP_URL, "corpus_hit_check", {
            "query": question, "namespace_prefix": RESEARCH_CORPUS_NS_PREFIX,
            "threshold": RESEARCH_CORPUS_HIT_THRESHOLD,
            "min_chunks": RESEARCH_CORPUS_MIN_CHUNKS})
        res = (pc.get("result") or {}) if isinstance(pc, dict) else {}
        hit = bool(res.get("hit"))
        chunks = res.get("chunks") or []
        session_state.mark_corpus_checked()  # precondition satisfied for this session
        otel_emit.record("corpus_precheck", {
            "tool": "deep_research", "question": question,
            "hit": hit, "chunks_found": res.get("chunks_found", 0),
            "threshold": res.get("threshold"), "scoring": res.get("scoring"),
            "launched_cascade": not hit})
        if hit and chunks:
            n = res.get("chunks_found", len(chunks))
            report = (f"# Prior research: {question}\n\n"
                      f"> _Answered from the existing corpus — {n} prior research "
                      f"chunk(s) above similarity {res.get('threshold')} covered this. "
                      f"No external research cascade was launched (corpus-first gate)._\n\n"
                      + "\n\n".join(
                          f"## Source: {c.get('source','?')} "
                          f"(namespace {c.get('namespace','?')}, score {c.get('score')})\n\n"
                          f"{c.get('snippet','')}" for c in chunks))
            return {
                "ok": True, "question": question,
                "answered_from_corpus": True, "launched_cascade": False,
                "corpus_chunks": chunks, "report_md": report,
                "actionable": True, "confidence_is_advisory": True,
                "sources_explored": len(chunks),
                "stop_reason": "corpus-first hit",
                "note": f"answered from existing corpus — {n} prior research chunk(s) covered this",
                "elapsed_s": round(time.monotonic() - t0, 2), "sovereign": True}

    # ── RESEARCH BUDGET + COOLDOWN gate (R-Stage 2; SWE-agent per-task budget) ─
    # The corpus didn't cover it — but research still can't consume the whole task
    # budget or re-fire reflexively. Block (not the skill — the SERVER) if a
    # deep_research fired < cooldown ago, or cumulative research time this session
    # would exceed the budget. Demand-driven with a cooldown: a genuinely novel
    # later need still fires after the cooldown; reflexive re-firing does not.
    gate = session_state.research_gate(est_s=WALL_BUDGET_S)
    otel_emit.record("research_budget_gate", {
        "tool": "deep_research", "allowed": gate["allowed"], "reason": gate["reason"],
        "cooldown_remaining_s": gate["cooldown_remaining_s"],
        "cumulative_s": gate["cumulative_s"], "budget_s": gate["budget_s"],
        "calls": gate["calls"]})
    if not gate["allowed"]:
        if gate["reason"] == "cooldown":
            msg = (f"deep_research is on cooldown — a call fired recently and "
                   f"{gate['cooldown_remaining_s']:.0f}s remain of the "
                   f"{int(session_state.RESEARCH_COOLDOWN_S)}s window. Do NOT re-run it.")
        else:
            msg = (f"deep_research budget exhausted this session "
                   f"({gate['cumulative_s']:.0f}s used of {int(gate['budget_s'])}s).")
        return {"ok": False, "gated": True, "gate_reason": gate["reason"],
                "error": msg,
                "use_instead": ("search_code against the corpus, or mcp-docs "
                                "research_topic / fetch_clean for the specific "
                                "sub-question — never another deep_research now"),
                **{k: gate[k] for k in ("cooldown_remaining_s", "cumulative_s",
                                        "budget_s", "calls")}}

    # ── EXHAUSTION-FIRST gate (R-Stage 3): prove cheaper tools were tried ──────
    # deep_research is Tier 4. The corpus check above is Tier 1; Tiers 2-3
    # (fetch_clean / research_topic) must be attempted on a RELATED query first, or
    # the agent must explicitly note it tried them (note_lighter_tools_attempted).
    if RESEARCH_EXHAUSTION_GATE:
        lt = session_state.lighter_tools_attempted(question)
        otel_emit.record("tool_ladder_gate", {
            "tool": "deep_research", "tier_attempted": 4,
            "lighter_tools_flag": lt["attempted"], "best_sim": lt["best_sim"],
            "best_tool": lt["best_tool"], "considered": lt["considered"],
            "escalation_allowed": lt["attempted"]})
        if not lt["attempted"]:
            return {"ok": False, "gated": True, "gate_reason": "lighter_tools_not_attempted",
                    "error": ("Lighter tools not yet attempted for this question. Run "
                              "search_code against the corpus, then mcp-docs fetch_clean "
                              "or research_topic on the specific sub-question. Call "
                              "deep_research only after those return insufficient "
                              "results. For textbook algorithms and standard patterns, "
                              "implement from parametric knowledge without any research tool."),
                    "use_instead": "search_code → fetch_clean / research_topic (Tiers 1-3)",
                    "ladder": ["Tier0 parametric (no tool)", "Tier1 search_code/corpus",
                               "Tier2 fetch_clean", "Tier3 research_topic", "Tier4 deep_research"],
                    "best_related_sim": lt["best_sim"]}

    plan = plan_research(question)
    subgoals = plan["subgoals"]
    all_sources: list[dict] = []
    seen_urls: list[str] = []
    echo_blocked_total = 0
    low_authority_total = 0
    stop_reason = "completed"
    max_waves = max(1, min(int(max_loops), 8))  # max_loops kept for compat; now WAVE-gated
    centroid = None  # running evidence centroid for drift saturation

    def _run_wave(qmap: list[tuple[str, str]]) -> list[dict]:
        """Explore the (query→subgoal) batch; tag each retained source with its
        sub-goal so coverage can be computed."""
        nonlocal seen_urls, echo_blocked_total, low_authority_total
        ex = explore([q for q, _ in qmap], seen_urls=seen_urls,
                     max_total=max(1, max_total_sources - len(all_sources)),
                     category=category)
        new = ex.get("sources", [])
        q2sg = {q: sg for q, sg in qmap}
        for s in new:
            s["_subgoal"] = q2sg.get(s.get("query"))
        seen_urls = ex.get("seen_urls", seen_urls)
        echo_blocked_total += ex.get("echo_chamber_blocked", 0)
        low_authority_total += ex.get("low_authority_filtered", 0)
        return new

    import novel as _novel  # Phase 5 helpers (local import: avoids import-time cycle)
    ensemble_strategies: list[str] | None = None

    # ── Wave 1: one batch of diverse (perspective × abstraction) queries per sub-goal ──
    waves = 1
    if RESEARCH_ENSEMBLE:
        # 5.4 ensemble-of-decompositions: explore several framings, RRF-fuse the
        # retained evidence so cross-framing corroboration wins. Replaces wave 1.
        ens = _novel.ensemble_wave1(question, subgoals, max_total_sources, category)
        new = ens["sources"]
        seen_urls = ens["seen_urls"]
        ensemble_strategies = ens["strategies"]
    else:
        wave1: list[tuple[str, str]] = []
        for sg in subgoals:
            for q in develop_queries(sg)["queries"]:
                wave1.append((q, sg))
        new = _run_wave(wave1)
    all_sources.extend(new)
    otel_emit.record("research_progress", {
        "tool": "deep_research", "done": len(all_sources), "total": max_total_sources,
        "item": f"wave 1/{max_waves}: {len(subgoals)} sub-goal(s)",
        "per_item": f"+{len(new)} sources", "elapsed_s": round(time.monotonic() - t0, 1)})

    # ── Waves 2..N: assess coverage/saturation, reflect on gaps, target the gaps ──
    while waves < max_waves:
        if time.monotonic() - t0 > WALL_BUDGET_S:
            stop_reason = "wall-clock budget"; break
        if len(all_sources) >= max_total_sources:
            stop_reason = "source cap"; break
        cov = _coverage_state(subgoals, all_sources)
        coverage = _coverage_fraction(cov)
        saturated, centroid, sat = _assess_saturation(new, len(all_sources) - len(new), centroid)
        otel_emit.record("research_coverage", {
            "tool": "deep_research", "wave": waves, "coverage": round(coverage, 3),
            "covered": sum(1 for d in cov.values() if len(d) >= MIN_INDEPENDENT_SOURCES),
            "subgoals": len(subgoals)})
        otel_emit.record("research_saturation", {"tool": "deep_research", "wave": waves, **sat})
        if coverage >= 1.0:
            stop_reason = "covered"; break
        if saturated:
            stop_reason = "saturated"; break
        gaps = reflect_gaps(subgoals, all_sources, cov)
        fq = gaps.get("followup_queries", [])
        if not fq:
            stop_reason = "no gaps"; break
        uncovered = gaps.get("uncovered_subgoals") or [
            sg for sg in subgoals if len(cov.get(sg, set())) < MIN_INDEPENDENT_SOURCES]
        sg_label = uncovered[0] if uncovered else subgoals[0]
        waves += 1
        new = _run_wave([(q, sg_label) for q in fq])  # targeted gap wave
        all_sources.extend(new)
        otel_emit.record("research_progress", {
            "tool": "deep_research", "done": len(all_sources), "total": max_total_sources,
            "item": f"wave {waves}/{max_waves}: gaps ({len(uncovered)} uncovered)",
            "per_item": f"+{len(new)} sources", "elapsed_s": round(time.monotonic() - t0, 1)})
        if not new:
            stop_reason = "no new sources"; break

    loops = waves  # result/otel field kept as `loops` for back-compat

    # extract -> verify (intermediate) -> synthesize
    claims = _extract_claims(question, all_sources)
    verified = verify_claims(claims)["verified"]

    # ── 5.1 ADVERSARIAL / disconfirming wave: actively try to FALSIFY the tentative
    # findings, fold the counter-evidence in, re-verify, and measure what got downgraded.
    adversarial: dict[str, Any] = {"ran": False}
    if RESEARCH_ADVERSARIAL and verified and (time.monotonic() - t0) < WALL_BUDGET_S \
            and len(all_sources) < max_total_sources:
        dq = _novel.disconfirm_queries(verified)
        if dq:
            adv_new = _run_wave([(q, "disconfirming") for q in dq])
            for s in adv_new:
                s["_adversarial"] = True
            if adv_new:
                all_sources.extend(adv_new)
                verified_post = verify_claims(_extract_claims(question, all_sources))["verified"]
                diff = _novel.verdict_downgrades(verified, verified_post)
                verified = verified_post
                adversarial = {"ran": True, "queries": dq, "new_sources": len(adv_new), **diff}
                otel_emit.record("adversarial_wave", {
                    "tool": "deep_research", "queries": len(dq), "new_sources": len(adv_new),
                    "downgraded": diff["count"]})

    # ── 5.2 CROSS-RUN contradiction detection: a new claim that conflicts with a prior
    # corpus claim becomes a KG `contradicts` edge (self-correcting across time).
    contradictions: dict[str, Any] = {"contradictions": [], "checked": 0, "backend": "off"}
    if RESEARCH_CROSS_RUN:
        contradictions = _novel.cross_run_contradictions(verified, question, write_kg=True)

    # ── 5.3 TEMPORAL provenance: stamp each finding "true as of <run date>" (+ flag a
    # newer source that may supersede it) — the report becomes a living artifact.
    if RESEARCH_TEMPORAL:
        verified = _novel.temporal_annotate(verified, all_sources)

    synth = synthesize(question, verified, plan)

    # ── CitationAgent pass (M-Stage 5): audit the report's claims vs the sources ─
    citation = {"supported_claims": 0, "unsupported_claims": [], "source_attribution": {},
                "sources_checked": 0, "backend": "off"}
    if CITATION_VERIFY:
        citation = _citation_verify(synth.get("report_md", ""), all_sources)
        unc = citation.get("unsupported_claims") or []
        otel_emit.record("citation_verified", {
            "tool": "deep_research", "supported_claims": citation.get("supported_claims"),
            "unsupported_count": len(unc), "sources_checked": citation.get("sources_checked"),
            "backend": citation.get("backend")})
        if unc:
            warn = (f"Warning: {len(unc)} claim(s) in this synthesis are not directly "
                    f"attributed to a source — treat with caution: "
                    + "; ".join(c[:80] for c in unc[:5]))
            synth["gap_note"] = ((synth.get("gap_note", "") + " ") if synth.get("gap_note") else "") + warn

    compounded = {"rag_stored": False, "kg_entities": 0}
    if compound and synth.get("report_md"):
        topic = f"research/{plan['slug']}"
        ing = _mcp_call(DOCS_MCP_URL, "ingest_doc",
                        {"url_or_markdown": synth["report_md"], "topic": topic})
        if ing.get("ok"):
            res = ing.get("result") or {}
            compounded = {"rag_stored": bool(res.get("rag_stored")),
                          "kg_entities": res.get("kg_entities_written", 0),
                          "namespace": res.get("namespace")}

    elapsed = round(time.monotonic() - t0, 2)
    # record against the per-session budget/cooldown (R-Stage 2)
    session_state.record_research(elapsed)
    otel_emit.record("deep_research_done", {
        "question": question, "loops": loops, "sources": len(all_sources),
        "claims": len(verified), "actionable": synth.get("actionable"),
        "claims_corroborated": synth.get("claims_corroborated"),
        "unsupported_rate": synth.get("unsupported_rate"),
        "unsupported_claims": len(citation.get("unsupported_claims") or []),
        "citation_backend": citation.get("backend"),
        "elapsed_s": elapsed, "stop_reason": stop_reason})
    return {
        "ok": True,
        "question": question,
        "plan": {"subgoals": subgoals, "roadmap": plan.get("roadmap"), "plan_path": plan.get("plan_path")},
        "loops": loops,
        "stop_reason": stop_reason,
        "sources_explored": len(all_sources),
        "sources": [{"url": s["url"], "domain": s["domain"], "authority": s["authority"],
                     "title": s["title"]} for s in all_sources],
        "verified_findings": verified,
        "report_md": synth["report_md"],
        "synthesized": synth["synthesized"],
        # GAP ANALYSIS, not confidence (R-Stage 4): this result is actionable unless
        # genuinely empty/broken; the agent proceeds and notes the gaps, and NEVER
        # re-runs deep_research on the quality score. Real quality gate = verify on code.
        "actionable": synth.get("actionable", True),
        "gap_note": synth.get("gap_note"),
        # CitationAgent pass (M-Stage 5): claims audited against the sources
        "unsupported_claims": citation.get("unsupported_claims", []),
        "source_attribution": citation.get("source_attribution", {}),
        "citation_backend": citation.get("backend"),
        "citation_count": synth.get("citation_count"),
        "claims_total": synth.get("claims_total"),
        "claims_corroborated": synth.get("claims_corroborated"),
        "claims_single_sourced": synth.get("claims_single_sourced"),
        "unsupported_rate": synth.get("unsupported_rate"),
        "gaps": synth["gaps"],
        "citations": synth["citations"],
        "echo_chamber_blocked": echo_blocked_total,
        "low_authority_filtered": low_authority_total,
        "compounded": compounded,
        # Phase 5 novel capabilities (present only when their gate ran)
        "adversarial": adversarial,
        "cross_run_contradictions": contradictions.get("contradictions", []),
        "ensemble_strategies": ensemble_strategies,
        "valid_as_of": (verified[0].get("valid_as_of") if (RESEARCH_TEMPORAL and verified) else None),
        "elapsed_s": elapsed,
        "sovereign": True,
    }


def stats() -> dict[str, Any]:
    def _up(url: str, path: str = "") -> bool:
        try:
            with httpx.Client(timeout=3) as c:
                return c.get(url + path).status_code < 500
        except Exception:  # noqa: BLE001
            return False
    docs_http = DOCS_MCP_URL.replace("/mcp", "/health")
    return {
        "chat_model": VLLM_BASE_URL or "(unset — deterministic plan/queries/synthesis)",
        "docs_mcp": DOCS_MCP_URL,
        "docs_up": _up(docs_http),
        "rag_mcp": RAG_MCP_URL,
        "kg_mcp": KG_MCP_URL,
        "rerank": RERANK_BASE_URL or "(unset — authority-heuristic ranking only)",
        "max_loops": MAX_RESEARCH_LOOPS,
        "max_total_sources": MAX_TOTAL_SOURCES,
        "max_sources_per_query": MAX_SOURCES_PER_QUERY,
        "min_independent_sources": MIN_INDEPENDENT_SOURCES,
        "wall_budget_s": WALL_BUDGET_S,
    }
