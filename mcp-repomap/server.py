"""mcp-repomap — Aider's PageRank repo-map as an MCP context provider (M-Stage 4).

Transport: streamable-http on $MCP_REPOMAP_PORT (default 9111), path /mcp.
Health:    GET /health.

Answers "what is the most structurally important code I haven't been told about" via
NetworkX PageRank over a tree-sitter symbol graph (Aider's RepoMap class, MIT). This
is COMPLEMENTARY to RAG (semantic similarity) and LSP (precise symbol lookup): it
ranks symbols by how central they are to the codebase's structure. focus_files (the
files you're about to edit) bias the personalization vector so the map centers on
what's relevant to the current task.

Independent process; reaches no model (pure static analysis). If killed, Hermes
reports the tool unavailable and the agent falls back to RAG/LSP/reading files.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import time

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from aider.repomap import RepoMap

PORT = int(os.environ.get("MCP_REPOMAP_PORT", "9111"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
CACHE_TTL_S = float(os.environ.get("REPOMAP_CACHE_TTL_S", "60"))
MAX_FILES = int(os.environ.get("REPOMAP_MAX_FILES", "2000"))

mcp = FastMCP(
    "mcp-repomap",
    instructions=(
        "Structural repo orientation via Aider's PageRank repo-map. Call repo_map at "
        "the start of a coding task with the files you plan to edit as focus_files; "
        "it returns the most important symbols + relationships within a token budget. "
        "Complements search_code (semantic) and LSP (precise symbol lookup)."
    ),
    host=HOST, port=PORT, stateless_http=True, json_response=True,
)


def _threaded(fn):
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw


class _IO:
    """Minimal aider IO shim — RepoMap only reads files + emits advisory output."""
    encoding = "utf-8"

    def read_text(self, fname):
        try:
            with open(fname, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:  # noqa: BLE001
            return ""

    def tool_output(self, *a, **k):  # noqa: D401
        pass

    def tool_warning(self, *a, **k):
        pass

    def tool_error(self, *a, **k):
        pass


class _Model:
    """Token counter for RepoMap's budget fitting (chars/4 estimate — no model dep)."""
    def token_count(self, text):
        return max(1, len(str(text)) // 4)


_IGNORE_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules", ".mypy_cache",
                ".ruff_cache", ".pytest_cache", "dist", "build", ".tox"}
_CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".c",
             ".cpp", ".h", ".hpp", ".cs", ".php", ".scala", ".kt", ".swift"}

_cache: dict[tuple, tuple[float, str]] = {}


def _repo_files(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
        for n in names:
            if os.path.splitext(n)[1] in _CODE_EXT:
                out.append(os.path.join(dirpath, n))
                if len(out) >= MAX_FILES:
                    return out
    return out


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-repomap", "port": PORT,
                         "cache_entries": len(_cache)})


@mcp.tool()
@_threaded
def repo_map(repo_path: str, focus_files: list | None = None, token_budget: int = 1000) -> dict:
    """Aider PageRank repo-map: the most structurally important symbols in `repo_path`
    within `token_budget` tokens. `focus_files` (the files you're about to edit) bias
    the ranking toward what's relevant to the current task (Aider weights them 50x in
    the personalization vector). Complements search_code (semantic) and LSP (precise
    lookup) — use it to ORIENT to a codebase before reading full files. Cached 60s
    (repo structure changes slowly). Pure static analysis; no model call."""
    root = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(root):
        return {"ok": False, "error": f"not a directory: {repo_path}"}
    focus = [os.path.abspath(os.path.expanduser(f)) for f in (focus_files or [])]
    key = (root, tuple(sorted(focus)), int(token_budget))
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < CACHE_TTL_S:
        return {"ok": True, "repo_path": root, "token_budget": token_budget,
                "focus_files": focus, "cached": True, "repo_map": hit[1]}
    try:
        rm = RepoMap(map_tokens=int(token_budget), root=root, main_model=_Model(),
                     io=_IO(), verbose=False)
        all_files = _repo_files(root)
        other = [f for f in all_files if f not in set(focus)]
        out = rm.get_repo_map(focus, other) or ""
        _cache[key] = (now, out)
        return {"ok": True, "repo_path": root, "token_budget": token_budget,
                "focus_files": focus, "files_scanned": len(all_files), "cached": False,
                "repo_map": out,
                "note": "PageRank-ranked symbols; complements search_code (semantic) + LSP (precise)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
