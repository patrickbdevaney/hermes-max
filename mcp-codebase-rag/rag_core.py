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
EMBED_MODEL = os.environ.get("EMBED_MODEL", "/model")
EMBED_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "30"))  # index-time batches may be slow
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "64"))
# Fail-fast bounds so an UNAVAILABLE backend (down, or up-but-not-serving) never
# stalls an interactive call for the full 30s. Connect is short (a closed/dropped
# port fails in ~1.5s); the QUERY path (search_code → _dense / rerank) uses a short
# read bound — a hung backend degrades to BM25+graph in a few seconds, not 30. On
# any failure the endpoint is marked unavailable (below), so EVERY subsequent call
# skips the lane sub-second until the negative-cache TTL re-probes.
_BACKEND_CONNECT_TIMEOUT = float(os.environ.get("RAG_BACKEND_CONNECT_TIMEOUT_S", "1.5"))
QUERY_EMBED_TIMEOUT = float(os.environ.get("RAG_QUERY_EMBED_TIMEOUT_S", "4"))
QUERY_RERANK_TIMEOUT = float(os.environ.get("RAG_QUERY_RERANK_TIMEOUT_S", "4"))
# Reranker (Stage 1.2): an OPTIONAL cross-encoder that re-orders the fused
# top-pool before returning. No rerank endpoint ⇒ fused order returned unchanged.
# Independent of embeddings: rerank can sharpen even a BM25+graph result set.
#   POST {RERANK_BASE_URL}/rerank {model, query, documents:[...]}
#     -> {"results": [{"index": i, "relevance_score": s}, ...]}
RERANK_MODEL = os.environ.get("RERANK_MODEL", "/model")
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "30"))
# How many fused candidates to hand the cross-encoder (it then picks the top k).
RERANK_POOL = int(os.environ.get("RERANK_POOL", "24"))

# ── embed/rerank endpoint resolution (auto-detect the local serves) ──────────
# EMBED_BASE_URL / RERANK_BASE_URL may be set explicitly in .env, OR left blank and
# auto-detected when the serve-embed.sh (:8002) / serve-rerank.sh (:8003) servers
# are up — so `hm up` on gpu_local just works without editing .env. The URL is
# normalized to hit the OpenAI-style /v1 path (vLLM serves only /v1; local_serve
# serves both), so a bare `http://127.0.0.1:8002` is accepted and works on either
# backend. Auto-detect is gated by RAG_EMBED_AUTODETECT (default on); set it to 0
# to pin BM25-only deterministically (the smoke tests do).
# Explicit endpoint (env or monkeypatched in tests). Empty ⇒ try auto-detect.
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "").strip()
RERANK_BASE_URL = os.environ.get("RERANK_BASE_URL", "").strip()
_EMBED_AUTODETECT = os.environ.get("RAG_EMBED_AUTODETECT", "1").strip().lower() not in ("0", "false", "no")
_EMBED_PROBE_URL = os.environ.get("EMBED_AUTODETECT_URL", "http://127.0.0.1:8002")
_RERANK_PROBE_URL = os.environ.get("RERANK_AUTODETECT_URL", "http://127.0.0.1:8003")
_PROBE_TIMEOUT = float(os.environ.get("RAG_PROBE_TIMEOUT_S", "1.0"))
_PROBE_NEG_TTL = float(os.environ.get("RAG_PROBE_NEG_TTL_S", "30"))


def _normalize_openai_base(u: str) -> str:
    """A bare host:port (no path) gets `/v1` appended so the request hits the
    OpenAI-style /v1/embeddings|/v1/rerank route (works on vLLM AND local_serve).
    An explicit path (…/v1, …/openai) is left as-is."""
    from urllib.parse import urlparse
    u = (u or "").strip().rstrip("/")
    if not u:
        return ""
    p = urlparse(u)
    return u + "/v1" if (not p.path or p.path == "") else u


def _server_up(base_url: str) -> bool:
    probe = base_url.rstrip("/")
    for path in ("/health", "/v1/models"):
        try:
            with httpx.Client(timeout=_PROBE_TIMEOUT) as c:
                if c.get(f"{probe}{path}").status_code < 500:
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


# resolution cache: positive is sticky; negative re-probes after _PROBE_NEG_TTL.
_resolved: dict[str, tuple[str, float]] = {}


def _resolve_base(kind: str, env_val: str, probe_url: str) -> str:
    if env_val:
        return _normalize_openai_base(env_val)
    if not _EMBED_AUTODETECT:
        return ""
    import time as _t
    cached = _resolved.get(kind)
    now = _t.time()
    if cached is not None:
        url, ts = cached
        if url or (now - ts) < _PROBE_NEG_TTL:
            return url
    url = _normalize_openai_base(probe_url) if _server_up(probe_url) else ""
    _resolved[kind] = (url, now)
    return url


def embed_base_url() -> str:
    """Resolved embeddings endpoint (explicit EMBED_BASE_URL → auto-detected serve
    on :8002 → ""). Reads the module global so tests can monkeypatch it."""
    return _resolve_base("embed", EMBED_BASE_URL, _EMBED_PROBE_URL)


def rerank_base_url() -> str:
    """Resolved rerank endpoint (explicit RERANK_BASE_URL → auto-detected serve on
    :8003 → ""). Reads the module global so tests can monkeypatch it."""
    return _resolve_base("rerank", RERANK_BASE_URL, _RERANK_PROBE_URL)


def _mark_unavailable(kind: str) -> None:
    """Flip a backend's availability flag to False after a failed/hung real call.

    The 1s autodetect probe (/health, /v1/models) can pass on a serve that is
    up-but-not-serving (accepts the connection, never answers /embeddings or
    /rerank) — and a positive probe is sticky, so embeddings_configured()/
    rerank_configured() would keep returning True and every call would eat the
    read timeout. Recording a negative result here makes the flag honestly read
    False, so the dense/rerank lane is SKIPPED sub-second on subsequent calls
    until the negative-cache TTL re-probes. Only affects the autodetect path
    (explicit env URLs are left as configured)."""
    import time as _t
    _resolved[kind] = ("", _t.time())
MAX_FILE_BYTES = int(os.environ.get("RAG_MAX_FILE_BYTES", str(1_500_000)))
MAX_CHUNK_CHARS = int(os.environ.get("RAG_MAX_CHUNK_CHARS", "6000"))
RERANK_DOC_CHARS = int(os.environ.get("RERANK_DOC_CHARS", "2000"))
RRF_K = 60
# Stage 2: index_repo processes files in batches, committing + heartbeating per
# batch so a large repo reports progress, a mid-index kill keeps prior batches,
# and the next run resumes. A heartbeat stamp file is written to the shared
# watchdog heartbeat dir (best-effort; degrades silently) so the watchdog's
# liveness check sees index_repo is WORKING and never false-kills a long index.
RAG_INDEX_BATCH = int(os.environ.get("RAG_INDEX_BATCH", "64"))
_WD_STATE_DIR = os.path.expanduser(os.environ.get("WATCHDOG_STATE_DIR", "~/.hermes-max/watchdog"))
HEARTBEAT_DIR = os.path.expanduser(os.environ.get("HEARTBEAT_DIR", os.path.join(_WD_STATE_DIR, "heartbeats")))

try:
    import otel_emit
except Exception:  # noqa: BLE001 - observability is best-effort; never break indexing
    otel_emit = None  # type: ignore[assignment]

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
    return bool(embed_base_url())


def embed_texts(texts: list[str], timeout: float | None = None) -> list[list[float]] | None:
    """Return one vector per text, or None if the endpoint is unusable.

    Returning None (rather than raising) is what lets the whole server degrade
    to BM25-only without any caller having to know embeddings exist. `timeout`
    bounds the call — the query path (search_code → _dense) passes a SHORT one so
    an up-but-not-serving backend degrades fast; index-time batches use the longer
    EMBED_TIMEOUT. On failure the endpoint is marked unavailable so the next call
    skips the dense lane sub-second instead of re-paying the timeout.
    """
    base = embed_base_url()
    if not base or not texts:
        return None
    to = httpx.Timeout(timeout if timeout is not None else EMBED_TIMEOUT,
                       connect=_BACKEND_CONNECT_TIMEOUT)
    vectors: list[list[float]] = []
    try:
        with httpx.Client(timeout=to) as client:
            for i in range(0, len(texts), EMBED_BATCH):
                batch = texts[i: i + EMBED_BATCH]
                resp = client.post(
                    f"{base}/embeddings",
                    json={"model": EMBED_MODEL, "input": batch},
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                vectors.extend(item["embedding"] for item in data)
        return vectors
    except Exception:  # noqa: BLE001
        _mark_unavailable("embed")  # flip dense_available False → next call skips fast
        return None


# ── reranker (cross-encoder; optional, highest-precision-per-token step) ──────
def rerank_configured() -> bool:
    return bool(rerank_base_url())


def rerank(query: str, documents: list[str]) -> list[int] | None:
    """Re-order `documents` against `query` with the cross-encoder.

    Returns a list of indices into `documents`, best-first, or None if the
    endpoint is unset/unreachable/misshaped — in which case the caller keeps the
    fused order (graceful degradation; rerank only ever sharpens, never breaks).
    """
    base = rerank_base_url()
    if not base or not documents:
        return None
    docs = [d[:RERANK_DOC_CHARS] for d in documents]
    # Rerank is always interactive (query path) → short bound; a hung serve degrades
    # to the fused order in a few seconds, not 30. RERANK_TIMEOUT remains the cap.
    to = httpx.Timeout(min(QUERY_RERANK_TIMEOUT, RERANK_TIMEOUT), connect=_BACKEND_CONNECT_TIMEOUT)
    try:
        with httpx.Client(timeout=to) as client:
            resp = client.post(
                f"{base}/rerank",
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
        _mark_unavailable("rerank")  # flip rerank_configured False → next call skips fast
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
    # Per-file fingerprint table (Stage 2) — makes index_repo idempotent +
    # resumable: a file whose fingerprint (size+mtime) is unchanged AND already has
    # chunks is skipped on reindex, so a killed mid-index resumes instead of
    # restarting from zero. embedded=1 once its vectors are stored.
    con.execute(
        """CREATE TABLE IF NOT EXISTS files_index(
            repo TEXT, path TEXT, fp TEXT, embedded INTEGER DEFAULT 0,
            PRIMARY KEY(repo, path))"""
    )
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


# ── Stage 2: robust-init helpers (pre-flight scan, fingerprint, heartbeat) ────
def _file_fp(path: str) -> str:
    """Cheap change-detector: size+mtime. Stable enough to skip unchanged files on
    reindex; a real content edit changes mtime so it is always re-indexed."""
    try:
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return "0:0"


def scan_repo(path: str) -> dict[str, Any]:
    """Pre-flight scan: WHAT would be indexed, BEFORE touching the store. Returns
    the indexable file list (path, lang, size, fp), counts by language, total
    bytes, and a look-ahead duration estimate — logged so the operator sees the
    scope upfront and the watchdog knows what's normal."""
    repo = os.path.abspath(os.path.expanduser(path))
    files: list[tuple[str, str, str, int, str]] = []  # (abs, rel, lang, size, fp)
    by_lang: dict[str, int] = {}
    total_bytes = 0
    oversize = 0
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in names:
            lang = EXT_LANG.get(Path(fn).suffix.lower())
            if not lang:
                continue
            fp_abs = os.path.join(root, fn)
            try:
                size = os.path.getsize(fp_abs)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                oversize += 1
                continue
            files.append((fp_abs, os.path.relpath(fp_abs, repo), lang, size, _file_fp(fp_abs)))
            by_lang[lang] = by_lang.get(lang, 0) + 1
            total_bytes += size
    n = len(files)
    # Look-ahead estimate (mirrors the watchdog's index_repo estimator).
    per_file = float(os.environ.get("EST_INDEX_PER_FILE_S", "0.077"))
    per_mb = float(os.environ.get("EST_INDEX_PER_MB_S", "0.5"))
    est_s = round(max(0.5, n * per_file + (total_bytes / 1_048_576) * per_mb), 1) if n else 0.0
    return {
        "repo": repo, "files": files, "n_files": n, "by_lang": by_lang,
        "total_bytes": total_bytes, "oversize_skipped": oversize, "est_s": est_s,
    }


_HB_T0: dict[str, float] = {}  # per-tool start time for tqdm-style elapsed/ETA


def _heartbeat(tool: str, done: int, total: int, note: str = "") -> None:
    """Best-effort liveness stamp the watchdog can read to confirm a long index is
    WORKING (never false-killed). Emits a tqdm-style index_progress OTel span with
    elapsed + running ETA (Stage 7a). Any failure here is swallowed — observability
    never breaks indexing."""
    import time as _time
    t0 = _HB_T0.setdefault(tool, _time.time())
    elapsed = max(0.0, _time.time() - t0)
    eta = (elapsed * (total - done) / done) if (done and total and done > 0) else None
    try:
        os.makedirs(HEARTBEAT_DIR, exist_ok=True)
        import json as _json
        tmp = os.path.join(HEARTBEAT_DIR, f".{tool}.tmp")
        dst = os.path.join(HEARTBEAT_DIR, f"{tool}.json")
        with open(tmp, "w") as f:
            _json.dump({"tool": tool, "ts": _time.time(), "done": done,
                        "total": total, "note": note, "elapsed_s": round(elapsed, 1)}, f)
        os.replace(tmp, dst)
    except Exception:  # noqa: BLE001
        pass
    if otel_emit is not None:
        pct = round(100 * done / total, 1) if total else 100.0
        otel_emit.record("index_progress", {
            "tool": tool, "done": done, "total": total, "pct": pct, "note": note,
            "elapsed_s": round(elapsed, 1),
            "eta_s": (round(eta, 1) if eta is not None else None)})


# ── public operations ────────────────────────────────────────────────────────
def _delete_file_chunks(con: sqlite3.Connection, repo: str, rel: str) -> None:
    """Remove a single file's chunks (+fts +vec) so it can be cleanly re-indexed."""
    ids = [r["id"] for r in con.execute(
        "SELECT id FROM chunks WHERE repo=? AND path=?", (repo, rel))]
    if not ids:
        return
    qs = ",".join("?" * len(ids))
    con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({qs})", ids)
    if _vec_table_exists(con):
        con.execute(f"DELETE FROM chunks_vec WHERE rowid IN ({qs})", ids)
    con.execute(f"DELETE FROM chunks WHERE id IN ({qs})", ids)


def index_repo(path: str, batch_size: int | None = None, full: bool = False) -> dict[str, Any]:
    """Robust, ALWAYS-usable-state indexing (Stage 2).

    Empty repo ⇒ instant clean empty success (not a hang). Otherwise: pre-flight
    scan (logged scope + look-ahead), batched indexing that commits + heartbeats
    per batch (a kill mid-index keeps prior batches; the next run resumes via
    per-file fingerprints), unparseable files SKIPPED not fatal, dense embeddings
    degrade to BM25+graph when the embed endpoint is absent/down, and a post-init
    self-check that the index is actually queryable. Never a silent failure."""
    repo = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(repo):
        return {"ok": False, "repo": repo, "error": "not a directory"}
    bs = max(1, int(batch_size or RAG_INDEX_BATCH))
    _HB_T0.pop("index_repo", None)  # fresh per-run clock for tqdm elapsed/ETA

    # ── pre-flight scan: report the scope BEFORE touching the store ──────────
    scan = scan_repo(repo)
    files = scan["files"]
    total = scan["n_files"]
    _heartbeat("index_repo", 0, total or 1,
               note=f"pre-flight: {total} files, {scan['by_lang']}, "
                    f"{scan['total_bytes']/1_048_576:.1f}MB, est ~{scan['est_s']}s")

    con, vec_ok = _connect()
    try:
        # Deletions: files previously indexed for this repo that are gone now.
        on_disk = {rel for (_a, rel, _l, _s, _f) in files}
        prior = {r["path"]: (r["fp"], r["embedded"])
                 for r in con.execute(
                     "SELECT path, fp, embedded FROM files_index WHERE repo=?", (repo,))}
        removed = 0
        for rel in list(prior):
            if rel not in on_disk:
                _delete_file_chunks(con, repo, rel)
                con.execute("DELETE FROM files_index WHERE repo=? AND path=?", (repo, rel))
                removed += 1
        if removed:
            con.commit()

        # ── empty repo: clean empty success, valid (queryable) state, no hang ─
        embed_ok = vec_ok and embeddings_configured()
        if total == 0:
            graph_info = _build_graph(con, repo)
            con.commit()
            health = _self_check(con, repo, embed_ok)
            note = ("indexed 0 files (empty repo) — RAG will return no results "
                    "until files exist")
            _heartbeat("index_repo", 1, 1, note="empty repo — clean empty success")
            if otel_emit is not None:
                otel_emit.record("index_repo_done", {"repo": repo, "files": 0,
                                                     "empty": True, "note": note})
            return {"ok": True, "repo": repo, "empty": True, "files_indexed": 0,
                    "files_scanned": 0, "chunks_indexed": 0, "skipped_unparseable": 0,
                    "skipped_oversize": scan["oversize_skipped"], "removed_deleted": removed,
                    "files_resumed_unchanged": 0, "dense_embedded": False,
                    "mode": "empty", "note": note, "index_health": health, **graph_info}

        # ── decide which files actually need (re)indexing ────────────────────
        embedded_any = False
        n_indexed = 0
        n_chunks = 0
        skipped_unparseable = 0
        resumed = 0
        batch_ids: list[int] = []
        batch_text: list[str] = []
        processed = 0

        def _flush_batch() -> None:
            nonlocal embedded_any
            con.commit()  # checkpoint: prior files are now durable (resumable)
            if embed_ok and batch_text:
                vectors = embed_texts(batch_text)
                if vectors and len(vectors) == len(batch_ids):
                    _ensure_vec_table(con, len(vectors[0]))
                    for cid, vec in zip(batch_ids, vectors):
                        con.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES(?, ?)",
                                    (cid, _ser(vec)))
                    con.commit()
                    embedded_any = True
            batch_ids.clear()
            batch_text.clear()

        for (fp_abs, rel, lang, _size, fp) in files:
            processed += 1
            prev = prior.get(rel)
            has_chunks = bool(con.execute(
                "SELECT 1 FROM chunks WHERE repo=? AND path=? LIMIT 1", (repo, rel)).fetchone())
            # Resume/idempotent: unchanged fingerprint AND chunks already present ⇒ skip.
            if (not full) and prev is not None and prev[0] == fp and has_chunks:
                resumed += 1
                if processed % bs == 0 or processed == total:
                    _heartbeat("index_repo", processed, total,
                               note=f"{n_indexed} indexed · {resumed} resumed · "
                                    f"{skipped_unparseable} skipped")
                continue

            # (re)index this file
            _delete_file_chunks(con, repo, rel)
            try:
                chunks = chunk_file(fp_abs, lang)
            except Exception:  # noqa: BLE001 - a parse blow-up is a SKIP, never fatal
                chunks = []
            if not chunks:
                skipped_unparseable += 1
                con.execute("INSERT OR REPLACE INTO files_index(repo, path, fp, embedded) "
                            "VALUES(?,?,?,0)", (repo, rel, fp))
            else:
                for ch in chunks:
                    cur = con.execute(
                        "INSERT INTO chunks(repo, path, symbol, kind, lang, start_line, end_line, content) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (repo, rel, ch.symbol, ch.kind, lang, ch.start_line, ch.end_line, ch.content))
                    cid = cur.lastrowid
                    con.execute("INSERT INTO chunks_fts(rowid, symbol, content) VALUES(?,?,?)",
                                (cid, ch.symbol, ch.content))
                    batch_ids.append(cid)
                    batch_text.append(f"{ch.symbol}\n{ch.content}")
                con.execute("INSERT OR REPLACE INTO files_index(repo, path, fp, embedded) "
                            "VALUES(?,?,?,?)", (repo, rel, fp, 1 if embed_ok else 0))
                n_indexed += 1
                n_chunks += len(chunks)

            if len(batch_ids) >= bs or processed == total:
                _flush_batch()
                _heartbeat("index_repo", processed, total,
                           note=f"{n_indexed} indexed · {resumed} resumed · "
                                f"{skipped_unparseable} skipped")
        _flush_batch()

        # Graph/AST layer — ON TOP of BM25+dense; any failure degrades cleanly.
        graph_info = _build_graph(con, repo)
        con.commit()

        # ── post-init self-check: prove the index is queryable NOW ───────────
        health = _self_check(con, repo, embed_ok)
        mode = ("hybrid" if embedded_any else
                ("bm25+graph" if graph_info.get("graph_available") else "bm25-only"))
        degraded_note = None
        if embeddings_configured() and not embedded_any:
            degraded_note = "dense embeddings unavailable — indexed in BM25+graph mode"
        result = {
            "ok": True, "repo": repo, "empty": False,
            "files_indexed": n_indexed, "files_scanned": total,
            "files_resumed_unchanged": resumed, "chunks_indexed": n_chunks,
            "skipped_unparseable": skipped_unparseable,
            "skipped_oversize": scan["oversize_skipped"], "removed_deleted": removed,
            "dense_embedded": embedded_any, "mode": mode,
            "est_s": scan["est_s"], "index_health": health, **graph_info,
        }
        if degraded_note:
            result["degraded_note"] = degraded_note
        if otel_emit is not None:
            otel_emit.record("index_repo_done", {"repo": repo, "files": n_indexed,
                                                "chunks": n_chunks, "mode": mode,
                                                "skipped": skipped_unparseable, "resumed": resumed})
        return result
    finally:
        con.close()


def _build_graph(con: sqlite3.Connection, repo: str) -> dict[str, Any]:
    """Build the AST/graph layer; best-effort, never breaks indexing."""
    try:
        import graph_core
        gi = graph_core.build_graph(con, repo)
        _meta_set(con, "graph_available", "1")
        con.commit()
        return {"graph_available": True, **gi}
    except Exception as e:  # noqa: BLE001 - graph is best-effort
        try:
            _meta_set(con, "graph_available", "0")
            con.commit()
        except Exception:  # noqa: BLE001
            pass
        return {"graph_available": False, "graph_error": f"{type(e).__name__}: {e}"}


def _self_check(con: sqlite3.Connection, repo: str, embed_ok: bool) -> dict[str, Any]:
    """Confirm the freshly-built index is actually QUERYABLE (a corrupt/empty index
    is caught at init, not at first use mid-task). Runs a trivial count + FTS probe
    + vec-table check and reports health rather than raising."""
    health: dict[str, Any] = {"queryable": False}
    try:
        n = con.execute("SELECT count(*) AS c FROM chunks WHERE repo=?", (repo,)).fetchone()["c"]
        health["chunks_for_repo"] = int(n)
        # FTS probe — exercises the lexical lane end-to-end.
        con.execute("SELECT rowid FROM chunks_fts LIMIT 1").fetchone()
        health["fts_ok"] = True
        if embed_ok and _vec_table_exists(con):
            vc = con.execute("SELECT count(*) AS c FROM chunks_vec").fetchone()["c"]
            health["vectors"] = int(vc)
        health["queryable"] = True
    except Exception as e:  # noqa: BLE001
        health["error"] = f"{type(e).__name__}: {e}"
    return health


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
    # Skip the dense lane immediately if it isn't truly available (no sqlite-vec,
    # no embed endpoint, or no vector table) — sub-second, no network call.
    if not (vec_ok and embeddings_configured() and _vec_table_exists(con)):
        return []
    # Short query-path timeout: an up-but-not-serving embed endpoint degrades to
    # BM25+graph in a few seconds (and marks itself unavailable for next time),
    # never the full 30s index-time budget.
    vecs = embed_texts([query], timeout=QUERY_EMBED_TIMEOUT)
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
            "embed_endpoint": embed_base_url() or None,
            "rerank_endpoint": rerank_base_url() or None,
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
