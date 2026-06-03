#!/usr/bin/env python3
"""repo_dump.py — one Markdown file with the repo's directory tree + the full contents of
every CODE/DOC file, each tagged with its path (provenance).

Selection is deliberately conservative:
  • candidate set = `git ls-files` (so .gitignored files, venvs, build artifacts, and
    untracked junk are excluded for free) — falls back to a filtered filesystem walk if
    git is unavailable.
  • kept = source code + Markdown docs ONLY. Dropped: yaml/yml, env, json, toml, lockfiles,
    data (jsonl/csv/db/sqlite/log/parquet), notebooks, images/fonts/binaries, minified/map.
  • the dump file itself and obvious vendored/build dirs are always skipped.

Usage: python3 repo_dump.py [output.md]   (default: repo_dump.md at the repo root)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# code + docs we WANT (ext -> fenced-code language hint)
INCLUDE = {
    ".py": "python", ".sh": "bash", ".bash": "bash", ".rs": "rust", ".go": "go",
    ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx",
    ".mjs": "javascript", ".cjs": "javascript", ".md": "markdown", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".css": "css", ".html": "html", ".sql": "sql",
    ".rst": "rst",
}
# never include these, even if git-tracked (config / data / build / binary)
DENY_EXT = {".yaml", ".yml", ".env", ".lock", ".json", ".toml", ".ini", ".cfg", ".txt",
            ".jsonl", ".csv", ".tsv", ".parquet", ".db", ".sqlite", ".sqlite3", ".log",
            ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".pdf", ".zip",
            ".gz", ".tar", ".tgz", ".whl", ".so", ".bin", ".pyc", ".woff", ".woff2",
            ".ttf", ".eot", ".map", ".ipynb", ".lockb"}
DENY_NAME = {"package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock", ".gitignore",
             "repo_dump.py"}
DENY_DIR = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
            "target", ".next", ".cache", ".mypy_cache", ".pytest_cache", "snapshots",
            "site-packages"}
MAX_BYTES = 500_000  # truncate any single file larger than this (keeps the dump sane)


def repo_root() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return Path(__file__).resolve().parent


def tracked_files(root: Path) -> list[str]:
    """git-tracked files (relative POSIX paths). Falls back to a filtered walk."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                             text=True, timeout=30)
        if out.returncode == 0:
            return [p for p in out.stdout.splitlines() if p.strip()]
    except Exception:  # noqa: BLE001
        pass
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DENY_DIR]
        for fn in filenames:
            files.append(str(Path(dirpath, fn).relative_to(root).as_posix()))
    return files


def keep(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in DENY_DIR for p in parts):
        return False
    name = Path(rel).name
    if name in DENY_NAME:
        return False
    ext = Path(rel).suffix.lower()
    if ext in DENY_EXT or ext not in INCLUDE:
        return False
    return True


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def build_tree(paths: list[str]) -> str:
    """Render an indented directory tree from the kept file paths."""
    tree: dict = {}
    for p in sorted(paths):
        node = tree
        for part in Path(p).parts:
            node = node.setdefault(part, {})
    lines: list[str] = ["."]

    def walk(node: dict, prefix: str) -> None:
        items = sorted(node.items(), key=lambda kv: (not bool(kv[1]), kv[0]))  # dirs first
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{name}")
            if child:
                walk(child, prefix + ("    " if last else "│   "))

    walk(tree, "")
    return "\n".join(lines)


def main() -> int:
    root = repo_root()
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "repo_dump.md"
    out_abs = out_path.resolve()

    kept: list[str] = []
    for rel in tracked_files(root):
        if not keep(rel):
            continue
        ap = root / rel
        if not ap.is_file() or ap.resolve() == out_abs:
            continue
        kept.append(rel)
    kept.sort()

    total_bytes = 0
    skipped_binary = 0
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"# Repo dump — `{root.name}`\n\n")
        out.write("Code + docs only (source + Markdown). Excludes gitignored files, venvs, "
                  "build artifacts, yaml/env/json/toml/lockfiles, data, and binaries.\n\n")
        out.write(f"- Files included: **{len(kept)}**\n")
        out.write(f"- Branch: `{_branch(root)}`  ·  Commit: `{_commit(root)}`\n\n")
        out.write("## Directory tree (included files)\n\n```\n")
        out.write(build_tree(kept))
        out.write("\n```\n\n---\n\n## Files\n\n")
        for rel in kept:
            ap = root / rel
            try:
                raw = ap.read_bytes()
            except OSError as e:
                out.write(f"### `{rel}`\n\n_(unreadable: {e})_\n\n")
                continue
            if is_binary(raw):
                skipped_binary += 1
                continue
            truncated = len(raw) > MAX_BYTES
            text = raw[:MAX_BYTES].decode("utf-8", errors="replace")
            total_bytes += len(raw)
            lang = INCLUDE.get(ap.suffix.lower(), "")
            out.write(f"### `{rel}`\n\n")
            # guard against ``` inside the file closing the fence early: use a 4-backtick fence
            out.write(f"````{lang}\n{text}\n````\n")
            if truncated:
                out.write(f"\n_…truncated at {MAX_BYTES} bytes (file is {len(raw)} bytes)._\n")
            out.write("\n")

    print(f"✓ wrote {out_path}")
    print(f"  files: {len(kept)} included" + (f", {skipped_binary} skipped (binary)" if skipped_binary else ""))
    print(f"  bytes dumped: {total_bytes:,}")
    print(f"  size: {out_path.stat().st_size:,} bytes")
    return 0


def _branch(root: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def _commit(root: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


if __name__ == "__main__":
    raise SystemExit(main())
