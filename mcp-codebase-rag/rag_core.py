"""Hybrid (BM25 + dense) code retrieval over the user's own repositories.

ONE store: a single SQLite file holds the chunk rows, an FTS5 index (lexical /
BM25) and an optional sqlite-vec vector index (dense). ONE embed endpoint:
EMBED_BASE_URL (OpenAI-compatible /embeddings). If embeddings are unavailable
the server degrades cleanly to BM25-only — still useful, never broken.

Chunking is code-aware: tree-sitter splits by function/class/etc. A heuristic
splitter is the fallback when a grammar is missing or parsing fails, so a file
is never dropped silently.

Deliberately NOT built: HyDE / RAG-Fusion / ColBERT / Self-RAG / HippoRAG. Hybrid
dense+BM25 with good code chunking is ~85% of the value at ~10% of the fragility.

The ONE sanctioned precision lever on top (Stage 1.2): an optional cross-encoder
RERANKER (RERANK_BASE_URL). When present, the fused top-pool is re-ordered by the
reranker before the top-k is returned; when absent, the fused order is returned
unchanged. Both embeddings and reranker are independently optional and each
degrades to the next-best mode with a clear stats() banner — never a hard fail.
"""

from __future__ import annotations

import os
import re
import sqlite3
import struct
from pathlib import Path
from typing import Any

import httpx

try:
    import sqlite_vec
except Exception:  # noqa: BLE001 - optional; we degrade to BM25-only
    sqlite_vec = None

try:
    from tree_sitter_language_pack import get_parser
except Exception:  # noqa: BLE001 - optional; we fall back to heuristic chunking
    get_parser = None  # type: ignore[assignment]

# ── config ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.expanduser(os.environ.get("RAG_INDEX_PATH", "~/.hermes-max/rag/index.db"))
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "/model")
EMBED_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "30"))
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "64"))
# Reranker (Stage 1.2): an OPTIONAL cross-encoder that re-orders the fused
# top-pool before returning. Blank RERANK_BASE_URL ⇒ no rerank (fused order is
# returned unchanged). Independent of embeddings: rerank can sharpen even a
# BM25+graph result set. The endpoint is Cohere/Jina/vLLM-rerank shaped:
#   POST {RERANK_BASE_URL}/rerank {model, query, documents:[...]}
#     -> {"results": [{"index": i, "relevance_score": s}, ...]}
RERANK_BASE_URL = os.environ.get("RERANK_BASE_URL", "").rstrip("/")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "/model")
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "30"))
# How many fused candidates to hand the cross-encoder (it then picks the top k).
RERANK_POOL = int(os.environ.get("RERANK_POOL", "24"))
MAX_FILE_BYTES = int(os.environ.get("RAG_MAX_FILE_BYTES", str(1_500_000)))
MAX_CHUNK_CHARS = int(os.environ.get("RAG_MAX_CHUNK_CHARS", "6000"))
RERANK_DOC_CHARS = int(os.environ.get("RERANK_DOC_CHARS", "2000"))
RRF_K = 60

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "site-packages", ".tox", ".gradle", ".idea", ".vscode", "coverage",
    ".cache", "vendor", ".terraform",
}

EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".rs": "rust", ".go": "go", ".java": "java",
    ".rb": "ruby", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
}

# Node kinds that become their own chunk, per tree-sitter grammar.
TARGET_KINDS: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "generator_function_declaration", "class_declaration", "method_definition"},
    "typescript": {"function_declaration", "class_declaration", "method_definition", "interface_declaration", "enum_declaration", "abstract_class_declaration"},
    "tsx": {"function_declaration", "class_declaration", "method_definition", "interface_declaration", "enum_declaration", "abstract_class_declaration"},
    "rust": {"function_item", "struct_item", "enum_item", "trait_item", "impl_item"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "java": {"class_declaration", "interface_declaration", "method_declaration", "enum_declaration", "constructor_declaration"},
    "ruby": {"method", "class", "module"},
    "csharp": {"class_declaration", "method_declaration", "interface_declaration", "struct_declaration", "enum_declaration"},
    "c": {"function_definition", "struct_specifier"},
    "cpp": {"function_definition", "struct_specifier", "class_specifier"},
}

NAME_FALLBACK_KINDS = {
    "identifier", "type_identifier", "property_identifier", "field_identifier",
    "constant", "name",
}


# ── tree-sitter adapter (robust to property-vs-method and kind-vs-type drift) ─
def _c(obj: Any, name: str, *args: Any) -> Any:
    v = getattr(obj, name)
    return v(*args) if callable(v) else v


def _has(obj: Any, name: str) -> bool:
    return hasattr(obj, name)


def _kind(n: Any) -> str:
    return _c(n, "kind") if _has(n, "kind") else _c(n, "type")


def _children(n: Any) -> list[Any]:
    if _has(n, "child_count"):
        cc = _c(n, "child_count")
        return [_c(n, "child", i) for i in range(cc)]
    ch = getattr(n, "children")
    return list(ch() if callable(ch) else ch)


def _field(n: Any, name: str) -> Any:
    return _c(n, "child_by_field_name", name)


def _byte_range(n: Any) -> tuple[int, int]:
    return _c(n, "start_byte"), _c(n, "end_byte")


def _start_row(n: Any) -> int:
    if _has(n, "start_position"):
        return _c(_c(n, "start_position"), "row")
    sp = _c(n, "start_point")
    return sp[0]


def _end_row(n: Any) -> int:
    if _has(n, "end_position"):
        return _c(_c(n, "end_position"), "row")
    ep = _c(n, "end_point")
    return ep[0]


def _root(tree: Any) -> Any:
    return _c(tree, "root_node")


def _node_name(node: Any, src: bytes) -> str | None:
    nm = _field(node, "name")
    if nm is not None:
        a, b = _byte_range(nm)
        return src[a:b].decode("utf-8", "replace")
    for child in _children(node):
        if _kind(child) in NAME_FALLBACK_KINDS:
            a, b = _byte_range(child)
            return src[a:b].decode("utf-8", "replace")
    return None


# ── chunking ────────────────────────────────────────────────────────────────
class Chunk:
    __slots__ = ("symbol", "kind", "start_line", "end_line", "content")

    def __init__(self, symbol: str, kind: str, start_line: int, end_line: int, content: str):
        self.symbol = symbol
        self.kind = kind
        self.start_line = start_line
        self.end_line = end_line
        self.content = content[:MAX_CHUNK_CHARS]


def _collect(node: Any, src: bytes, targets: set[str], out: list[Chunk]) -> None:
    for child in _children(node):
        k = _kind(child)
        if k in targets:
            a, b = _byte_range(child)
            name = _node_name(child, src) or f"{k}@{_start_row(child) + 1}"
            out.append(Chunk(name, k, _start_row(child) + 1, _end_row(child) + 1,
                             src[a:b].decode("utf-8", "replace")))
        _collect(child, src, targets, out)


_HEURISTIC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def |class |function |func |fn |"
    r"public |private |protected |interface |struct |impl |trait |type )",
)


def _heuristic_chunks(text: str) -> list[Chunk]:
    """Fallback: split on def/class/function-ish boundary lines; else windows."""
    lines = text.splitlines()
    bounds = [i for i, ln in enumerate(lines) if _HEURISTIC_RE.match(ln)]
    chunks: list[Chunk] = []
    if bounds:
        bounds.append(len(lines))
        if bounds[0] > 0:
            chunks.append(Chunk("<module>", "module", 1, bounds[0],
                                "\n".join(lines[: bounds[0]])))
        for i in range(len(bounds) - 1):
            s, e = bounds[i], bounds[i + 1]
            header = lines[s].strip()
            name = re.sub(r"[^A-Za-z0-9_]", " ", header).split()[-1] if header else f"block@{s + 1}"
            chunks.append(Chunk(name, "block", s + 1, e, "\n".join(lines[s:e])))
    else:
        win = 80
        for s in range(0, max(1, len(lines)), win):
            e = min(len(lines), s + win)
            chunks.append(Chunk(f"lines_{s + 1}_{e}", "window", s + 1, e,
                                "\n".join(lines[s:e])))
    return [c for c in chunks if c.content.strip()]


def chunk_file(path: str, lang: str) -> list[Chunk]:
    try:
        raw = Path(path).read_bytes()
    except Exception:  # noqa: BLE001
        return []
    if b"\x00" in raw[:4096]:  # binary guard
        return []
    text = raw.decode("utf-8", "replace")

    targets = TARGET_KINDS.get(lang)
    if get_parser is not None and targets:
        try:
            parser = get_parser(lang)
            tree = parser.parse(text)
            root = _root(tree)
            src = text.encode("utf-8")
            out: list[Chunk] = []
            _collect(root, src, targets, out)
            # Top-level non-target statements -> a module preamble chunk.
            preamble: list[str] = []
            for child in _children(root):
                if _kind(child) not in targets:
                    a, b = _byte_range(child)
                    frag = src[a:b].decode("utf-8", "replace").strip()
                    if frag:
                        preamble.append(frag)
            if preamble:
                body = "\n".join(preamble)
                out.append(Chunk("<module>", "module", 1, min(len(text.splitlines()), 1) or 1, body))
            if out:
                return out
        except Exception:  # noqa: BLE001 - fall through to heuristic
            pass
    return _heuristic_chunks(text)


# ── embeddings ──────────────────────────────────────────────────────────────
def embeddings_configured() -> bool:
    return bool(EMBED_BASE_URL)


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Return one vector per text, or None if the endpoint is unusable.

    Returning None (rather than raising) is what lets the whole server degrade
    to BM25-only without any caller having to know embeddings exist.
    """
    if not EMBED_BASE_URL or not texts:
        return None
    vectors: list[list[float]] = []
    try:
        with httpx.Client(timeout=EMBED_TIMEOUT) as client:
            for i in range(0, len(texts), EMBED_BATCH):
                batch = texts[i: i + EMBED_BATCH]
                resp = client.post(
                    f"{EMBED_BASE_URL}/embeddings",
                    json={"model": EMBED_MODEL, "input": batch},
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                vectors.extend(item["embedding"] for item in data)
        return vectors
    except Exception:  # noqa: BLE001
        return None


# ── reranker (cross-encoder; optional, highest-precision-per-token step) ──────
def rerank_configured() -> bool:
    return bool(RERANK_BASE_URL)


def rerank(query: str, documents: list[str]) -> list[int] | None:
    """Re-order `documents` against `query` with the cross-encoder.

    Returns a list of indices into `documents`, best-first, or None if the
    endpoint is unset/unreachable/misshaped — in which case the caller keeps the
    fused order (graceful degradation; rerank only ever sharpens, never breaks).
    """
    if not RERANK_BASE_URL or not documents:
        return None
    docs = [d[:RERANK_DOC_CHARS] for d in documents]
    try:
        with httpx.Client(timeout=RERANK_TIMEOUT) as client:
            resp = client.post(
                f"{RERANK_BASE_URL}/rerank",
                json={"model": RERANK_MODEL, "query": query, "documents": docs},
            )
            resp.raise_for_status()
            payload = resp.json()
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        if not isinstance(results, list) or not results:
            return None
        order = sorted(
            results,
            key=lambda r: r.get("relevance_score", r.get("score", 0.0)),
            reverse=True,
        )
        out = [int(r["index"]) for r in order if 0 <= int(r.get("index", -1)) < len(docs)]
        return out or None
    except Exception:  # noqa: BLE001
        return None


# ── storage ─────────────────────────────────────────────────────────────────
def _connect() -> tuple[sqlite3.Connection, bool]:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    vec_ok = False
    if sqlite_vec is not None:
        try:
            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.enable_load_extension(False)
            vec_ok = True
        except Exception:  # noqa: BLE001
            vec_ok = False
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute(
        """CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY,
            repo TEXT, path TEXT, symbol TEXT, kind TEXT, lang TEXT,
            start_line INTEGER, end_line INTEGER, content TEXT)"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol)")
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
        "USING fts5(symbol, content, tokenize='unicode61')"
    )
    con.commit()
    return con, vec_ok


def _meta_get(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))


def _ensure_vec_table(con: sqlite3.Connection, dim: int) -> bool:
    stored = _meta_get(con, "embed_dim")
    if stored is not None and int(stored) != dim:
        con.execute("DROP TABLE IF EXISTS chunks_vec")
        con.execute("DELETE FROM meta WHERE key='embed_dim'")
        stored = None
    con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])")
    _meta_set(con, "embed_dim", str(dim))
    _meta_set(con, "embed_model", EMBED_MODEL)
    return True


def _ser(vec: list[float]) -> bytes:
    if sqlite_vec is not None:
        return sqlite_vec.serialize_float32(vec)
    return struct.pack(f"{len(vec)}f", *vec)


def _vec_table_exists(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
    ).fetchone()
    return row is not None


# ── public operations ────────────────────────────────────────────────────────
def index_repo(path: str) -> dict[str, Any]:
    repo = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(repo):
        return {"ok": False, "repo": repo, "error": "not a directory"}

    con, vec_ok = _connect()
    try:
        # Replace any prior index for this repo (idempotent reindex).
        old = [r["id"] for r in con.execute("SELECT id FROM chunks WHERE repo=?", (repo,))]
        if old:
            qs = ",".join("?" * len(old))
            con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({qs})", old)
            if _vec_table_exists(con):
                con.execute(f"DELETE FROM chunks_vec WHERE rowid IN ({qs})", old)
            con.execute(f"DELETE FROM chunks WHERE id IN ({qs})", old)
        con.commit()

        n_files = 0
        pending: list[int] = []
        pending_text: list[str] = []
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                ext = Path(fn).suffix.lower()
                lang = EXT_LANG.get(ext)
                if not lang:
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                chunks = chunk_file(fp, lang)
                if not chunks:
                    continue
                n_files += 1
                rel = os.path.relpath(fp, repo)
                for ch in chunks:
                    cur = con.execute(
                        "INSERT INTO chunks(repo, path, symbol, kind, lang, start_line, end_line, content) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (repo, rel, ch.symbol, ch.kind, lang, ch.start_line, ch.end_line, ch.content),
                    )
                    cid = cur.lastrowid
                    con.execute(
                        "INSERT INTO chunks_fts(rowid, symbol, content) VALUES(?,?,?)",
                        (cid, ch.symbol, ch.content),
                    )
                    pending.append(cid)
                    pending_text.append(f"{ch.symbol}\n{ch.content}")
        con.commit()

        n_chunks = len(pending)
        embedded = False
        if vec_ok and embeddings_configured() and pending_text:
            vectors = embed_texts(pending_text)
            if vectors and len(vectors) == len(pending):
                _ensure_vec_table(con, len(vectors[0]))
                for cid, vec in zip(pending, vectors):
                    con.execute(
                        "INSERT INTO chunks_vec(rowid, embedding) VALUES(?, ?)",
                        (cid, _ser(vec)),
                    )
                con.commit()
                embedded = True

        # Graph/AST layer (Stage 1.1) — ON TOP of BM25+dense. Any failure here
        # degrades to BM25/dense with graph_available=0; it never breaks indexing.
        graph_info: dict[str, Any] = {"graph_available": False}
        try:
            import graph_core

            gi = graph_core.build_graph(con, repo)
            _meta_set(con, "graph_available", "1")
            con.commit()
            graph_info = {"graph_available": True, **gi}
        except Exception as e:  # noqa: BLE001 - graph is best-effort
            try:
                _meta_set(con, "graph_available", "0")
                con.commit()
            except Exception:  # noqa: BLE001
                pass
            graph_info = {"graph_available": False, "graph_error": f"{type(e).__name__}: {e}"}

        return {
            "ok": True,
            "repo": repo,
            "files_indexed": n_files,
            "chunks_indexed": n_chunks,
            "dense_embedded": embedded,
            "mode": "hybrid" if embedded else "bm25-only",
            **graph_info,
        }
    finally:
        con.close()


def _markdown_chunks(text: str, source: str) -> list[Chunk]:
    """Section-aware chunking for ingested docs: split on markdown headings, then
    window any over-long section. Each chunk's symbol is its heading trail so it
    is co-retrievable with code symbols."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    cur_head = source or "doc"
    cur: list[str] = []
    for ln in lines:
        if re.match(r"^#{1,6}\s+\S", ln):
            if cur:
                sections.append((cur_head, cur))
            cur_head = ln.lstrip("#").strip()[:120]
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        sections.append((cur_head, cur))
    if not sections:
        sections = [(source or "doc", lines)]

    out: list[Chunk] = []
    for head, body in sections:
        block = "\n".join(body).strip()
        if not block:
            continue
        if len(block) <= MAX_CHUNK_CHARS:
            out.append(Chunk(head, "doc", 1, 1, block))
        else:
            for i in range(0, len(block), MAX_CHUNK_CHARS):
                out.append(Chunk(head, "doc", 1, 1, block[i: i + MAX_CHUNK_CHARS]))
    return out


def index_document(text: str, namespace: str, source: str = "", title: str = "") -> dict[str, Any]:
    """Ingest a distilled document (markdown) into the SAME hybrid store under a
    `namespace` (e.g. 'docs/fastapi'), co-retrievable with code via search_code.

    Idempotent per (namespace, source): re-ingesting the same URL replaces its
    prior chunks. Embeds when EMBED_BASE_URL is configured; otherwise BM25-only.
    No graph layer (docs aren't AST). Used by mcp-docs.ingest_doc.
    """
    if not text or not text.strip():
        return {"ok": False, "error": "empty document", "namespace": namespace}
    repo = namespace.strip() or "docs/uncategorized"
    src = source or (title or "doc")
    con, vec_ok = _connect()
    try:
        old = [r["id"] for r in con.execute(
            "SELECT id FROM chunks WHERE repo=? AND path=?", (repo, src))]
        if old:
            qs = ",".join("?" * len(old))
            con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({qs})", old)
            if _vec_table_exists(con):
                con.execute(f"DELETE FROM chunks_vec WHERE rowid IN ({qs})", old)
            con.execute(f"DELETE FROM chunks WHERE id IN ({qs})", old)

        chunks = _markdown_chunks(text, title or src)
        pending: list[int] = []
        pending_text: list[str] = []
        for ch in chunks:
            cur = con.execute(
                "INSERT INTO chunks(repo, path, symbol, kind, lang, start_line, end_line, content) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (repo, src, ch.symbol, "doc", "markdown", ch.start_line, ch.end_line, ch.content),
            )
            cid = cur.lastrowid
            con.execute("INSERT INTO chunks_fts(rowid, symbol, content) VALUES(?,?,?)",
                        (cid, ch.symbol, ch.content))
            pending.append(cid)
            pending_text.append(f"{ch.symbol}\n{ch.content}")
        con.commit()

        embedded = False
        if vec_ok and embeddings_configured() and pending_text:
            vectors = embed_texts(pending_text)
            if vectors and len(vectors) == len(pending):
                _ensure_vec_table(con, len(vectors[0]))
                for cid, vec in zip(pending, vectors):
                    con.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES(?, ?)",
                                (cid, _ser(vec)))
                con.commit()
                embedded = True

        return {"ok": True, "namespace": repo, "source": src,
                "chunks_indexed": len(pending), "dense_embedded": embedded,
                "mode": "hybrid" if embedded else "bm25-only"}
    finally:
        con.close()


def _fts_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression: OR of quoted word tokens."""
    words = re.findall(r"[A-Za-z0-9_]+", query)
    if not words:
        return ""
    return " OR ".join(f'"{w}"' for w in words[:32])


def _bm25(con: sqlite3.Connection, query: str, limit: int) -> list[tuple[int, float]]:
    match = _fts_query(query)
    if not match:
        return []
    rows = con.execute(
        "SELECT rowid, bm25(chunks_fts) AS score FROM chunks_fts "
        "WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
        (match, limit),
    ).fetchall()
    return [(r["rowid"], r["score"]) for r in rows]  # lower score = better


def _dense(con: sqlite3.Connection, query: str, limit: int, vec_ok: bool) -> list[tuple[int, float]]:
    if not (vec_ok and embeddings_configured() and _vec_table_exists(con)):
        return []
    vecs = embed_texts([query])
    if not vecs:
        return []
    rows = con.execute(
        "SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? "
        "ORDER BY distance LIMIT ?",
        (_ser(vecs[0]), limit),
    ).fetchall()
    return [(r["rowid"], r["distance"]) for r in rows]


def _rrf(ranked_lists: list[list[tuple[int, float]]], k: int) -> list[int]:
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, (cid, _) in enumerate(ranked):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)][:k]


def _rows_for(con: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    qs = ",".join("?" * len(ids))
    return {r["id"]: r for r in con.execute(f"SELECT * FROM chunks WHERE id IN ({qs})", ids)}


def _fmt(row: sqlite3.Row, snippet_chars: int = 1200) -> dict[str, Any]:
    content = row["content"]
    return {
        "symbol": row["symbol"],
        "kind": row["kind"],
        "lang": row["lang"],
        "path": row["path"],
        "location": f"{row['path']}:{row['start_line']}",
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "snippet": content[:snippet_chars],
    }


def search_code(query: str, k: int = 8) -> dict[str, Any]:
    con, vec_ok = _connect()
    try:
        total = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        if total == 0:
            return {"ok": True, "query": query, "results": [], "mode": "empty",
                    "note": "index is empty — call index_repo(path) first"}
        pool = max(k * 6, 50)
        bm = _bm25(con, query, pool)
        dn = _dense(con, query, pool, vec_ok)
        base = "hybrid" if dn else "bm25-only"

        # Graph-rank as a THIRD fused signal (Stage 1.1): re-rank the SAME
        # candidate pool by PageRank so well-connected symbols surface — never
        # injecting unrelated symbols, so it only sharpens, never derails.
        ranks: dict[str, float] = {}
        try:
            import graph_core

            if graph_core.graph_available(con):
                ranks = graph_core.rank_map(con)
        except Exception:  # noqa: BLE001 - graph is best-effort
            ranks = {}

        ranked_lists = [bm] + ([dn] if dn else [])
        graph_on = False
        cand_ids = {cid for cid, _ in bm} | {cid for cid, _ in dn}
        pre_rows = _rows_for(con, list(cand_ids))
        if ranks and cand_ids:
            graph_on = True
            gr = sorted(
                cand_ids,
                key=lambda cid: ranks.get(pre_rows[cid]["symbol"], 0.0) if cid in pre_rows else 0.0,
                reverse=True,
            )
            ranked_lists.append([(cid, 0.0) for cid in gr])

        # Fuse to a candidate set. When a reranker is available, fuse a LARGER
        # pool and let the cross-encoder choose the final top-k from it; with no
        # reranker, fuse exactly k (behaviour unchanged from before).
        fuse_n = max(k, min(RERANK_POOL, pool)) if rerank_configured() else k
        if len(ranked_lists) > 1:
            ids = _rrf(ranked_lists, fuse_n)
        else:
            ids = [cid for cid, _ in bm[:fuse_n]]
        mode = base + ("+graph" if graph_on else "")
        rows = pre_rows or _rows_for(con, ids)

        # Cross-encoder rerank (Stage 1.2): re-order the fused pool, then trim to
        # k. Any failure (endpoint down / misshaped reply) keeps the fused order,
        # so this only ever sharpens precision, never breaks retrieval.
        if rerank_configured() and len([i for i in ids if i in rows]) > 1:
            id_for_doc = [i for i in ids if i in rows]
            docs = [f"{rows[i]['symbol']}\n{rows[i]['content']}" for i in id_for_doc]
            order = rerank(query, docs)
            if order:
                ids = [id_for_doc[j] for j in order][:k]
                mode += "+rerank"
            else:
                ids = ids[:k]
        else:
            ids = ids[:k]

        results = [_fmt(rows[i]) for i in ids if i in rows]
        return {"ok": True, "query": query, "mode": mode, "results": results}
    finally:
        con.close()


def get_symbol_context(symbol: str, k: int = 5) -> dict[str, Any]:
    con, _ = _connect()
    try:
        rows = con.execute(
            "SELECT * FROM chunks WHERE symbol = ? ORDER BY kind LIMIT ?",
            (symbol, k),
        ).fetchall()
        if not rows:
            rows = con.execute(
                "SELECT * FROM chunks WHERE symbol LIKE ? ORDER BY length(symbol) LIMIT ?",
                (f"%{symbol}%", k),
            ).fetchall()
        return {"ok": True, "symbol": symbol,
                "results": [_fmt(r, snippet_chars=MAX_CHUNK_CHARS) for r in rows]}
    finally:
        con.close()


def find_similar(snippet: str, k: int = 8) -> dict[str, Any]:
    con, vec_ok = _connect()
    try:
        total = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        if total == 0:
            return {"ok": True, "results": [], "mode": "empty",
                    "note": "index is empty — call index_repo(path) first"}
        dn = _dense(con, snippet, max(k * 4, 40), vec_ok)
        if dn:
            ids = [cid for cid, _ in dn[:k]]
            mode = "dense"
        else:
            bm = _bm25(con, snippet, max(k * 4, 40))
            ids = [cid for cid, _ in bm[:k]]
            mode = "bm25-only"
        rows = _rows_for(con, ids)
        return {"ok": True, "mode": mode, "results": [_fmt(rows[i]) for i in ids if i in rows]}
    finally:
        con.close()


def stats() -> dict[str, Any]:
    con, vec_ok = _connect()
    try:
        n = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        repos = [r["repo"] for r in con.execute("SELECT DISTINCT repo FROM chunks")]
        graph: dict[str, Any] = {"graph_available": False}
        try:
            import graph_core

            graph = graph_core.graph_stats(con)
        except Exception:  # noqa: BLE001
            graph = {"graph_available": False}
        return {
            "chunks": n,
            "repos": repos,
            "dense_available": bool(vec_ok and embeddings_configured() and _vec_table_exists(con)),
            "embeddings_configured": embeddings_configured(),
            "rerank_configured": rerank_configured(),
            "retrieval_mode": (
                ("hybrid" if (vec_ok and embeddings_configured() and _vec_table_exists(con)) else "bm25")
                + ("+graph" if graph.get("graph_available") else "")
                + ("+rerank" if rerank_configured() else "")
            ),
            "sqlite_vec_loaded": vec_ok,
            "db_path": DB_PATH,
            **graph,
        }
    finally:
        con.close()
