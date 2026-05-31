"""codegraph_core.py — deterministic AST code-intelligence over a SQLite graph
(Phase 1.2). Complements RAG (semantic) and LSP (compiler-grade per-symbol) with
STRUCTURAL queries grep/embeddings can't do: blast-radius/impact, call hierarchy,
importers, dead code, structural pattern match.

Python-`ast` based (the codebase is ~all Python; tree-sitter is the multi-language
extension point). Call edges are NAME-resolved (callee identifier -> defining
symbols of that name) — an over-approximation of dynamic dispatch, which is the
right bias for "what could break" (blast radius). FTS not needed; the graph is the
index. Never raises out of a tool — returns a structured error.
"""
from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

DB_PATH = os.path.expanduser(os.environ.get("CODEGRAPH_DB", "~/.hermes-max/codegraph/graph.db"))
IGNORE_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules", ".mypy_cache",
               ".ruff_cache", ".pytest_cache", "vendor", "dist", "build", ".serena"}
MAX_FILES = int(os.environ.get("CODEGRAPH_MAX_FILES", "5000"))


def _connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS symbols(name TEXT, kind TEXT, file TEXT, lineno INT, end_lineno INT, parent TEXT);
    CREATE TABLE IF NOT EXISTS calls(caller TEXT, callee TEXT, file TEXT, lineno INT);
    CREATE TABLE IF NOT EXISTS imports(file TEXT, module TEXT, name TEXT);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE INDEX IF NOT EXISTS i_sym_name ON symbols(name);
    CREATE INDEX IF NOT EXISTS i_call_callee ON calls(callee);
    CREATE INDEX IF NOT EXISTS i_call_caller ON calls(caller);
    CREATE INDEX IF NOT EXISTS i_imp_module ON imports(module);
    """)


class _Walker(ast.NodeVisitor):
    """Collect symbols, name-resolved call edges, and imports, tracking the
    enclosing def/class so each call edge is attributed to its caller symbol."""
    def __init__(self, file: str):
        self.file = file
        self.scope: list[str] = []
        self.symbols: list[tuple] = []
        self.calls: list[tuple] = []
        self.imports: list[tuple] = []

    def _enter(self, node, kind):
        parent = self.scope[-1] if self.scope else ""
        self.symbols.append((node.name, kind, self.file, node.lineno,
                             getattr(node, "end_lineno", node.lineno), parent))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, n):      self._enter(n, "function")
    def visit_AsyncFunctionDef(self, n): self._enter(n, "function")
    def visit_ClassDef(self, n):         self._enter(n, "class")

    def visit_Call(self, n):
        caller = self.scope[-1] if self.scope else "<module>"
        f = n.func
        callee = (f.id if isinstance(f, ast.Name)
                  else f.attr if isinstance(f, ast.Attribute) else None)
        if callee:
            self.calls.append((caller, callee, self.file, getattr(n, "lineno", 0)))
        self.generic_visit(n)

    def visit_Import(self, n):
        for a in n.names:
            self.imports.append((self.file, a.name, a.asname or a.name))
        self.generic_visit(n)

    def visit_ImportFrom(self, n):
        mod = n.module or ""
        for a in n.names:
            self.imports.append((self.file, mod, a.name))
        self.generic_visit(n)


def _py_files(root: str) -> list[str]:
    out: list[str] = []
    for dp, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for nm in names:
            if nm.endswith(".py"):
                out.append(os.path.join(dp, nm))
                if len(out) >= MAX_FILES:
                    return out
    return out


def index_repo(repo_path: str) -> dict[str, Any]:
    """(Re)build the AST graph for `repo_path`. Idempotent (full rebuild)."""
    root = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(root):
        return {"ok": False, "error": f"not a directory: {repo_path}"}
    t0 = time.time()
    con = _connect()
    try:
        _schema(con)
        con.executescript("DELETE FROM symbols; DELETE FROM calls; DELETE FROM imports;")
        files = _py_files(root)
        nsym = ncall = nimp = 0
        for fp in files:
            rel = os.path.relpath(fp, root)
            try:
                tree = ast.parse(open(fp, encoding="utf-8", errors="replace").read(), filename=rel)
            except Exception:  # noqa: BLE001 - skip unparseable
                continue
            w = _Walker(rel)
            w.visit(tree)
            con.executemany("INSERT INTO symbols VALUES (?,?,?,?,?,?)", w.symbols)
            con.executemany("INSERT INTO calls VALUES (?,?,?,?)", w.calls)
            con.executemany("INSERT INTO imports VALUES (?,?,?)", w.imports)
            nsym += len(w.symbols); ncall += len(w.calls); nimp += len(w.imports)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('root', ?)", (root,))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('indexed_at', ?)", (str(time.time()),))
        con.commit()
        return {"ok": True, "root": root, "files": len(files), "symbols": nsym,
                "call_edges": ncall, "import_edges": nimp, "elapsed_s": round(time.time() - t0, 2)}
    finally:
        con.close()


def _ensure_indexed(repo_path: str | None) -> sqlite3.Connection:
    con = _connect()
    _schema(con)
    n = con.execute("SELECT COUNT(*) AS n FROM symbols").fetchone()["n"]
    root = (con.execute("SELECT v FROM meta WHERE k='root'").fetchone() or {})
    root = root["v"] if root else None
    if n == 0 and repo_path:
        con.close()
        index_repo(repo_path)
        con = _connect()
    return con


def _base(symbol: str) -> str:
    return symbol.split(".")[-1].split("/")[-1]


def code_callers(symbol: str, repo_path: str | None = None) -> dict[str, Any]:
    """Direct callers of `symbol` (reverse call edges, name-resolved)."""
    con = _ensure_indexed(repo_path)
    try:
        name = _base(symbol)
        rows = con.execute(
            "SELECT DISTINCT caller, file, lineno FROM calls WHERE callee=? ORDER BY file, lineno",
            (name,)).fetchall()
        return {"ok": True, "symbol": name, "callers": [dict(r) for r in rows], "count": len(rows)}
    finally:
        con.close()


def code_callees(symbol: str, repo_path: str | None = None) -> dict[str, Any]:
    """What `symbol` calls (forward call edges)."""
    con = _ensure_indexed(repo_path)
    try:
        name = _base(symbol)
        rows = con.execute(
            "SELECT DISTINCT callee, file, lineno FROM calls WHERE caller=? ORDER BY file, lineno",
            (name,)).fetchall()
        return {"ok": True, "symbol": name, "callees": [dict(r) for r in rows], "count": len(rows)}
    finally:
        con.close()


def code_impact(symbol: str, max_depth: int = 4, repo_path: str | None = None) -> dict[str, Any]:
    """Blast radius: the transitive set of symbols that (in)directly call `symbol` —
    what could break if it changes. BFS over reverse call edges to max_depth."""
    con = _ensure_indexed(repo_path)
    try:
        name = _base(symbol)
        seen: dict[str, int] = {}
        frontier = {name}
        depth = 0
        while frontier and depth < max_depth:
            depth += 1
            qs = ",".join("?" * len(frontier))
            rows = con.execute(
                f"SELECT DISTINCT caller, file, lineno FROM calls WHERE callee IN ({qs})",
                tuple(frontier)).fetchall()
            nxt = set()
            for r in rows:
                c = r["caller"]
                if c and c not in seen and c != name:
                    seen[c] = depth
                    nxt.add(c)
            frontier = nxt
        # files that import the symbol by name (module-level blast radius)
        importers = con.execute(
            "SELECT DISTINCT file FROM imports WHERE name=? OR module LIKE ?",
            (name, f"%{name}%")).fetchall()
        impacted = sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))
        return {"ok": True, "symbol": name,
                "impacted_symbols": [{"symbol": s, "depth": d} for s, d in impacted],
                "impacted_count": len(impacted),
                "importing_files": [r["file"] for r in importers],
                "note": "name-resolved reverse-call closure (over-approximation = conservative blast radius)"}
    finally:
        con.close()


def code_importers(file_or_module: str, repo_path: str | None = None) -> dict[str, Any]:
    """Which files import `file_or_module` (by module name)."""
    con = _ensure_indexed(repo_path)
    try:
        mod = _base(file_or_module).replace(".py", "")
        rows = con.execute(
            "SELECT DISTINCT file, module, name FROM imports WHERE module LIKE ? OR name=? ORDER BY file",
            (f"%{mod}%", mod)).fetchall()
        return {"ok": True, "module": mod, "importers": [dict(r) for r in rows], "count": len(rows)}
    finally:
        con.close()


def code_dead_code(repo_path: str | None = None) -> dict[str, Any]:
    """Symbols never referenced as a call target anywhere (candidate dead code).
    Excludes dunders, test_*, main, and module entry conventions — heuristic, advisory."""
    con = _ensure_indexed(repo_path)
    try:
        rows = con.execute("""
            SELECT s.name, s.kind, s.file, s.lineno FROM symbols s
            WHERE s.kind IN ('function','class')
              AND s.name NOT LIKE '\\_\\_%' ESCAPE '\\'
              AND s.name NOT LIKE 'test\\_%' ESCAPE '\\'
              AND s.name NOT IN ('main','setup','teardown','health','ready')
              AND NOT EXISTS (SELECT 1 FROM calls c WHERE c.callee = s.name)
            ORDER BY s.file, s.lineno
        """).fetchall()
        return {"ok": True, "dead_candidates": [dict(r) for r in rows], "count": len(rows),
                "note": "advisory: never called by name in this repo; may be an entrypoint/API/dynamic-dispatch"}
    finally:
        con.close()


def code_structural_search(pattern: str, repo_path: str | None = None, language: str = "python") -> dict[str, Any]:
    """ast-grep structural pattern match (the structural-rewrite primitive grep and
    embeddings can't do). Shells out to ast-grep if installed; otherwise degrades
    with a clear note."""
    binary = shutil.which("ast-grep") or shutil.which("sg")
    root = os.path.abspath(os.path.expanduser(repo_path or "."))
    if not binary:
        return {"ok": False, "available": False, "pattern": pattern,
                "note": "ast-grep not installed — `pip install ast-grep-cli` or `cargo install ast-grep` to enable "
                        "structural search; falling back is grep/LSP for now"}
    try:
        r = subprocess.run([binary, "run", "-p", pattern, "-l", language, "--json", root],
                           capture_output=True, text=True, timeout=60)
        import json as _json
        matches = _json.loads(r.stdout) if r.stdout.strip() else []
        return {"ok": True, "available": True, "pattern": pattern, "matches": matches[:100],
                "count": len(matches)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "available": True, "pattern": pattern, "error": f"{type(e).__name__}: {e}"}


def stats() -> dict[str, Any]:
    con = _connect()
    try:
        _schema(con)
        g = lambda q: con.execute(q).fetchone()[0]  # noqa: E731
        root = con.execute("SELECT v FROM meta WHERE k='root'").fetchone()
        return {"db_path": DB_PATH, "root": root["v"] if root else None,
                "symbols": g("SELECT COUNT(*) FROM symbols"),
                "call_edges": g("SELECT COUNT(*) FROM calls"),
                "import_edges": g("SELECT COUNT(*) FROM imports"),
                "ast_grep": bool(shutil.which("ast-grep") or shutil.which("sg"))}
    finally:
        con.close()
