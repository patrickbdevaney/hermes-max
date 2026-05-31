"""Stage 3 — on-disk human-readable corpus + provenance + lazy distillation.

Two problems this fixes, per the research-engine spec:

  1. The corpus was distilled-on-ingest (compressing away the technical nuance that
     frontier work needs) and only lived inside the vector store. Now: the FULL,
     untruncated extracted content is written to a greppable, git-versionable,
     human-readable markdown tree on disk — `corpus/{namespace}/{source_type}/
     {slug}.md` with YAML front-matter provenance — INDEPENDENT of the vector DB
     and fully sovereign. The full text is ALSO indexed into the existing hybrid
     RAG store (mcp-codebase-rag.index_document already keeps full chunks; the 10K
     truncation lived in the old distill step, which we stop calling on ingest).

  2. Distillation moves from ingest-time to QUERY-time, run only over the chunks
     a query actually retrieves — local Qwen by default; optional cheap-cloud
     (DeepSeek via the conductor's steer role) for DENSE technical sources, behind
     the RESEARCH_CLOUD_DISTILL flag. Off => fully local / offline.

Resolvability (the verify gate, Stage 5, depends on this): every RAG chunk is
indexed with `source` = the corpus file's relative path, so a retrieved chunk
resolves straight back to its backing on-disk document + front-matter provenance.

Discipline (unchanged): never raises (string errors), every backend degrades, no
new required keys. The vector store (machinery) is USED, not modified.

NOTE (honest): the spec says "Qdrant"; the actual RAG is mcp-codebase-rag's
SQLite + FTS5 + sqlite-vec hybrid store. Same contract (full chunks + embeddings),
different engine — documented rather than papered over.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc  # reuse _mcp_call / _llm / _slug — no duplication

# ── config ────────────────────────────────────────────────────────────────────
CORPUS_DIR = os.path.expanduser(
    os.environ.get("RESEARCH_CORPUS_DIR", "~/.hermes-max/corpus"))
ESCALATION_MCP_URL = os.environ.get("ESCALATION_MCP_URL", "http://127.0.0.1:9107/mcp")
RAG_MCP_URL = rc.RAG_MCP_URL
# Flag: off (default) => distillation is fully local/sovereign. On => DENSE
# technical sources may be distilled by the conductor's cheap-cloud steer role.
#
# WHY LOCAL IS THE DEFAULT (Stage 7b): per-source distillation is the highest-VOLUME
# step in the research cascade. Gating it on a rate-limited cloud tier (e.g. Groq's
# 6-8K TPM) would force serialization + 429 backoffs — a real ARTIFICIAL bottleneck
# on exactly the bulkiest step. The local model is already running, has no rate
# limit, and handles bulk summarization fine. Cloud distillation is therefore an
# explicit opt-in ONLY, and it is rate-limit-bound (warned below). Keep the fast
# cloud tiers for slop-drafting small verifiable tasks, not the bulk cascade.
CLOUD_DISTILL = os.environ.get("RESEARCH_CLOUD_DISTILL", "false").strip().lower() in ("1", "true", "yes")
# Source types whose content is dense enough to warrant cloud distillation.
DENSE_SOURCE_TYPES = {"arxiv", "semantic_scholar", "eip_erc", "ietf_rfc", "audit"}

if CLOUD_DISTILL:
    # One-time warning at import: the operator opted into the rate-limit-bound path.
    otel_emit.record("research_cloud_distill_enabled", {
        "warning": "RESEARCH_CLOUD_DISTILL=on — dense-source distillation routes to a "
                   "RATE-LIMITED cloud tier; high volume may serialize on 429 backoffs "
                   "(an artificial bottleneck). Local distillation is the default for a "
                   "reason.", "dense_source_types": sorted(DENSE_SOURCE_TYPES)},
        status="error")


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ── front-matter + path ───────────────────────────────────────────────────────
def _yaml_value(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(str(x)) for x in v) + "]"
    if v is None:
        return '""'
    if isinstance(v, (int, float, bool)):
        return str(v).lower() if isinstance(v, bool) else str(v)
    s = str(v)
    # quote anything with YAML-significant chars or leading/trailing space
    if s == "" or re.search(r"[:#\[\]{}\n\"']", s) or s != s.strip():
        return json.dumps(s)
    return s


def _front_matter(meta: dict[str, Any]) -> str:
    order = ["source_url", "title", "authors", "date", "retrieval_query",
             "source_type", "citation_count", "authority_score", "ingested_at",
             "session_id"]
    keys = order + [k for k in meta if k not in order]
    lines = ["---"]
    for k in keys:
        if k in meta:
            lines.append(f"{k}: {_yaml_value(meta[k])}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def corpus_path(namespace: str, source_type: str, slug: str) -> str:
    ns = re.sub(r"[^a-z0-9._/-]+", "-", (namespace or "research").lower()).strip("-/") or "research"
    st = re.sub(r"[^a-z0-9._-]+", "-", (source_type or "web").lower()).strip("-") or "web"
    sl = rc._slug(slug or "doc")
    return os.path.join(CORPUS_DIR, ns, st, f"{sl}.md")


def corpus_relpath(path: str) -> str:
    try:
        return os.path.relpath(path, CORPUS_DIR)
    except Exception:  # noqa: BLE001
        return path


# ── injectable backends (smoke tests stub these) ──────────────────────────────
def _rag_index(text: str, namespace: str, source: str, title: str) -> dict[str, Any]:
    return rc._mcp_call(RAG_MCP_URL, "index_document",
                        {"text": text, "namespace": namespace, "source": source, "title": title})


def _conductor_distill(prompt: str, max_tokens: int = 1500) -> str | None:
    """Cheap-cloud distill via the conductor's steer role (DeepSeek-first). Returns
    None if steer is OFF/capped/unreachable (proceed_local) -> caller falls to local."""
    r = rc._mcp_call(ESCALATION_MCP_URL, "conductor_steer",
                     {"prompt": prompt, "max_tokens": max_tokens})
    if not r.get("ok"):
        return None
    res = r.get("result") or {}
    if isinstance(res, dict) and res.get("proceed_local"):
        return None
    content = res.get("content") if isinstance(res, dict) else None
    return content.strip() if content else None


# ── write the on-disk corpus document (full, untruncated, with provenance) ─────
def write_corpus_doc(namespace: str, source_type: str, content: str,
                     meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write FULL untruncated content + YAML front-matter to
    corpus/{namespace}/{source_type}/{slug}.md. Idempotent (re-ingest overwrites
    the same slug). The sovereign, greppable, git-versionable record — independent
    of the vector store. Best-effort: a write failure returns a string error."""
    content = content or ""
    meta = dict(meta or {})
    meta.setdefault("source_type", source_type)
    meta.setdefault("ingested_at", _now_iso())
    slug_src = meta.get("title") or meta.get("source_url") or "doc"
    path = corpus_path(namespace, source_type, slug_src)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(_front_matter(meta))
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "path": path}
    otel_emit.record("corpus_written", {"namespace": namespace, "source_type": source_type,
                                        "path": corpus_relpath(path), "chars": len(content)})
    return {"ok": True, "path": path, "relpath": corpus_relpath(path),
            "chars": len(content), "front_matter": meta}


# ── ingest: write disk (full) + index full chunks to RAG (resolvable) ──────────
def ingest_research(namespace: str, source_type: str, content: str,
                    meta: dict[str, Any] | None = None, index: bool = True) -> dict[str, Any]:
    """Write the full document to the on-disk corpus AND (best-effort) index the
    FULL content into the hybrid RAG store. Each RAG chunk's `source` is set to the
    corpus relative path so a retrieved chunk resolves back to its backing document
    + provenance (used by the Stage-5 verify gate). NO distill-on-ingest — the full
    technical text is preserved; distillation happens lazily at query time."""
    meta = dict(meta or {})
    written = write_corpus_doc(namespace, source_type, content, meta)
    indexed = {"rag_stored": False}
    if index and content.strip():
        title = (meta.get("title") or source_type)[:120]
        # `source` carries the corpus relpath -> resolvable; fall back to URL.
        source = written.get("relpath") or meta.get("source_url") or title
        r = _rag_index(content, namespace, source, title)
        if r.get("ok"):
            res = r.get("result") or {}
            indexed = {"rag_stored": bool(res.get("ok", True)),
                       "chunks_indexed": res.get("chunks_indexed"),
                       "dense_embedded": res.get("dense_embedded"),
                       "rag_source": source}
        else:
            indexed = {"rag_stored": False, "error": r.get("error")}
    otel_emit.record("research_ingested", {"namespace": namespace, "source_type": source_type,
                                          "corpus_ok": written.get("ok"),
                                          "rag_stored": indexed.get("rag_stored")})
    return {"ok": True, "corpus": written, "rag": indexed,
            "resolvable_via": written.get("relpath")}


# ── lazy, query-time distillation (local default; optional dense cloud) ────────
_DISTILL_SYS = (
    "You are a technical distiller. Given a query and retrieved source chunks, "
    "produce a focused, FAITHFUL distillation that answers the query. PRESERVE exact "
    "technical detail VERBATIM — code, equations, parameter names, numbers, version "
    "constraints. Do NOT compress away nuance or generalize. If the chunks do not "
    "answer the query, say so. No invented facts."
)


def distill_for_query(query: str, chunks: list[str], source_type: str = "web",
                      max_tokens: int = 1500) -> dict[str, Any]:
    """Distill ONLY the retrieved chunks, at query time (not ingest time). Routes by
    density: dense technical source_types -> cheap-cloud (conductor steer / DeepSeek)
    when RESEARCH_CLOUD_DISTILL is on; everything else -> local Qwen. Degrades to a
    raw chunk concatenation if no model is available — still honest, never raises."""
    chunks = [c for c in (chunks or []) if c and c.strip()]
    if not chunks:
        return {"ok": True, "distilled": "", "method": "empty", "query": query}
    blob = "\n\n---\n\n".join(c[:6000] for c in chunks)[:24000]
    prompt = f"Query: {query}\n\nRetrieved chunks:\n{blob}"
    method = None
    out: str | None = None

    # Label heartbeats for the local rc._llm path (it is wrapped at the source);
    # the cloud path below is wrapped explicitly since it bypasses rc._llm.
    rc._HB_PHASE = "distill"
    dense = source_type in DENSE_SOURCE_TYPES
    if CLOUD_DISTILL and dense:
        rc.heartbeat.beat("deep_research", progress="distill: cloud inference start")
        try:
            out = _conductor_distill(f"{_DISTILL_SYS}\n\n{prompt}", max_tokens=max_tokens)
        finally:
            rc.heartbeat.beat("deep_research", progress="distill: cloud inference done")
        if out:
            method = "cloud"
    if out is None:  # local default (and fallback when cloud is off/unavailable)
        out = rc._llm([{"role": "system", "content": _DISTILL_SYS},
                       {"role": "user", "content": prompt}],
                      max_tokens=max_tokens, temperature=0.1)
        if out:
            method = "local"
    if out is None:  # fully sovereign fallback — no model anywhere
        out = blob
        method = "raw"
    otel_emit.record(f"distill_{method}", {"query": query, "chunks": len(chunks),
                                           "source_type": source_type, "dense": dense})
    return {"ok": True, "distilled": out, "method": method, "query": query,
            "chunks_used": len(chunks)}


# ── resolve a chunk's source back to its on-disk document + provenance ─────────
def resolve_source(relpath_or_path: str) -> dict[str, Any]:
    """Given a RAG chunk's `source` (a corpus relpath) or an absolute path, read the
    backing on-disk document: full content + parsed front-matter provenance. This is
    how a synthesized claim resolves to the exact stored chunk it came from."""
    p = relpath_or_path or ""
    path = p if os.path.isabs(p) else os.path.join(CORPUS_DIR, p)
    if not os.path.exists(path):
        return {"ok": False, "error": "not found", "path": path}
    try:
        with open(path) as f:
            raw = f.read()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "path": path}
    fm: dict[str, Any] = {}
    body = raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                v = v.strip()
                if v and v[0] in '"[{':  # our writer JSON-encodes quoted/list values
                    try:
                        v = json.loads(v)
                    except Exception:  # noqa: BLE001
                        pass
                fm[k.strip()] = v
        body = m.group(2)
    return {"ok": True, "path": path, "relpath": corpus_relpath(path),
            "front_matter": fm, "content": body, "chars": len(body)}


def corpus_stats() -> dict[str, Any]:
    n_docs = 0
    namespaces: set[str] = set()
    if os.path.isdir(CORPUS_DIR):
        for root, _dirs, files in os.walk(CORPUS_DIR):
            for fn in files:
                if fn.endswith(".md"):
                    n_docs += 1
                    rel = os.path.relpath(root, CORPUS_DIR)
                    namespaces.add(rel.split(os.sep)[0] if rel != "." else "")
    return {"corpus_dir": CORPUS_DIR, "docs": n_docs, "namespaces": sorted(namespaces),
            "cloud_distill": CLOUD_DISTILL, "dense_source_types": sorted(DENSE_SOURCE_TYPES)}
