"""Stage 1 — structured source fan-out for mcp-research.

Native MCP source adapters that sit ALONGSIDE the existing sovereign SearXNG ->
Crawl4AI/trafilatura web layer (mcp-docs). Each adapter is a thin, bounded reader
over a free/public API. The design rules (identical to research_core's discipline):

  * NEVER raise — every adapter returns {"ok": bool, "results": [...], "error": str}
    so a dead/blocked source degrades to an empty list, never a crash.
  * Presence-gated — an adapter that needs a token (github_search -> GITHUB_TOKEN)
    no-ops with {"skipped": true} when the token is absent. Keyless sources
    (arXiv, Semantic Scholar unauth pool, HN Algolia, Stack Exchange) always run.
  * Degrade to web — no structured source is load-bearing. The classifier-router
    ALWAYS includes "searxng" so the existing web layer answers even if every
    structured adapter is down.

Network lives in two monkeypatchable helpers (_get_json / _get_text) so the smoke
tests can assert parsing, gating, RRF, and routing with NO live services.

Normalized result item (every adapter emits this shape):
  {"title","url","content","source_type","authors":[...],"date":str|"",
   "citation_count":int|None,"extra":{...}}

NOTE (honest): the upstream spec assumed arxiv_search / hn_search already existed
to be "extended" — they did not; mcp-research was SearXNG-only. These are built
fresh here as native adapters.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

try:
    import otel_emit  # best-effort spans; no-op if unavailable
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

# ── env (all optional — keyless sources work without any of these) ────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
STACKEXCHANGE_KEY = os.environ.get("STACKEXCHANGE_KEY", "").strip()

HTTP_TIMEOUT = float(os.environ.get("RESEARCH_SOURCE_TIMEOUT", "12"))
RRF_K = int(os.environ.get("RESEARCH_RRF_K", "60"))

ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1"
GITHUB_API = "https://api.github.com"
HN_API = "https://hn.algolia.com/api/v1/search"
SE_API = "https://api.stackexchange.com/2.3"

# Attribution required by Semantic Scholar's API terms when results are displayed.
S2_ATTRIBUTION = "Data from Semantic Scholar (https://www.semanticscholar.org/)"


# ── monkeypatchable network primitives (smoke tests stub these) ───────────────
def _get_json(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    """GET -> parsed JSON. Returns {"ok":False,"error":...} on ANY failure."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, params=params, headers=headers)
            r.raise_for_status()
            return {"ok": True, "json": r.json()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _post_json(url: str, json_body: Any, params: dict | None = None,
               headers: dict | None = None, timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.post(url, params=params, headers=headers, json=json_body)
            r.raise_for_status()
            return {"ok": True, "json": r.json()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _get_text(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    """GET -> raw text (for arXiv Atom XML, Discourse, etc.)."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, params=params, headers=headers)
            r.raise_for_status()
            return {"ok": True, "text": r.text}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _item(title: str, url: str, content: str = "", source_type: str = "web",
          authors: list[str] | None = None, date: str = "",
          citation_count: int | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "content": (content or "").strip(),
        "source_type": source_type,
        "authors": authors or [],
        "date": date or "",
        "citation_count": citation_count,
        "extra": extra,
    }


# ── arXiv (keyless Atom API; optional days_back + category targeting) ─────────
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_search(query: str, days_back: int | None = None,
                 categories: list[str] | None = None, limit: int = 8) -> dict[str, Any]:
    """arXiv Atom API. days_back OPTIONAL (None => no recency filter, so seminal
    work is reachable). categories targets cs.CR / cs.LG / cs.DC / cs.AI etc.
    Keyless, ~1 req / 3s upstream. Errors returned as strings, never raised."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "arxiv", "results": []}
    limit = max(1, min(int(limit), 50))
    terms = [f"all:{query}"]
    if categories:
        cat_clause = " OR ".join(f"cat:{c.strip()}" for c in categories if c.strip())
        if cat_clause:
            terms = [f"({cat_clause})", f"all:{query}"]
    search_query = " AND ".join(terms)
    params = {"search_query": search_query, "start": 0, "max_results": limit,
              "sortBy": "relevance", "sortOrder": "descending"}
    if days_back is not None:
        # arXiv has no date query param; sort by recency and filter client-side.
        params["sortBy"] = "submittedDate"
    r = _get_text(ARXIV_API, params=params)
    if not r.get("ok"):
        return {"ok": False, "source": "arxiv", "error": r["error"], "results": []}
    try:
        root = ET.fromstring(r["text"])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "source": "arxiv", "error": f"parse: {e}", "results": []}
    results: list[dict[str, Any]] = []
    cutoff = None
    if days_back is not None:
        import datetime
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days_back))
    for entry in root.findall("a:entry", _ATOM_NS):
        title = (entry.findtext("a:title", default="", namespaces=_ATOM_NS) or "").strip()
        summary = (entry.findtext("a:summary", default="", namespaces=_ATOM_NS) or "").strip()
        url = (entry.findtext("a:id", default="", namespaces=_ATOM_NS) or "").strip()
        published = (entry.findtext("a:published", default="", namespaces=_ATOM_NS) or "").strip()
        if cutoff is not None and published:
            try:
                import datetime
                pub = datetime.datetime.strptime(published[:10], "%Y-%m-%d")
                if pub < cutoff:
                    continue
            except Exception:  # noqa: BLE001
                pass
        authors = [a.findtext("a:name", default="", namespaces=_ATOM_NS).strip()
                   for a in entry.findall("a:author", _ATOM_NS)]
        prim = entry.find("arxiv:primary_category", _ATOM_NS)
        cat = prim.get("term") if prim is not None else ""
        results.append(_item(title, url, summary, "arxiv", [a for a in authors if a],
                             published[:10], None, category=cat))
    otel_emit.record("source_arxiv", {"query": query, "results": len(results),
                                       "categories": categories or [], "days_back": days_back})
    return {"ok": True, "source": "arxiv", "results": results}


# ── Semantic Scholar (keyless shared pool; citation-graph traversal) ──────────
_S2_FIELDS = "title,abstract,year,authors,citationCount,url,externalIds"


def _s2_headers() -> dict[str, str]:
    return {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}


def _s2_paper_to_item(p: dict[str, Any]) -> dict[str, Any]:
    ext = p.get("externalIds") or {}
    url = p.get("url") or (f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv")
                           else (f"https://doi.org/{ext['DOI']}" if ext.get("DOI") else ""))
    authors = [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")]
    return _item(p.get("title", ""), url, p.get("abstract") or "", "semantic_scholar",
                 authors, str(p.get("year") or ""), p.get("citationCount"),
                 paper_id=p.get("paperId"), external_ids=ext,
                 attribution=S2_ATTRIBUTION)


def semantic_scholar_search(query: str, limit: int = 10) -> dict[str, Any]:
    """Relevance paper search. Keyless (5,000 req/5min shared pool); optional
    SEMANTIC_SCHOLAR_API_KEY -> 1 RPS dedicated. The killer feature is the citation
    graph (see semantic_scholar_citations)."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "semantic_scholar", "results": []}
    limit = max(1, min(int(limit), 100))
    r = _get_json(f"{S2_API}/paper/search",
                  params={"query": query, "limit": limit, "fields": _S2_FIELDS},
                  headers=_s2_headers())
    if not r.get("ok"):
        return {"ok": False, "source": "semantic_scholar", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = [_s2_paper_to_item(p) for p in (data.get("data") or [])]
    otel_emit.record("source_semantic_scholar", {"query": query, "results": len(results),
                                                  "keyed": bool(SEMANTIC_SCHOLAR_API_KEY)})
    return {"ok": True, "source": "semantic_scholar", "results": results,
            "attribution": S2_ATTRIBUTION}


def semantic_scholar_citations(paper_id: str, direction: str = "references",
                               limit: int = 25) -> dict[str, Any]:
    """Citation-graph traversal — the feature that turns search into 'find the
    canonical + frontier of a topic'. direction='references' -> backward (papers
    THIS one cites -> seminal); 'citations' -> forward (papers citing THIS ->
    latest). paper_id may be an S2 id, 'arXiv:2106.01345', 'DOI:...', etc."""
    paper_id = (paper_id or "").strip()
    if not paper_id:
        return {"ok": False, "source": "semantic_scholar", "error": "empty paper_id", "results": []}
    direction = direction if direction in ("references", "citations") else "references"
    limit = max(1, min(int(limit), 100))
    nested = "citedPaper" if direction == "references" else "citingPaper"
    r = _get_json(f"{S2_API}/paper/{quote(paper_id, safe=':')}/{direction}",
                  params={"limit": limit, "fields": _S2_FIELDS}, headers=_s2_headers())
    if not r.get("ok"):
        return {"ok": False, "source": "semantic_scholar", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = []
    for edge in (data.get("data") or []):
        paper = edge.get(nested) or {}
        if paper:
            it = _s2_paper_to_item(paper)
            it["extra"]["edge"] = "cites" if direction == "references" else "cited_by"
            results.append(it)
    otel_emit.record("source_s2_citation_graph", {"paper_id": paper_id, "direction": direction,
                                                  "results": len(results)})
    return {"ok": True, "source": "semantic_scholar", "direction": direction,
            "results": results, "attribution": S2_ATTRIBUTION}


# ── GitHub search (PAT-gated; repos / code / issues) ──────────────────────────
def _github_headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def github_search(query: str, search_type: str = "repositories",
                  limit: int = 10) -> dict[str, Any]:
    """GitHub REST search over repositories / code / issues. Presence-gated on
    GITHUB_TOKEN — absent => no-op {"skipped": true} (the web layer still answers).
    With a free PAT: 30 req/min search, ~9 req/min code-search."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "github", "results": []}
    if not GITHUB_TOKEN:
        return {"ok": True, "source": "github", "skipped": True, "results": [],
                "error": "GITHUB_TOKEN absent — github_search skipped (web layer covers it)"}
    search_type = search_type if search_type in ("repositories", "code", "issues") else "repositories"
    limit = max(1, min(int(limit), 50))
    r = _get_json(f"{GITHUB_API}/search/{search_type}",
                  params={"q": query, "per_page": limit}, headers=_github_headers())
    if not r.get("ok"):
        return {"ok": False, "source": "github", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results: list[dict[str, Any]] = []
    for it in (data.get("items") or [])[:limit]:
        if search_type == "repositories":
            results.append(_item(it.get("full_name", ""), it.get("html_url", ""),
                                 it.get("description") or "", "github_repo",
                                 [(it.get("owner") or {}).get("login", "")],
                                 (it.get("pushed_at") or "")[:10],
                                 None, stars=it.get("stargazers_count")))
        elif search_type == "code":
            repo = (it.get("repository") or {}).get("full_name", "")
            results.append(_item(f"{repo}:{it.get('path','')}", it.get("html_url", ""),
                                 it.get("path") or "", "github_code", [], "", None, repo=repo))
        else:  # issues (and PRs)
            results.append(_item(it.get("title", ""), it.get("html_url", ""),
                                 (it.get("body") or "")[:2000], "github_issue",
                                 [(it.get("user") or {}).get("login", "")],
                                 (it.get("created_at") or "")[:10], None,
                                 comments=it.get("comments"), state=it.get("state")))
    otel_emit.record("source_github", {"query": query, "type": search_type, "results": len(results)})
    return {"ok": True, "source": "github", "results": results}


# ── Hacker News (Algolia; keyless) ────────────────────────────────────────────
def hn_search(query: str, limit: int = 10, tags: str = "story") -> dict[str, Any]:
    """HN search via Algolia (keyless). tags='story' for submissions; comments and
    discussion via the item URL. Good signal for what practitioners actually use."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "hn", "results": []}
    limit = max(1, min(int(limit), 50))
    r = _get_json(HN_API, params={"query": query, "tags": tags, "hitsPerPage": limit})
    if not r.get("ok"):
        return {"ok": False, "source": "hn", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = []
    for h in (data.get("hits") or [])[:limit]:
        oid = h.get("objectID", "")
        url = h.get("url") or (f"https://news.ycombinator.com/item?id={oid}" if oid else "")
        results.append(_item(h.get("title") or h.get("story_title") or "", url,
                             (h.get("story_text") or h.get("comment_text") or "")[:1000],
                             "hn", [h.get("author", "")], (h.get("created_at") or "")[:10],
                             None, points=h.get("points"), num_comments=h.get("num_comments"),
                             hn_url=f"https://news.ycombinator.com/item?id={oid}"))
    otel_emit.record("source_hn", {"query": query, "results": len(results)})
    return {"ok": True, "source": "hn", "results": results}


# ── Stack Exchange (keyless 300/day; optional key) ────────────────────────────
def stackexchange_search(query: str, site: str = "stackoverflow",
                         limit: int = 10) -> dict[str, Any]:
    """Q&A with votes/tags. Keyless = 300 req/day; STACKEXCHANGE_KEY -> 10k/day.
    Medium value — the classifier routes it for library/how-to queries."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "source": "stackexchange", "results": []}
    limit = max(1, min(int(limit), 50))
    params = {"order": "desc", "sort": "relevance", "q": query, "site": site,
              "pagesize": limit, "filter": "withbody"}
    if STACKEXCHANGE_KEY:
        params["key"] = STACKEXCHANGE_KEY
    r = _get_json(f"{SE_API}/search/advanced", params=params)
    if not r.get("ok"):
        return {"ok": False, "source": "stackexchange", "error": r["error"], "results": []}
    data = r["json"] if isinstance(r["json"], dict) else {}
    results = []
    for q in (data.get("items") or [])[:limit]:
        body = re.sub(r"<[^>]+>", " ", q.get("body") or "")[:1500]
        results.append(_item(q.get("title", ""), q.get("link", ""), body, "stackexchange",
                             [(q.get("owner") or {}).get("display_name", "")],
                             "", None, score=q.get("score"), tags=q.get("tags"),
                             is_answered=q.get("is_answered")))
    otel_emit.record("source_stackexchange", {"query": query, "results": len(results)})
    return {"ok": True, "source": "stackexchange", "results": results}


# ── RRF fusion (pure arithmetic, no model) ────────────────────────────────────
def rrf_fuse(ranked_lists: list[list[dict[str, Any]]], k: int = RRF_K,
             key: str = "url") -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank_in_list). Rewards items
    that rank consistently across multiple sources. Pure arithmetic — the biggest
    robustness-per-effort win when several adapters return ranked lists."""
    scores: dict[str, float] = {}
    merged: dict[str, dict[str, Any]] = {}
    contributing: dict[str, set[str]] = {}
    for lst in ranked_lists or []:
        for rank, item in enumerate(lst or []):
            kv = (item.get(key) or "").strip().lower()
            if not kv:
                continue
            scores[kv] = scores.get(kv, 0.0) + 1.0 / (k + rank + 1)
            contributing.setdefault(kv, set()).add(item.get("source_type", "web"))
            if kv not in merged:
                merged[kv] = dict(item)
            elif not merged[kv].get("content") and item.get("content"):
                merged[kv] = dict(item)  # prefer the richer copy
    out = []
    for kv, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        it = dict(merged[kv])
        it["_rrf_score"] = round(sc, 6)
        it["_rrf_sources"] = sorted(contributing[kv])
        out.append(it)
    return out


# ── classifier-router (keyword heuristic; always includes searxng) ────────────
_CRYPTO_KW = ("zero-knowledge", "zk-", "zksnark", "zk-snark", "snark", "stark", "ethereum",
              "eip-", "erc-", "evm", "solidity", "rollup", "consensus", "validator", "mev",
              "blockchain", "cryptograph", "elliptic curve", "merkle", "proof of stake",
              "smart contract", "danksharding", "blob", "calldata", "plonk", "kzg",
              "protocol", "p2p", "byzantine", "bls signature", "vrf")
_ML_KW = ("neural", "transformer", "llm", "language model", "deep learning", "diffusion",
          "embedding", "fine-tun", "reinforcement learning", "rlhf", "gradient", "dataset",
          "benchmark", "attention", "quantization", "inference", "pretrain", "model weights",
          "convolution", "gan", "vae", "tokeniz")
_LIB_KW = ("how to", "install", "error", "exception", "traceback", "library", "package",
           "api usage", "tutorial", "example", "import", "deprecated", "version", "config",
           "command", "cli", "function", "method", "syntax", "stack trace")


def classify_query(query: str) -> dict[str, Any]:
    """Map a query to a source set + per-source budget. crypto/protocol ->
    arXiv(cs.CR) + github + ethresearch + eip + searxng; applied-ML -> arXiv(cs.LG)
    + semantic_scholar + github + hn; library-how-to -> github + searxng + hn +
    stackexchange. Always includes searxng as catch-all. Bounded budgets prevent
    overspawning. (ethresearch/eip adapters arrive in Stage 2; the router names
    them now and source_fanout simply skips unregistered sources.)"""
    q = (query or "").lower()
    crypto = sum(1 for kw in _CRYPTO_KW if kw in q)
    ml = sum(1 for kw in _ML_KW if kw in q)
    lib = sum(1 for kw in _LIB_KW if kw in q)

    if crypto and crypto >= ml:
        category = "crypto"
        budgets = {"arxiv": 6, "github": 5, "ethresearch": 5, "eip_erc": 3,
                   "semantic_scholar": 4, "searxng": 6}
        arxiv_cats = ["cs.CR", "cs.DC"]
    elif ml and ml >= lib:
        category = "applied_ml"
        budgets = {"arxiv": 6, "semantic_scholar": 8, "github": 5, "hn": 4, "searxng": 6}
        arxiv_cats = ["cs.LG", "cs.AI"]
    elif lib:
        category = "library"
        budgets = {"github": 6, "searxng": 8, "hn": 4, "stackexchange": 6}
        arxiv_cats = []
    else:
        category = "general"
        budgets = {"searxng": 8, "semantic_scholar": 4, "github": 4, "hn": 4}
        arxiv_cats = []

    sources = list(budgets.keys())
    if "searxng" not in sources:  # invariant: web is always the catch-all
        sources.append("searxng")
        budgets["searxng"] = 6
    return {"ok": True, "query": query, "category": category, "sources": sources,
            "budgets": budgets, "arxiv_categories": arxiv_cats}


# ── source registry + fan-out orchestrator ────────────────────────────────────
def _registry() -> dict[str, Any]:
    """name -> adapter callable. Stage 2 extends this (ethresearch, eip_erc, ...).
    Resolved at call time so monkeypatched adapters in tests are honored."""
    import sys
    mod = sys.modules[__name__]
    return {
        "arxiv": mod.arxiv_search,
        "semantic_scholar": mod.semantic_scholar_search,
        "github": mod.github_search,
        "hn": mod.hn_search,
        "stackexchange": mod.stackexchange_search,
    }


def source_fanout(query: str, sources: list[str] | None = None,
                  fuse: bool = True) -> dict[str, Any]:
    """Classify -> call each routed STRUCTURED adapter with its budget -> RRF-fuse.
    'searxng' / unregistered names (e.g. ethresearch before Stage 2) are skipped
    here — the existing web layer (research_core.explore) covers searxng. No source
    is load-bearing: a fully-down structured layer returns an empty fused list and
    the caller falls back to web. Errors are collected per-source, never raised."""
    routing = classify_query(query)
    sources = sources or routing["sources"]
    budgets = routing["budgets"]
    reg = _registry()
    per_source: dict[str, dict[str, Any]] = {}
    ranked_lists: list[list[dict[str, Any]]] = []
    errors: dict[str, str] = {}
    skipped: list[str] = []
    for name in sources:
        if name == "searxng" or name not in reg:
            if name != "searxng":
                skipped.append(name)  # not yet registered (Stage 2+)
            continue
        budget = budgets.get(name, 5)
        try:
            if name == "arxiv":
                res = reg[name](query, days_back=None, categories=routing["arxiv_categories"], limit=budget)
            else:
                res = reg[name](query, limit=budget)
        except Exception as e:  # noqa: BLE001 — belt-and-suspenders; adapters shouldn't raise
            res = {"ok": False, "source": name, "error": f"{type(e).__name__}: {e}", "results": []}
        per_source[name] = {"ok": res.get("ok"), "count": len(res.get("results", [])),
                            "skipped": res.get("skipped", False), "error": res.get("error")}
        if res.get("error") and not res.get("skipped"):
            errors[name] = res["error"]
        if res.get("results"):
            ranked_lists.append(res["results"])
    fused = rrf_fuse(ranked_lists) if fuse else [it for lst in ranked_lists for it in lst]
    otel_emit.record("source_fanout", {"query": query, "category": routing["category"],
                                       "sources": len([s for s in sources if s in reg]),
                                       "results": len(fused), "errors": len(errors)})
    if fuse:
        otel_emit.record("rrf_fused", {"lists": len(ranked_lists), "results": len(fused)})
    return {"ok": True, "query": query, "category": routing["category"],
            "routed_sources": sources, "per_source": per_source, "skipped": skipped,
            "errors": errors, "count": len(fused), "results": fused,
            "attribution": S2_ATTRIBUTION if any(s == "semantic_scholar" for s in sources) else None}


def source_stats() -> dict[str, Any]:
    return {
        "arxiv": "keyless (Atom API)",
        "semantic_scholar": "keyed (1 RPS)" if SEMANTIC_SCHOLAR_API_KEY else "keyless (5k/5min pool)",
        "github": "PAT (30 req/min)" if GITHUB_TOKEN else "SKIPPED (no GITHUB_TOKEN)",
        "hn": "keyless (Algolia)",
        "stackexchange": "keyed (10k/day)" if STACKEXCHANGE_KEY else "keyless (300/day)",
        "rrf_k": RRF_K,
        "registered": sorted(_registry().keys()),
    }
