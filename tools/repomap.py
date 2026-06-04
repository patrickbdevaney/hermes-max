"""tools/repomap.py — mention-seeded, PageRank-ranked repo map fitted to a token budget.

A compact `path → public symbols` map for planner context. The default `build_repomap`
ranks files by a personalized PageRank over the symbol-reference graph, seeded toward the
files/identifiers the task mentions, then binary-searches the file count so the rendered map
fits a hard token budget. Files the task cares about rank first; the rest stay out of context.

Pure-Python (no networkx — a small power-iteration PageRank), stdlib `ast` for Python +
regex for ts/js/go/rust. `build_repomap_flat` is the alphabetical fallback (and the path
taken when there is no query / no graph). Never raises.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", "plans", ".hermes-conductor",
})
_SKIP_EXTS = frozenset({".pyc", ".pyo", ".so", ".dll", ".exe", ".bin"})
_SRC_EXTS = (".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs")
_MAX_FILES = 200
_SYMS_PER_FILE = 20


# ── symbol extraction ─────────────────────────────────────────────────────────
def _extract_python_symbols(path: Path, include_private: bool) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for node in tree.body:  # top-level only (no double-counting methods)
        if isinstance(node, ast.ClassDef):
            if not include_private and node.name.startswith("_"):
                continue
            out.append(f"- class {node.name}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        include_private or not item.name.startswith("_")):
                    out.append(f"  - {_py_sig(item)}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if include_private or not node.name.startswith("_"):
                out.append(f"- {_py_sig(node)}")
    return out[:30]


def _py_sig(node) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")][:6]
    ret = ""
    if node.returns:
        try:
            ret = f" -> {ast.unparse(node.returns)}"
        except Exception:  # noqa: BLE001
            pass
    return f"{prefix} {node.name}({', '.join(args)}){ret}"


def _regex_symbols(path: Path, patterns: list[str], include_private: bool, limit: int = 200) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for line in text.splitlines()[:limit]:
        s = line.strip()
        for pat in patterns:
            m = re.match(pat, s)
            if m:
                name = m.group(1)
                if include_private or not name.startswith("_"):
                    out.append(f"- {name}")
    return out[:20]


def _extract_js_symbols(path: Path, p: bool) -> list[str]:
    return _regex_symbols(path, [
        r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)",
        r"export\s+(?:const|let)\s+(\w+)", r"export\s+(?:abstract\s+)?class\s+(\w+)",
        r"export\s+interface\s+(\w+)", r"export\s+type\s+(\w+)"], p)


def _extract_go_symbols(path: Path, p: bool) -> list[str]:
    return _regex_symbols(path, [r"func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(", r"type\s+(\w+)\s+struct"], p)


def _extract_rust_symbols(path: Path, p: bool) -> list[str]:
    return _regex_symbols(path, [r"pub\s+(?:async\s+)?fn\s+(\w+)", r"pub\s+struct\s+(\w+)",
                                 r"pub\s+enum\s+(\w+)", r"pub\s+trait\s+(\w+)"], p)


def _symbols_for(path: Path, include_private: bool) -> list[str]:
    if path.suffix == ".py":
        return _extract_python_symbols(path, include_private)
    if path.suffix in (".ts", ".js", ".tsx", ".jsx"):
        return _extract_js_symbols(path, include_private)
    if path.suffix == ".go":
        return _extract_go_symbols(path, include_private)
    if path.suffix == ".rs":
        return _extract_rust_symbols(path, include_private)
    return []


def _bare_name(sym: str) -> Optional[str]:
    m = re.search(r"\b([A-Za-z_]\w{2,})\b", sym.split("(")[0])  # skip 1-2 char noise
    return m.group(1) if m else None


# ── reference graph + personalized PageRank (pure-Python power iteration) ──────
def _reference_graph(all_syms: dict[str, list[str]], file_text: dict[str, str]) -> dict[str, dict[str, float]]:
    """Edge (definer → referencer), weight = times the definer's symbols appear in referencer."""
    adj: dict[str, dict[str, float]] = {f: {} for f in all_syms}
    defined = {f: {_bare_name(s) for s in syms if _bare_name(s)} for f, syms in all_syms.items()}
    for referencer, text in file_text.items():
        toks = set(re.findall(r"\b[A-Za-z_]\w{2,}\b", text))
        for definer, names in defined.items():
            if definer == referencer:
                continue
            hit = names & toks
            if hit:
                w = float(sum(text.count(n) for n in hit))
                if w:
                    adj[definer][referencer] = adj[definer].get(referencer, 0.0) + w
    return adj


def _pagerank(adj: dict[str, dict[str, float]], personalization: dict[str, float],
              damping: float = 0.85, iters: int = 40) -> dict[str, float]:
    nodes = list(adj.keys())
    n = len(nodes)
    if n == 0:
        return {}
    base = {x: personalization.get(x, 1.0 / n) for x in nodes}
    s = sum(base.values()) or 1.0
    base = {k: v / s for k, v in base.items()}
    rank = dict(base)
    out_w = {x: sum(adj[x].values()) for x in nodes}
    for _ in range(iters):
        new = {x: (1.0 - damping) * base[x] for x in nodes}
        for x in nodes:
            ow = out_w[x]
            if ow == 0.0:  # dangling — redistribute by personalization
                for t in nodes:
                    new[t] += damping * rank[x] * base[t]
            else:
                for nbr, w in adj[x].items():
                    new[nbr] += damping * rank[x] * (w / ow)
        rank = new
    return rank


def _rank_files(all_syms: dict[str, list[str]], file_text: dict[str, str], query: str,
                target_files: Optional[list[str]]) -> list[str]:
    n = max(len(all_syms), 1)
    q = (query or "").lower()
    pers: dict[str, float] = {}
    for node in all_syms:
        stem = Path(node).stem.lower()
        score = 1.0 / n
        if stem and stem in q:
            score += 100.0 / n
        if target_files and any(node.endswith(t) for t in target_files):
            score += 100.0 / n
        if Path(node).name.startswith("_"):
            score *= 0.1
        pers[node] = score
    adj = _reference_graph(all_syms, file_text)
    ranks = _pagerank(adj, pers)
    if not ranks:
        ranks = pers
    # long-symbol (>=8) query terms boost matching file stems
    long_syms = [w.lower() for w in re.split(r"\W+", query or "") if len(w) >= 8]
    for node in list(ranks):
        stem = Path(node).stem.lower()
        if any(sym in stem for sym in long_syms):
            ranks[node] *= 10.0
    return [p for p, _ in sorted(ranks.items(), key=lambda x: -x[1])]


# ── public API ────────────────────────────────────────────────────────────────
def _collect(root: Path, include_private: bool) -> tuple[dict[str, list[str]], dict[str, str]]:
    all_syms: dict[str, list[str]] = {}
    file_text: dict[str, str] = {}
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= _MAX_FILES:
            break
        if not path.is_file() or path.suffix not in _SRC_EXTS:
            continue
        if any(part in _SKIP_DIRS or part.startswith(".") for part in path.parts):
            continue
        syms = _symbols_for(path, include_private)
        if not syms:
            continue
        rel = str(path.relative_to(root))
        all_syms[rel] = syms
        try:
            file_text[rel] = path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            file_text[rel] = ""
        count += 1
    return all_syms, file_text


def _render(root_name: str, all_syms: dict[str, list[str]], paths: list[str], ranked: bool) -> str:
    out = [f"# Repo map: {root_name}" + (" (ranked)" if ranked else "") + "\n"]
    for p in paths:
        out.append(f"\n### {p}\n" + "\n".join(f"  {s}" for s in all_syms[p][:_SYMS_PER_FILE]) + "\n")
    return "".join(out)


def build_repomap(root, query: str = "", target_files: Optional[list[str]] = None,
                  max_tokens: int = 2000, include_private: bool = False) -> str:
    """Mention-seeded, PageRank-ranked repo map fitted to max_tokens (binary-searched within
    ~15%). Ranks toward `query`/`target_files`; falls back to alphabetical when no query."""
    root = Path(root).resolve()
    all_syms, file_text = _collect(root, include_private)
    if not all_syms:
        return f"# Repo map: {root.name}\n\n(no source symbols found)\n"
    ranked = bool(query)
    ordered = _rank_files(all_syms, file_text, query, target_files) if ranked else sorted(all_syms)
    ordered = [p for p in ordered if p in all_syms] + [p for p in all_syms if p not in ordered]

    budget = max_tokens * 4
    lo, hi, best = 1, len(ordered), _render(root.name, all_syms, ordered[:1], ranked)
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = _render(root.name, all_syms, ordered[:mid], ranked)
        if len(cand) <= budget:
            best = cand
            lo = mid + 1
            if (budget - len(cand)) / budget < 0.15:  # within 15% of budget → good enough
                break
        else:
            hi = mid - 1
    return best


def build_repomap_flat(root, max_tokens: int = 2000, include_private: bool = False) -> str:
    """Alphabetical, unranked fallback (no query / explicit flat path)."""
    return build_repomap(root, query="", max_tokens=max_tokens, include_private=include_private)
