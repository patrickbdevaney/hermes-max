"""Thin git-commit checkpointing core for mcp-checkpoint.

A *checkpoint* is a git commit that is ONLY ever created from a verified-green
working tree. The invariant — "a checkpoint always represents a green state" —
is the whole point: `revert_to_last_green()` must always land the tree on a
known-good commit.

Discipline:
  * No model calls, no randomness. Verification is delegated to the running
    mcp-verify server over its MCP boundary; if mcp-verify is unreachable,
    checkpoint(verify=True) degrades to an UNVERIFIED commit with a loud
    warning rather than crashing (graceful degradation).
  * Operates on the project repo at the caller's cwd (or an explicit
    repo_path), NEVER on the hermes-max repo or on ~ / / itself.
  * Never force-pushes, never touches remotes. Local working-tree commits only.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import subprocess
from typing import Any

CHECKPOINT_MARKER = "[hermes-max checkpoint]"

VERIFY_PORT = int(os.environ.get("MCP_VERIFY_PORT", "9101"))
VERIFY_HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
# A connection refusal returns fast; a real verify run is bounded by the verify
# server's own per-stage timeout. This cap is the outer "never hang" backstop.
VERIFY_CALL_TIMEOUT = float(os.environ.get("CHECKPOINT_VERIFY_TIMEOUT", "600"))
GIT_TIMEOUT = int(os.environ.get("CHECKPOINT_GIT_TIMEOUT", "120"))


# ── git plumbing ─────────────────────────────────────────────────────────────
def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT
    )


def _resolve_repo(repo_path: str | None) -> str:
    return os.path.abspath(os.path.expanduser(repo_path or os.getcwd()))


def _is_protected(path: str) -> bool:
    """Refuse to operate on $HOME or the filesystem root."""
    real = os.path.realpath(path)
    return real == os.path.realpath("/") or real == os.path.realpath(os.path.expanduser("~"))


def _is_git_repo(path: str) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], path)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _head_sha(repo: str) -> str | None:
    r = _git(["rev-parse", "HEAD"], repo)
    return r.stdout.strip() if r.returncode == 0 else None


def _last_green_sha(repo: str) -> str:
    """SHA of the most recent [hermes-max checkpoint] commit, or '' if none."""
    r = _git(["log", "-F", f"--grep={CHECKPOINT_MARKER}", "-n", "1", "--format=%H"], repo)
    return r.stdout.strip() if r.returncode == 0 else ""


def _label_of(repo: str, sha: str) -> str:
    r = _git(["log", "-n", "1", "--format=%s", sha], repo)
    subj = r.stdout.strip() if r.returncode == 0 else ""
    return subj.replace(CHECKPOINT_MARKER, "").strip()


def _commit(repo: str, msg: str) -> subprocess.CompletedProcess:
    """Commit, supplying a fallback identity only when the repo has none set
    (so a real project's configured author is never overridden)."""
    pre: list[str] = []
    if not _git(["config", "user.name"], repo).stdout.strip():
        pre += ["-c", "user.name=hermes-max"]
    if not _git(["config", "user.email"], repo).stdout.strip():
        pre += ["-c", "user.email=hermes-max@localhost"]
    return _git([*pre, "commit", "--no-verify", "-m", msg], repo)


def _guard(repo_path: str | None, init: bool) -> tuple[str | None, dict | None]:
    """Resolve + validate the repo. Returns (repo, error_dict). On success
    error_dict is None; on failure repo is None."""
    repo = _resolve_repo(repo_path)
    if not os.path.isdir(repo):
        return None, {"ok": False, "error": f"repo_path is not a directory: {repo}"}
    if _is_protected(repo):
        return None, {"ok": False, "error": f"refusing to operate on protected path: {repo}"}
    if not _is_git_repo(repo):
        if init:
            r = _git(["init"], repo)
            if r.returncode != 0:
                return None, {"ok": False, "error": f"git init failed: {r.stderr.strip()}"}
        else:
            return None, {
                "ok": False,
                "error": f"not a git repository: {repo} (pass init=True to initialize)",
            }
    return repo, None


# ── verify boundary (graceful-degrade if mcp-verify is unreachable) ──────────
async def _call_verify(repo: str) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://{VERIFY_HOST}:{VERIFY_PORT}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("verify", {"path": repo})
            text = getattr(res.content[0], "text", "") if res.content else ""
            data = res.structuredContent or (json.loads(text) if text else {})
            if isinstance(data, dict) and "result" in data and "passed" not in data:
                data = data["result"]
            return data if isinstance(data, dict) else {}


def _verify_repo(repo: str) -> dict[str, Any]:
    """Call the mcp-verify server. reachable=False on any connection error.

    Runs the async MCP call on a dedicated thread with its own event loop, so it
    works whether or not the caller (e.g. the FastMCP server) is already inside a
    running event loop.
    """

    def _runner() -> dict[str, Any]:
        return asyncio.run(asyncio.wait_for(_call_verify(repo), timeout=VERIFY_CALL_TIMEOUT))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            data = ex.submit(_runner).result(timeout=VERIFY_CALL_TIMEOUT + 30)
    except Exception as e:  # noqa: BLE001 — any failure to reach verify = degrade
        return {"reachable": False, "passed": False, "result": None, "error": f"{type(e).__name__}: {e}"}
    return {"reachable": True, "passed": bool(data.get("passed")), "result": data, "error": None}


# A sensible default .gitignore, written into a project repo at checkpoint time
# only when it has none — so `git add -A` never sweeps caches, virtualenvs,
# secrets, or build artifacts into a "last green" checkpoint. An existing
# .gitignore is always respected (never overwritten).
_DEFAULT_GITIGNORE = """__pycache__/
*.pyc
.venv/
venv/
node_modules/
.env
.env.*
*.log
.DS_Store
dist/
build/
*.egg-info/
"""


def _ensure_gitignore(repo: str) -> bool:
    """Write the default .gitignore IF the repo has none. Returns True if it
    created one; False if one already existed (respected) or on any error."""
    path = os.path.join(repo, ".gitignore")
    if os.path.exists(path):
        return False
    try:
        with open(path, "w") as f:
            f.write(_DEFAULT_GITIGNORE)
        return True
    except OSError:
        return False


# ── public tools ─────────────────────────────────────────────────────────────
def checkpoint(label: str, verify: bool = True, repo_path: str | None = None, init: bool = False) -> dict:
    """Create a verified-green checkpoint commit. Refuses on RED."""
    repo, err = _guard(repo_path, init)
    if err:
        return err
    assert repo is not None  # _guard sets repo whenever err is None

    warnings: list[str] = []
    verified = False
    if verify:
        v = _verify_repo(repo)
        if v["reachable"]:
            if not v["passed"]:
                return {
                    "ok": False,
                    "checkpointed": False,
                    "reason": "mcp-verify is RED — refusing to checkpoint a non-green state",
                    "verify": v["result"],
                }
            verified = True
        else:
            warnings.append(
                "mcp-verify UNREACHABLE — checkpoint created WITHOUT verification "
                f"(verify degraded to False). {v['error']}"
            )

    # FIX 3: ensure caches/secrets/build artifacts are filtered before add -A.
    created_gitignore = _ensure_gitignore(repo)
    add = _git(["add", "-A"], repo)
    if add.returncode != 0:
        return {"ok": False, "error": f"git add failed: {add.stderr.strip()}"}

    # Nothing staged AND working tree clean → idempotent no-op.
    if _git(["diff", "--cached", "--quiet"], repo).returncode == 0:
        sha = _last_green_sha(repo) or _head_sha(repo)
        return {
            "ok": True,
            "checkpointed": False,
            "no_op": True,
            "sha": sha,
            "label": label,
            "warnings": warnings,
            "message": "nothing changed; returning last checkpoint SHA",
        }

    commit = _commit(repo, f"{CHECKPOINT_MARKER} {label}")
    if commit.returncode != 0:
        return {"ok": False, "error": f"git commit failed: {commit.stderr.strip() or commit.stdout.strip()}"}

    sha = _head_sha(repo)
    # Tag the commit ref (best-effort; the commit marker is the source of truth).
    if sha:
        short = sha[:12]
        _git(["tag", "-f", f"hermes-green-{short}", sha], repo)
        _git(["tag", "-f", "hermes-last-green", sha], repo)
    return {
        "ok": True,
        "checkpointed": True,
        "sha": sha,
        "label": label,
        "verified": verified,
        "warnings": warnings,
    }


def revert_to_last_green(repo_path: str | None = None) -> dict:
    """Stash any dirty tree, then hard-reset to the last green checkpoint."""
    repo, err = _guard(repo_path, init=False)
    if err:
        return err
    assert repo is not None  # _guard sets repo whenever err is None

    sha = _last_green_sha(repo)
    if not sha:
        return {"ok": False, "reason": f"no {CHECKPOINT_MARKER} commit found to revert to"}

    stash = _git(
        ["stash", "push", "-u", "-m", "hermes-max revert_to_last_green safety stash"], repo
    )
    stashed = stash.returncode == 0 and "No local changes" not in (stash.stdout + stash.stderr)

    reset = _git(["reset", "--hard", sha], repo)
    if reset.returncode != 0:
        return {"ok": False, "reason": "git reset --hard failed", "stderr": reset.stderr.strip()}

    return {
        "ok": True,
        "reverted_to": sha,
        "label": _label_of(repo, sha),
        "stashed": stashed,
        "stash_ref": "stash@{0}" if stashed else None,
        "note": "Working tree is now at the last verified-green checkpoint."
        + (" Prior dirty changes were stashed (recover with `git stash pop`)." if stashed else ""),
    }


def list_checkpoints(n: int = 10, repo_path: str | None = None) -> dict:
    """List recent [hermes-max checkpoint] commits (SHA, label, time)."""
    repo, err = _guard(repo_path, init=False)
    if err:
        return err
    assert repo is not None  # _guard sets repo whenever err is None

    r = _git(
        ["log", "-F", f"--grep={CHECKPOINT_MARKER}", "-n", str(n), "--format=%H%x09%cI%x09%s"], repo
    )
    checkpoints = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        sha, when, subj = parts
        checkpoints.append({"sha": sha, "time": when, "label": subj.replace(CHECKPOINT_MARKER, "").strip()})
    return {"ok": True, "count": len(checkpoints), "checkpoints": checkpoints}


def checkpoint_status(repo_path: str | None = None) -> dict:
    """Branch, clean/dirty, last-green SHA, and commits-ahead of last green."""
    repo, err = _guard(repo_path, init=False)
    if err:
        return err
    assert repo is not None  # _guard sets repo whenever err is None

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip() or "(unborn)"
    porcelain = _git(["status", "--porcelain"], repo).stdout.strip()
    last_green = _last_green_sha(repo)
    ahead = 0
    if last_green:
        cnt = _git(["rev-list", "--count", f"{last_green}..HEAD"], repo)
        ahead = int(cnt.stdout.strip() or "0") if cnt.returncode == 0 else 0
    return {
        "ok": True,
        "repo": repo,
        "branch": branch,
        "dirty": bool(porcelain),
        "last_green_sha": last_green or None,
        "last_green_label": _label_of(repo, last_green) if last_green else None,
        "commits_ahead_of_last_green": ahead,
    }
