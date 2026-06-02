"""scopemap_core — the two-phase context protocol (Fix 1), pure static analysis.

Phase 1 (always first, no LLM, <2s even on a 300k-token repo): a REPO_MAP.md — one
line per source file (path + a one-sentence description from the module docstring /
top-level class & function names). It fits in ~8-12k tokens regardless of repo size.

Phase 2: planner-directed selective retrieval — request_context() returns exactly
the files the planner asked for, at the requested depth (full bodies | signatures).

The map is cached at ~/.hermes-max/scope_cache/<repo_hash>/REPO_MAP.md and considered
stale when any source file is newer than the cache (cheap mtime check) or when
invalidate() is called (the checkpoint MCP calls it after a verified-green commit).

Greenfield (a directory with no source files) → an empty map; the planner proceeds
straight to PLAN.md with no retrieval phase.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from typing import Any, Optional

# Directories never walked (VCS internals, deps, build output, caches).
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "dist", "build", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode", ".next",
    "target", ".tox", ".eggs", "site-packages", ".cache", "coverage", ".turbo",
}
# Source extensions we describe (others are listed only if no source exists).
_SRC_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb",
            ".c", ".h", ".cpp", ".hpp", ".cc", ".sh", ".lua", ".php", ".swift",
            ".kt", ".scala", ".sql", ".yaml", ".yml", ".toml"}

_MAX_CHARS = 48000      # ~12k tokens — the hard ceiling on the map
_MAX_FILE_BYTES = 800_000   # don't parse absurdly large files
_DESC_WIDTH = 44


def _config_root() -> str:
    return os.path.expanduser(
        os.environ.get("HERMES_MAX_STATE_DIR", "~/.hermes-max"))


def _repo_hash(cwd: str) -> str:
    return hashlib.sha1(os.path.abspath(cwd).encode()).hexdigest()[:12]


def _cache_dir(cwd: str) -> str:
    d = os.path.join(_config_root(), "scope_cache", _repo_hash(cwd))
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(cwd: str) -> str:
    return os.path.join(_cache_dir(cwd), "REPO_MAP.md")


# ── per-file one-line descriptions ────────────────────────────────────────────
def _py_desc(path: str) -> str:
    """Module docstring first line, else 'defines: <top-level names>'."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read(_MAX_FILE_BYTES)
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return ""
    doc = ast.get_docstring(tree)
    if doc:
        first = doc.strip().splitlines()[0].strip()
        if first:
            return first
    names = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if names:
        shown = ", ".join(names[:6]) + (" …" if len(names) > 6 else "")
        return "defines: " + shown
    return ""


_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?"
    r"(?:async\s+)?(?:function|class|interface|type|const|struct|impl|fn|def|func|enum)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)")
_DOC_RE = re.compile(r"^\s*(?://+|#+|/\*+|\*+|--+)\s*(.+?)\s*$")


def _generic_desc(path: str) -> str:
    """First meaningful comment line, else 'defines: <regex-matched names>'."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(60)]
    except OSError:
        return ""
    # a leading comment block → its first MEANINGFUL line (skip shebangs and
    # decorative rule/box-drawing bars like ═══ or ----).
    for ln in head[:8]:
        m = _DOC_RE.match(ln)
        if not m:
            continue
        text = m.group(1)
        if text.startswith(("!", "/")):
            continue
        if sum(c.isalnum() for c in text) < 6:   # mostly punctuation → a rule bar
            continue
        return text[:120]
    names: list[str] = []
    for ln in head:
        m = _DEF_RE.match(ln)
        if m:
            names.append(m.group(1))
        if len(names) >= 6:
            break
    if names:
        return "defines: " + ", ".join(names[:6]) + (" …" if len(names) >= 6 else "")
    return ""


def _describe(path: str) -> str:
    if path.endswith(".py"):
        d = _py_desc(path)
        if d:
            return d
    return _generic_desc(path)


# ── map generation ────────────────────────────────────────────────────────────
def _iter_source_files(cwd: str) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, files in os.walk(cwd):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _SRC_EXT:
                out.append(os.path.join(dirpath, fn))
        if len(out) > 5000:
            break
    return out


def _newest_mtime(files: list[str]) -> float:
    newest = 0.0
    for f in files:
        try:
            newest = max(newest, os.path.getmtime(f))
        except OSError:
            continue
    return newest


def _build_map(cwd: str) -> str:
    files = _iter_source_files(cwd)
    if not files:
        return ("# REPO_MAP — " + os.path.abspath(cwd) + "\n\n"
                "(greenfield — no existing source files. Proceed directly to PLAN.md; "
                "there is nothing to map or retrieve.)\n")
    rel = sorted(os.path.relpath(f, cwd) for f in files)
    # group is implicit via sorted paths; one line per file
    lines: list[str] = []
    header = (f"# REPO_MAP — {os.path.abspath(cwd)}\n"
              f"# {len(files)} source files · static analysis · "
              f"request bodies/signatures via request_context()\n\n")
    budget = _MAX_CHARS - len(header) - 200
    used = 0
    omitted = 0
    by_rel = {os.path.relpath(f, cwd): f for f in files}
    for r in rel:
        desc = _describe(by_rel[r])
        col = r if len(r) <= _DESC_WIDTH else r[: _DESC_WIDTH - 1] + "…"
        line = f"{col.ljust(_DESC_WIDTH)} — {desc}".rstrip() + "\n"
        if used + len(line) > budget:
            omitted += 1
            continue
        used += len(line)
        lines.append(line)
    body = "".join(lines)
    if omitted:
        body += f"\n… {omitted} more files omitted to keep the map ≤ ~12k tokens.\n"
    return header + body


def get_repo_map(cwd: str, force: bool = False) -> str:
    """Return REPO_MAP.md for `cwd`, regenerating if missing/stale. <2s; cached."""
    cwd = os.path.abspath(os.path.expanduser(cwd or "."))
    if not os.path.isdir(cwd):
        return f"# REPO_MAP\n\n(error: not a directory: {cwd})\n"
    cache = _cache_path(cwd)
    files = _iter_source_files(cwd)
    fresh = False
    if not force and os.path.exists(cache):
        try:
            fresh = os.path.getmtime(cache) >= _newest_mtime(files)
        except OSError:
            fresh = False
    if fresh:
        try:
            with open(cache) as f:
                return f.read()
        except OSError:
            pass
    content = _build_map(cwd)
    try:
        with open(cache, "w") as f:
            f.write(content)
    except OSError:
        pass
    return content


def invalidate(cwd: str) -> dict[str, Any]:
    """Drop the cached map for `cwd` (called by the checkpoint MCP on verified-green
    commit — the repo changed meaningfully)."""
    cwd = os.path.abspath(os.path.expanduser(cwd or "."))
    cache = _cache_path(cwd)
    removed = False
    try:
        if os.path.exists(cache):
            os.remove(cache)
            removed = True
    except OSError:
        pass
    return {"ok": True, "invalidated": removed, "repo": cwd}


# ── phase 2: selective retrieval ──────────────────────────────────────────────
def _signatures(path: str) -> str:
    """Top-level + class-method signatures + docstrings, no bodies (Python)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read(_MAX_FILE_BYTES))
    except (OSError, SyntaxError, ValueError):
        return _head(path, 40)
    out: list[str] = []
    doc = ast.get_docstring(tree)
    if doc:
        out.append(f'"""{doc.strip().splitlines()[0]}"""')

    def sig(node: ast.AST, indent: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            args = ", ".join(arg.arg for arg in node.args.args)
            out.append(f"{indent}{a}def {node.name}({args}): ...")
        elif isinstance(node, ast.ClassDef):
            out.append(f"{indent}class {node.name}:")
            d = ast.get_docstring(node)
            if d:
                out.append(f'{indent}    """{d.strip().splitlines()[0]}"""')
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig(sub, indent + "    ")

    for n in tree.body:
        sig(n)
    return "\n".join(out) if out else _head(path, 40)


def _head(path: str, n: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(next(f, "") for _ in range(n))
    except OSError:
        return ""


def _full(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(_MAX_FILE_BYTES)
    except OSError:
        return ""


def request_context(cwd: str, files: dict[str, Any]) -> str:
    """Fetch the planner's CONTEXT_REQUEST. `files` = {need_full:[...],
    need_signatures:[...], need_nothing:[...]}. Returns a single context blob with the
    full bodies and signature-only sections the planner asked for (need_nothing is
    honored by simply not fetching). Paths are repo-relative; bounded."""
    cwd = os.path.abspath(os.path.expanduser(cwd or "."))
    full = files.get("need_full") or []
    sigs = files.get("need_signatures") or []
    parts: list[str] = []
    total = 0
    cap = 120_000

    def add(header: str, body: str) -> None:
        nonlocal total
        if total >= cap:
            return
        chunk = f"\n===== {header} =====\n{body}\n"
        parts.append(chunk[: max(0, cap - total)])
        total += len(chunk)

    for rel in full:
        p = os.path.join(cwd, rel)
        add(f"{rel}  (full)", _full(p) if os.path.isfile(p) else "(not found)")
    for rel in sigs:
        p = os.path.join(cwd, rel)
        body = (_signatures(p) if rel.endswith(".py") else _head(p, 60)) \
            if os.path.isfile(p) else "(not found)"
        add(f"{rel}  (signatures)", body)
    if not parts:
        return "(no files requested)"
    out = "".join(parts)
    if total >= cap:
        out += "\n… context truncated at ~30k tokens.\n"
    return out
