"""mcp-checkpoint — verified-green git checkpointing as an independent MCP server.

Transport: streamable-http on $MCP_CHECKPOINT_PORT (default 9106), path /mcp.
Health:    GET /health (independent of the MCP protocol, for healthcheck.sh).

A thin wrapper around git: the ONLY new capability it adds is that a checkpoint
is created exclusively from a verified-green state (it asks mcp-verify first),
so revert_to_last_green() always lands on a known-good commit.

This process shares no mutable state with any other component. If it dies,
Hermes reports its tools unavailable and the agent degrades gracefully — it can
still do all its work, just without checkpoint/revert. It never takes Hermes down.
"""

from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import checkpoint_core

PORT = int(os.environ.get("MCP_CHECKPOINT_PORT", "9106"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-checkpoint",
    instructions=(
        "Verified-green git checkpointing for long-horizon work. After a subtask "
        "goes green, call checkpoint('<subtask label>') to commit it. If a later "
        "subtask drifts or you get stuck, call revert_to_last_green() to return the "
        "tree to the last known-good state. A checkpoint is REFUSED unless mcp-verify "
        "is green — so 'last green' always means green."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _threaded(fn):
    """Run a sync @mcp.tool() body on a worker thread so it never blocks the event
    loop. FastMCP (1.27) calls sync tool handlers directly in the single event-loop
    thread, so any long tool (running tests, indexing a repo, an LLM/cloud call,
    fetching+distilling a page) stalls EVERY other request — including GET /health,
    which is what made a live server show DOWN while it was actively serving the
    agent. asyncio.to_thread offloads the body so /health and concurrent calls stay
    responsive; functools.wraps preserves the typed signature for the schema, and
    the body runs in a thread with no running loop (so MCP-to-MCP asyncio.run works).
    """
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-checkpoint", "port": PORT})


@mcp.tool()
@_threaded
def checkpoint(label: str, verify: bool = True, repo_path: str | None = None, init: bool = False) -> dict:
    """Create a verified-green checkpoint commit of the project working tree.

    Args:
        label: Short human-readable subtask label (becomes the commit message).
        verify: If True (default), call mcp-verify FIRST and REFUSE to commit if
            it is RED — a checkpoint must represent a green state. If mcp-verify
            is unreachable, degrades to an UNVERIFIED commit with a loud warning.
        repo_path: Project repo to operate on (default: current working dir).
            Never operates on $HOME or /.
        init: If True, `git init` the directory when it isn't a repo yet.

    Returns the commit SHA + label. Idempotent: a no-op when nothing changed
    (returns the last checkpoint SHA without a new commit).
    """
    return checkpoint_core.checkpoint(label, verify, repo_path, init)


@mcp.tool()
@_threaded
def revert_to_last_green(repo_path: str | None = None) -> dict:
    """Recover from a stuck/drifted state: stash any dirty tree (nothing is
    lost), then `git reset --hard` to the last [hermes-max checkpoint] commit.

    This is the stuck-reset recovery primitive — it puts the tree in a known-good
    state. Returns the SHA reverted to and whether anything was stashed.
    """
    return checkpoint_core.revert_to_last_green(repo_path)


@mcp.tool()
@_threaded
def list_checkpoints(n: int = 10, repo_path: str | None = None) -> dict:
    """List the n most recent verified-green checkpoints (SHA, label, time)."""
    return checkpoint_core.list_checkpoints(n, repo_path)


@mcp.tool()
@_threaded
def checkpoint_status(repo_path: str | None = None) -> dict:
    """Report current branch, clean/dirty, the last green checkpoint SHA, and
    how many commits the tree is ahead of it."""
    return checkpoint_core.checkpoint_status(repo_path)


@mcp.tool()
@_threaded
def snapshot_state(task_id: str, plan: str = "", notes: str = "",
                   repo_path: str | None = None) -> dict:
    """Snapshot the agent's REASONING context (PLAN.md text + decision notes)
    alongside the git checkpoint, so a later revert can restore the plan — not
    just the tree. If plan is empty, the repo's PLAN.md is captured. Pair this
    with checkpoint() after a green subtask so a stuck-reset is lossless."""
    return checkpoint_core.snapshot_state(task_id, plan, notes, repo_path)


@mcp.tool()
@_threaded
def restore_state(task_id: str, repo_path: str | None = None, write_plan: bool = True) -> dict:
    """Restore the snapshotted reasoning context (plan + notes) for a task after
    a revert/reset, so the agent re-grounds on the real PLAN instead of a lossy
    summary. With write_plan=True, PLAN.md is rewritten to match the snapshot."""
    return checkpoint_core.restore_state(task_id, repo_path, write_plan)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
