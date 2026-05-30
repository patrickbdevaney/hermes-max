"""Graph/AST-aware retrieval layer — ON TOP of the BM25+dense hybrid, never a
replacement (Stage 1.1, the RepoGraph-class multi-file lever).

The existing `rag_core` already does code-aware (tree-sitter) chunking by
definition. This module turns those definitions into a SYMBOL GRAPH:

  * nodes   = defined symbols (functions/classes/methods/…)
  * edges   = caller -> callee / referencer -> referenced, recovered by
              identifier-overlap inside each definition's body (robust across
              every language we chunk; no per-grammar tags query to break).
  * ranking = PageRank over that graph (Aider-style repo-map), so the symbols
              the codebase leans on most float to the top, token-budgeted.

It adds three capabilities the agent drives iteratively:
  * `retrieve_related(symbol)` — multi-hop callers/callees/imports (≈90% of real
    fixes need a multi-hop connection, so this is the high-value path).
  * `repo_map(token_budget)`   — the ranked, budgeted map of the repo.
  * a graph-rank signal folded into `search_code` as a THIRD fused list.

Graceful degradation: the graph lives in extra tables in the SAME sqlite store.
If building it fails (tree-sitter missing, parse error, anything), a meta flag
records `graph_available=0` and every graph entrypoint falls back to BM25/dense
with a clear "graph retrieval unavailable" note. Nothing here can break search.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import rag_core  # shared _connect / _rows_for / _fmt (rag_core never imports us at top)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# identifiers that are never interesting graph targets
_STOP = {
    "self", "cls", "this", "true", "false", "none", "null", "return", "import",
    "from", "def", "class", "function", "func", "const", "let", "var", "if",
    "else", "for", "while", "in", "is", "and", "or", "not", "new", "await",
    "async", "public", "private", "protected", "static", "void", "int", "str",
    "float", "bool", "list", "dict", "set", "type", "interface", "struct",
}


def ensure_graph_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS graph_symbols(
            chunk_id INTEGER PRIMARY KEY,
            repo TEXT, symbol TEXT, kind TEXT, path TEXT,
            start_line INTEGER, end_line INTEGER, rank REAL DEFAULT 0)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS graph_edges(
            repo TEXT, src TEXT, dst TEXT, etype TEXT,
            UNIQUE(repo, src, dst, etype))"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_gsym_sym ON graph_symbols(repo, symbol)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_gedge_src ON graph_edges(repo, src)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_gedge_dst ON graph_edges(repo, dst)")
    con.commit()


def _pagerank(nodes: list[str], out_edges: dict[str, set[str]],
              damping: float = 0.85, iters: int = 40) -> dict[str, float]:
    n = len(nodes)
    if n == 0:
        return {}
    rank = {x: 1.0 / n for x in nodes}
    outdeg = {x: len(out_edges.get(x, ())) for x in nodes}
    for _ in range(iters):
        new = {x: (1.0 - damping) / n for x in nodes}
        dangling = sum(rank[x] for x in nodes if outdeg[x] == 0)
        bump = damping * dangling / n
        for x in nodes:
            new[x] += bump
        for x in nodes:
            if outdeg[x] == 0:
                continue
            share = damping * rank[x] / outdeg[x]
            for m in out_edges[x]:
                if m in new:
                    new[m] += share
        rank = new
    return rank


def build_graph(con: sqlite3.Connection, repo: str) -> dict[str, Any]:
    """(Re)build the symbol graph for one repo from its already-indexed chunks.

    Called at the end of rag_core.index_repo, INSIDE a try — any failure here is
    caught by the caller and recorded as graph_available=0 (degrade, never crash).
    """
    ensure_graph_tables(con)
    con.execute("DELETE FROM graph_symbols WHERE repo=?", (repo,))
    con.execute("DELETE FROM graph_edges WHERE repo=?", (repo,))

    rows = con.execute(
        "SELECT id, symbol, kind, path, start_line, end_line, content FROM chunks WHERE repo=?",
        (repo,),
    ).fetchall()

    # defined symbol names in this repo (a token is an edge target only if defined here)
    defined: set[str] = {r["symbol"] for r in rows if r["symbol"] and not r["symbol"].startswith("<")}
    edges: set[tuple[str, str, str]] = set()
    out_edges: dict[str, set[str]] = {}

    for r in rows:
        src = r["symbol"]
        con.execute(
            "INSERT OR REPLACE INTO graph_symbols(chunk_id, repo, symbol, kind, path, start_line, end_line, rank)"
            " VALUES(?,?,?,?,?,?,?,0)",
            (r["id"], repo, src, r["kind"], r["path"], r["start_line"], r["end_line"]),
        )
        if not src or src.startswith("<"):
            continue
        body = r["content"] or ""
        etype = "import" if r["kind"] == "module" else "ref"
        seen: set[str] = set()
        for tok in _IDENT.findall(body):
            if tok == src or tok in seen or tok.lower() in _STOP:
                continue
            if tok in defined:
                seen.add(tok)
                edges.add((src, tok, etype))
                out_edges.setdefault(src, set()).add(tok)

    for s, d, et in edges:
        con.execute(
            "INSERT OR IGNORE INTO graph_edges(repo, src, dst, etype) VALUES(?,?,?,?)",
            (repo, s, d, et),
        )

    nodes = sorted({s for s, _, _ in edges} | {d for _, d, _ in edges} | defined)
    ranks = _pagerank(nodes, out_edges)
    for sym, rk in ranks.items():
        con.execute("UPDATE graph_symbols SET rank=? WHERE repo=? AND symbol=?", (rk, repo, sym))
    con.commit()
    return {"symbols": len(defined), "edges": len(edges), "nodes_ranked": len(ranks)}


def graph_available(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='graph_edges'"
    ).fetchone()
    if not row:
        return False
    n = con.execute("SELECT COUNT(*) AS n FROM graph_edges").fetchone()["n"]
    return n > 0


def rank_map(con: sqlite3.Connection) -> dict[str, float]:
    """symbol -> best (max) rank across its chunks, for the search_code boost."""
    out: dict[str, float] = {}
    for r in con.execute("SELECT symbol, rank FROM graph_symbols"):
        if r["symbol"] and (r["symbol"] not in out or r["rank"] > out[r["symbol"]]):
            out[r["symbol"]] = r["rank"]
    return out


# ── public ops (each opens its own connection via rag_core._connect) ─────────
def retrieve_related(symbol: str, hops: int = 1, k: int = 20) -> dict[str, Any]:
    """Multi-hop neighbors of a symbol: callees (it calls), callers (call it),
    and imports. Returns the defining chunk for each, tagged with relation+hop."""
    con, _ = rag_core._connect()
    try:
        if not graph_available(con):
            return {"ok": True, "symbol": symbol, "graph_available": False, "results": [],
                    "note": "graph retrieval unavailable — build the graph via index_repo, "
                            "or use search_code (BM25/dense) instead"}
        # BFS out (callees) and in (callers) up to `hops`
        hops = max(1, min(int(hops), 4))
        found: dict[str, dict[str, Any]] = {}
        frontier = {symbol}
        visited = {symbol}
        for depth in range(1, hops + 1):
            nxt: set[str] = set()
            for node in frontier:
                for r in con.execute(
                    "SELECT dst, etype FROM graph_edges WHERE src=?", (node,)
                ):
                    rel = "import" if r["etype"] == "import" else "callee"
                    found.setdefault(r["dst"], {"relation": rel, "hop": depth})
                    nxt.add(r["dst"])
                for r in con.execute("SELECT src FROM graph_edges WHERE dst=?", (node,)):
                    found.setdefault(r["src"], {"relation": "caller", "hop": depth})
                    nxt.add(r["src"])
            frontier = {x for x in nxt if x not in visited}
            visited |= frontier
            if not frontier:
                break

        if not found:
            return {"ok": True, "symbol": symbol, "graph_available": True, "results": [],
                    "note": f"no graph neighbors for '{symbol}' (unknown or leaf symbol)"}

        # rank neighbors by (hop asc, rank desc) and attach the defining chunk
        rmap = rank_map(con)
        order = sorted(found.items(), key=lambda kv: (kv[1]["hop"], -rmap.get(kv[0], 0.0)))[:k]
        results = []
        for sym, meta in order:
            crow = con.execute(
                "SELECT * FROM chunks WHERE symbol=? ORDER BY length(content) DESC LIMIT 1", (sym,)
            ).fetchone()
            entry = {"symbol": sym, "relation": meta["relation"], "hop": meta["hop"],
                     "rank": round(rmap.get(sym, 0.0), 6)}
            if crow:
                entry.update(rag_core._fmt(crow))
            results.append(entry)
        return {"ok": True, "symbol": symbol, "graph_available": True, "results": results}
    finally:
        con.close()


def repo_map(token_budget: int = 2000, repo: str | None = None) -> dict[str, Any]:
    """The PageRank-ranked, token-budgeted map of the repo's symbols."""
    con, _ = rag_core._connect()
    try:
        if not graph_available(con):
            return {"ok": True, "graph_available": False, "entries": [],
                    "note": "graph retrieval unavailable — index a repo first, or use search_code"}
        q = "SELECT symbol, kind, path, start_line, rank FROM graph_symbols"
        args: tuple = ()
        if repo:
            import os
            q += " WHERE repo=?"
            args = (os.path.abspath(os.path.expanduser(repo)),)
        q += " ORDER BY rank DESC, symbol ASC"
        budget_chars = max(200, int(token_budget) * 4)  # ~4 chars/token
        entries, used, truncated = [], 0, False
        for r in con.execute(q, args):
            if not r["symbol"] or r["symbol"].startswith("<"):
                continue
            line = f"{r['path']}:{r['start_line']}  {r['kind']} {r['symbol']}"
            if used + len(line) > budget_chars and entries:
                truncated = True
                break
            entries.append({"symbol": r["symbol"], "kind": r["kind"],
                            "location": f"{r['path']}:{r['start_line']}", "rank": round(r["rank"], 6)})
            used += len(line)
        return {"ok": True, "graph_available": True, "token_budget": token_budget,
                "truncated": truncated, "count": len(entries), "entries": entries}
    finally:
        con.close()


def graph_stats(con: sqlite3.Connection) -> dict[str, Any]:
    try:
        if not graph_available(con):
            return {"graph_available": False, "graph_symbols": 0, "graph_edges": 0}
        ns = con.execute("SELECT COUNT(*) AS n FROM graph_symbols").fetchone()["n"]
        ne = con.execute("SELECT COUNT(*) AS n FROM graph_edges").fetchone()["n"]
        return {"graph_available": True, "graph_symbols": ns, "graph_edges": ne}
    except Exception:  # noqa: BLE001
        return {"graph_available": False, "graph_symbols": 0, "graph_edges": 0}
