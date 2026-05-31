"""mcp-lsp — LSP-over-MCP via Serena (oraios/serena), M-Stage 1.

Transport: streamable-http on $MCP_LSP_PORT (default 9112), path /mcp.
Health:    GET /health.

Compiler-grade symbol intelligence is the single highest-value retrieval gap:
find-references via a language server is ~50ms vs tens of seconds via grep, and
cross-file type errors surface immediately after an edit (tight feedback loop)
instead of at verify-gate time.

Serena is the LSP engine (it bundles pyright/gopls/etc. via solidlsp). It exposes no
curl-able /health and uses its own tool names, so this thin wrapper:
  * launches Serena's MCP server as a subprocess on an INTERNAL port (default 9113),
  * exposes a real /health (incl. backend reachability) for the stack manifest,
  * proxies the high-value LSP tools under stable lsp_* names.

Independent process; if Serena/the language server is down the tools return a clear
error and the agent falls back to search_code/grep — never a hard crash.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import socket
import subprocess
import time

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

PORT = int(os.environ.get("MCP_LSP_PORT", "9112"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("SERENA_BACKEND_PORT", "9113"))
BACKEND_URL = f"http://{HOST}:{BACKEND_PORT}/mcp"
PROJECT_ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("LSP_PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERENA_BIN = os.environ.get("SERENA_BIN", os.path.join(REPO_ROOT, "vendor", "serena", ".venv", "bin", "serena"))
BACKEND_BOOT_TIMEOUT = float(os.environ.get("SERENA_BOOT_TIMEOUT_S", "60"))

mcp = FastMCP(
    "mcp-lsp",
    instructions=(
        "LSP symbol intelligence (Serena backend). Prefer lsp_find_references / "
        "lsp_go_to_definition over grep for symbol lookup, and lsp_diagnostics after "
        "an edit to catch cross-file type errors immediately."
    ),
    host=HOST, port=PORT, stateless_http=True, json_response=True,
)


def _threaded(fn):
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw


def _port_up(port: int) -> bool:
    try:
        socket.create_connection((HOST, port), 1).close()
        return True
    except OSError:
        return False


_proc = None


def _ensure_backend() -> bool:
    """Start the Serena MCP subprocess if it isn't already listening. Idempotent."""
    global _proc
    if _port_up(BACKEND_PORT):
        return True
    if not os.path.exists(SERENA_BIN):
        return False
    log = open(os.path.expanduser(os.path.join(
        os.environ.get("HERMES_MAX_LOG_DIR", "~/.hermes-max/logs"), "lsp-backend.log")), "a")
    _proc = subprocess.Popen(
        [SERENA_BIN, "start-mcp-server", "--transport", "streamable-http",
         "--host", HOST, "--port", str(BACKEND_PORT), "--project", PROJECT_ROOT],
        stdout=log, stderr=log)
    deadline = time.time() + BACKEND_BOOT_TIMEOUT
    while time.time() < deadline:
        if _port_up(BACKEND_PORT):
            return True
        time.sleep(1)
    return _port_up(BACKEND_PORT)


def _call_backend(tool: str, args: dict) -> dict:
    """Call a Serena tool over MCP. Returns {ok, result} or {ok: False, error}."""
    if not _ensure_backend():
        return {"ok": False, "error": "Serena LSP backend unavailable (not installed or failed to boot)"}

    async def _go():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        box: dict = {}
        try:
            async with streamablehttp_client(BACKEND_URL) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool(tool, args)
                    txt = getattr(res.content[0], "text", "") if res.content else ""
                    d = res.structuredContent or (json.loads(txt) if txt else txt)
                    box["v"] = d.get("result", d) if isinstance(d, dict) else d
        except BaseException:  # noqa: BLE001 - the streamable-http client raises a
            # benign ExceptionGroup on context teardown AFTER a successful call; if we
            # already captured the result, return it rather than discarding it.
            if "v" in box:
                return box["v"]
            raise
        return box["v"]
    try:
        out = asyncio.run(asyncio.wait_for(_go(), timeout=float(os.environ.get("LSP_CALL_TIMEOUT_S", "60"))))
        return {"ok": True, "tool": tool, "result": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "tool": tool, "error": f"{type(e).__name__}: {e}"}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-lsp", "port": PORT,
                         "backend": "serena", "backend_port": BACKEND_PORT,
                         "backend_up": _port_up(BACKEND_PORT),
                         "serena_installed": os.path.exists(SERENA_BIN),
                         "project_root": PROJECT_ROOT})


@mcp.tool()
@_threaded
def lsp_find_references(name_path: str, relative_path: str) -> dict:
    """Find all references to a symbol via the language server (~50ms, exact — use
    this INSTEAD of grep for symbol lookup). name_path: the symbol (e.g. 'verify' or
    'MyClass/method'); relative_path: the file (relative to the project root) that
    DEFINES it. Returns each referencing location with content around it."""
    return _call_backend("find_referencing_symbols",
                         {"name_path": name_path, "relative_path": relative_path})


@mcp.tool()
@_threaded
def lsp_go_to_definition(name_path: str, relative_path: str = "") -> dict:
    """Locate a symbol's definition (and body) via the language server. name_path is
    the symbol; relative_path optionally scopes the search to one file/dir. Use to
    jump to the real signature instead of guessing or grepping."""
    args = {"name_path_pattern": name_path, "include_body": True}
    if relative_path:
        args["relative_path"] = relative_path
    return _call_backend("find_symbol", args)


@mcp.tool()
@_threaded
def lsp_diagnostics(relative_path: str) -> dict:
    """Compiler-grade diagnostics (type errors, undefined names, …) for a file from
    the language server. Run this AFTER editing a file to catch cross-file type
    errors immediately, rather than waiting for the verify gate."""
    return _call_backend("get_diagnostics_for_file", {"relative_path": relative_path})


@mcp.tool()
@_threaded
def lsp_hover(name_path: str, relative_path: str = "") -> dict:
    """Hover info for a symbol — its signature + body/docstring from the language
    server (the structured 'what is this' lookup). name_path is the symbol;
    relative_path optionally scopes to one file."""
    args = {"name_path_pattern": name_path, "include_body": True}
    if relative_path:
        args["relative_path"] = relative_path
    return _call_backend("find_symbol", args)


@mcp.tool()
@_threaded
def lsp_rename(name_path: str, relative_path: str, new_name: str) -> dict:
    """Rename a symbol across the whole project via the language server (updates
    every reference safely — the cross-file rename grep can't do). name_path: the
    symbol; relative_path: the file defining it; new_name: the new identifier."""
    return _call_backend("rename_symbol",
                         {"name_path": name_path, "relative_path": relative_path, "new_name": new_name})


@mcp.tool()
@_threaded
def lsp_find_symbol(name_path: str, relative_path: str = "") -> dict:
    """Find a symbol (function/class/method) by name across the project via the
    language server — the fast structured alternative to grepping for a definition."""
    args = {"name_path_pattern": name_path}
    if relative_path:
        args["relative_path"] = relative_path
    return _call_backend("find_symbol", args)


@mcp.tool()
@_threaded
def lsp_activate_project(project_root: str) -> dict:
    """Point the LSP backend at a different project root (the language server
    re-indexes it). Call this when you start working in a repo other than the default."""
    return _call_backend("activate_project", {"project": os.path.abspath(os.path.expanduser(project_root))})


if __name__ == "__main__":
    # Warm the Serena backend in a BACKGROUND thread so the wrapper binds its own
    # port (and serves /health) IMMEDIATELY — blocking on the ~12s language-server
    # boot here would make start-all's health grace time out and report DOWN.
    import threading
    threading.Thread(target=lambda: _ensure_backend(), daemon=True).start()
    mcp.run(transport="streamable-http")
