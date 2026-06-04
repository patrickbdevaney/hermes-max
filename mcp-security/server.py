"""mcp-security — supply-chain advisory check + egress classifier (port 9119).

Transport: streamable-http on $MCP_SECURITY_PORT (default 9119), path /mcp.
Health:    GET /health.

Two sovereign, offline-capable guards (supply-chain + egress) — uncommon for a coding agent:
  • check_install(ecosystem, package) — before pip/npm/uvx/npx installs, fail CLOSED on a
    confirmed-malware (MAL-*) advisory in the LOCAL advisory DB; WARN otherwise / when the DB
    is absent. No per-install network call.
  • classify_egress(command) — pattern-classify the network destinations a command would
    reach (url/git_remote/s3/gcs/scp/ssh/docker_registry/package_publish), for review before
    running. Observability, not a gate.
  • update_advisory_db() — refresh the local DB from the open OSV feeds (operator/cron).

Presence-gated: absent → the executor proceeds without these checks. Pure stdlib.
"""
from __future__ import annotations

import asyncio
import functools
import os
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import advisory_db
import egress

PORT = int(os.environ.get("MCP_SECURITY_PORT", "9119"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-security",
    instructions=(
        "Sovereign supply-chain + egress guards. Before any install (pip/npm/uvx/npx/cargo "
        "add/go get) call check_install(ecosystem, package): action='block' → do NOT install, "
        "report + escalate (never route around it); 'warn' → surface and proceed only if the "
        "task needs it; 'allow' → proceed. Before any command that phones home, call "
        "classify_egress(command) and include the summary in your step output."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _threaded(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def _aw(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-security", "port": PORT})


@mcp.tool()
@_threaded
def check_install(ecosystem: str, package: str) -> dict:
    """Check a package for malware advisories BEFORE installing it.
    ecosystem: 'pypi' | 'npm' | 'cargo' | 'go'. Returns {safe, action, advisories, reason}.
    action='block' → do NOT install (confirmed malware). action='warn' → surface to the
    operator (non-malware advisory, or the local DB is absent/stale). action='allow' → clean."""
    return advisory_db.check_package(ecosystem, package)


@mcp.tool()
@_threaded
def classify_egress(command: str) -> dict:
    """Classify the network destinations a shell command would reach (no execution). Returns
    {targets: [{kind, target}], summary}. Call before running anything that might phone home;
    proceed unless the destinations look unrelated to the task."""
    targets = egress.classify_egress(command)
    summary = (f"{len(targets)} network destination(s): "
               + ", ".join(f"{t.kind}({t.target[:40]})" for t in targets[:5])
               if targets else "No network destinations detected.")
    return {"targets": [{"kind": t.kind, "target": t.target} for t in targets], "summary": summary}


@mcp.tool()
@_threaded
def update_advisory_db() -> dict:
    """Refresh the local advisory DB from the open OSV feeds (operator/cron use; needs network).
    Returns {ok, entries, packages}."""
    try:
        import db_update
        return db_update.update()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
