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
MAX_SUBGOALS = int(os.environ.get("RESEARCH_MAX_SUBGOALS", "5"))
QUERIES_PER_SUBGOAL = int(os.environ.get("RESEARCH_QUERIES_PER_SUBGOAL", "4"))
MAX_SOURCES_PER_QUERY = int(os.environ.get("RESEARCH_MAX_SOURCES_PER_QUERY", "3"))
MAX_TOTAL_SOURCES = int(os.environ.get("RESEARCH_MAX_TOTAL_SOURCES", "8"))
WALL_BUDGET_S = float(os.environ.get("RESEARCH_WALL_BUDGET_S", "600"))
MIN_INDEPENDENT_SOURCES = int(os.environ.get("RESEARCH_MIN_SOURCES", "2"))

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


def _fetch(url: str) -> dict[str, Any]:
    """Clean markdown via mcp-docs.fetch_clean (Crawl4AI -> trafilatura)."""
    r = _mcp_call(DOCS_MCP_URL, "fetch_clean", {"url": url})
    if not r.get("ok"):
        return {"ok": False, "url": url, "error": r.get("error", "fetch failed")}
    res = r.get("result") or {}
    return res if isinstance(res, dict) else {"ok": False, "url": url}


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
    "Generate diverse, COMPLEMENTARY web-search queries for the sub-goal — vary the "
    "abstraction level (broad vs specific), phrasing, and angle so they retrieve "
    "DIFFERENT sources, NOT near-duplicates. Prefer queries likely to surface "
    "primary/official sources. Return a STRICT JSON array of strings only."
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
    if not queries:  # graceful deterministic fallback: a few angle variants
        queries = [subgoal, f"{subgoal} documentation", f"{subgoal} example", f"{subgoal} best practices"]
    queries = _dedup_queries(queries)[:n]
    otel_emit.record("queries_developed", {"subgoal": subgoal, "n": len(queries)})
    return {"ok": True, "subgoal": subgoal, "queries": queries}


# ── STAGE 3: explore (dedup + authority rank + bounded breadth) ───────────────
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
    max_sources_per_query = max(1, min(int(max_sources_per_query), 10))
    max_total = max(1, min(int(max_total), 50))

    seen_norm: set[str] = {_normalize_url(u) for u in (seen_urls or [])}
    seen_shingles: list[set[str]] = []
    sources: list[dict[str, Any]] = []
    echo_blocked = 0
    low_authority_filtered = 0
    fetch_attempts = 0

    for q in queries:
        if len(sources) >= max_total:
            break
        candidates = _search(q, limit=max(max_sources_per_query * 3, 8), category=category)
        # authority-aware re-rank of candidates for THIS query (primary first).
        for c in candidates:
            c["_authority"] = authority_score(c.get("url", ""))
        order = sorted(range(len(candidates)), key=lambda i: candidates[i]["_authority"], reverse=True)
        rr = _rerank(q, [f"{candidates[i].get('title','')} {candidates[i].get('content','')}"
                         for i in order])
        if rr:  # blend: cross-encoder order, but keep authority as the primary key
            order = [order[j] for j in rr]
        taken_this_query = 0
        for i in order:
            if len(sources) >= max_total or taken_this_query >= max_sources_per_query:
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
            fetch_attempts += 1
            fetched = _fetch(url)
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
            taken_this_query += 1

    otel_emit.record("sources_explored", {
        "queries": len(queries), "sources": len(sources), "fetch_attempts": fetch_attempts,
        "echo_chamber_blocked": echo_blocked, "low_authority_filtered": low_authority_filtered,
    })
    if echo_blocked:
        otel_emit.record("echo_chamber_blocked", {"count": echo_blocked})
    if low_authority_filtered:
        otel_emit.record("low_authority_filtered", {"count": low_authority_filtered})
    return {"ok": True, "queries": queries, "count": len(sources), "sources": sources,
            "seen_urls": sorted(seen_norm), "echo_chamber_blocked": echo_blocked,
            "low_authority_filtered": low_authority_filtered}


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
    out: list[dict[str, Any]] = []
    for c in claims:
        claim = str(c.get("claim", "")).strip()
        srcs = c.get("sources", []) or []
        if not claim:
            continue
        # normalize to {url, snippet}
        norm = []
        for s in srcs:
            if isinstance(s, str):
                norm.append({"url": s, "snippet": ""})
            elif isinstance(s, dict):
                norm.append({"url": s.get("url", ""), "snippet": s.get("snippet", s.get("markdown", ""))})
        by_domain: dict[str, dict] = {}
        contradicts = 0
        supporters: list[str] = []
        for s in norm:
            dom = _domain(s["url"])
            if not dom or dom in by_domain:
                continue  # one vote per domain -> independence
            label = _label_support(claim, s["snippet"]) if (VLLM_BASE_URL and s["snippet"]) else "unchecked"
            by_domain[dom] = {"url": s["url"], "label": label}
            if label == "contradicts":
                contradicts += 1
            elif label in ("supports", "unchecked"):
                # 'unchecked' counts as candidate support (deterministic fallback),
                # but the status wording stays honest about the lack of entailment.
                supporters.append(s["url"])
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

    findings_blob = json.dumps(verified_findings, indent=2)[:16000]
    report = _llm(
        [{"role": "system", "content": _SYNTH_SYS},
         {"role": "user", "content":
            f"Question: {question}\n\nVerified findings (JSON):\n{findings_blob}\n\n"
            f"Sources (numbered):\n" + "\n".join(f"[{i + 1}] {u}" for i, u in enumerate(citations))}],
        temperature=0.2)
    if not report:  # deterministic, still-cited fallback
        lines = [f"# Research brief: {question}", ""]
        idx = {u: i + 1 for i, u in enumerate(citations)}
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
    # Confidence is an INTERNAL quality metric, never a gate or a signal to the
    # caller to retry. By design most syntheses score "low": claims are effectively
    # single-sourced after the echo-chamber guard dedups overlapping sources (the
    # same fact corroborated once, not N times). The agent was reading caller-facing
    # confidence="low" as "research insufficient → call deep_research again", which
    # is always wrong. So the OBSERVABILITY span keeps the honest internal level,
    # but the CALLER-FACING field is relabelled away from the alarming "low":
    #   low -> "adequate"  (medium/high pass through unchanged)
    # The real quality gate is the verify gate on the IMPLEMENTATION, not this score.
    caller_confidence = "adequate" if confidence == "low" else confidence
    if confidence == "low" and report and "single-sourced by design" not in report:
        report += ("\n\n> _Research sufficiency: **adequate**. A low internal "
                   "confidence is NORMAL and expected — claims are single-sourced by "
                   "design once the echo-chamber guard dedups overlapping sources. "
                   "This synthesis is complete and sufficient to act on; validate the "
                   "work it informs via the implementation's verify gate. Do NOT "
                   "re-run deep_research._")
    otel_emit.record("report_synthesized", {"question": question, "citations": len(citations),
                                            "confidence": confidence, "llm": synthesized})
    return {"ok": True, "question": question, "report_md": report, "synthesized": synthesized,
            "citations": citations,
            # caller-facing confidence is relabelled (low->adequate); the honest
            # internal level stays in confidence_internal + the otel span.
            "confidence": caller_confidence, "confidence_internal": confidence,
            "gaps": gaps,
            # always usable regardless of confidence; confidence is metadata only.
            "actionable": True, "confidence_is_advisory": True}


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
_PARAMETRIC_FRAMES = (
    "how does", "how do i implement", "how to implement", "implement a", "implement the",
    "explain the", "explain how", "what is a", "what is the", "write a function",
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
    loops = 0
    echo_blocked_total = 0
    low_authority_total = 0
    stop_reason = "completed"

    for loop in range(max_loops):
        loops = loop + 1
        if time.monotonic() - t0 > WALL_BUDGET_S:
            stop_reason = "wall-clock budget"
            break
        if len(all_sources) >= max_total_sources:
            stop_reason = "source cap"
            break
        subgoal = subgoals[loop % len(subgoals)]
        dq = develop_queries(subgoal)
        ex = explore(dq["queries"], seen_urls=seen_urls,
                     max_total=max(1, max_total_sources - len(all_sources)),
                     category=category)
        new = ex.get("sources", [])
        all_sources.extend(new)
        seen_urls = ex.get("seen_urls", seen_urls)
        echo_blocked_total += ex.get("echo_chamber_blocked", 0)
        low_authority_total += ex.get("low_authority_filtered", 0)
        # tqdm-style empirical progress (Stage 7a): item N/total, per-loop yield,
        # running elapsed — the live log derives the ETA. Real movement, not "running…".
        otel_emit.record("research_progress", {
            "tool": "deep_research", "done": len(all_sources), "total": max_total_sources,
            "item": f"loop {loops}/{max_loops}: {subgoal[:48]}",
            "per_item": f"+{len(new)} sources", "elapsed_s": round(time.monotonic() - t0, 1)})
        if not new and loop > 0:
            stop_reason = "no new sources"
            break

    # extract -> verify (intermediate) -> synthesize
    claims = _extract_claims(question, all_sources)
    verified = verify_claims(claims)["verified"]
    synth = synthesize(question, verified, plan)

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
        "claims": len(verified),
        # observability keeps the honest internal level; the caller sees the
        # relabelled, non-alarming value in the returned dict.
        "confidence": synth.get("confidence_internal", synth.get("confidence")),
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
        "confidence": synth["confidence"],
        # confidence is advisory metadata — this result is always usable; a low
        # score is not a reason to re-run. The verify gate on the implementation is
        # the real quality check. (See synthesize() for the full rationale.)
        "actionable": synth.get("actionable", True),
        "confidence_is_advisory": True,
        "gaps": synth["gaps"],
        "citations": synth["citations"],
        "echo_chamber_blocked": echo_blocked_total,
        "low_authority_filtered": low_authority_total,
        "compounded": compounded,
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
