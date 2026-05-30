#!/usr/bin/env python3
"""Standalone smoke test for mcp-checkpoint.

It boots a throwaway mcp-verify (the sibling server in this repo) so it can
exercise the real verify boundary — the ONE thing that makes a checkpoint more
than a bare `git commit`. Everything runs on throwaway ports in a temp git repo.

Part A (logic, against a real verify server):
  * checkpoint(verify=True) on a GREEN tree commits.
  * a breaking change makes the tree RED; checkpoint(verify=True) REFUSES.
  * revert_to_last_green() restores the tree to exactly the green checkpoint.
  * checkpoint(verify=True) with verify UNREACHABLE degrades to an unverified
    commit (graceful degradation, not a crash).
Part B (server): the process boots, /health responds, and the four tools are
  reachable over the real MCP streamable-http transport.

Exits non-zero on any failure so scripts/smoke-test.sh can gate on it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
VERIFY_DIR = REPO_ROOT / "mcp-verify"
TEST_PORT = int(os.environ.get("SMOKE_PORT", "19106"))
VERIFY_PORT = int(os.environ.get("SMOKE_VERIFY_PORT", "19101"))

GOOD_MODULE = "def add(a, b):\n    return a + b\n"
GOOD_TEST = "from mathy import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
BROKEN_MODULE = "def add(a, b)\n    return a + b\n"  # missing colon -> lint/type/test all red


def _ok(msg: str) -> None:
    print(f"  ok: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _wait_health(port: int, name: str, timeout: float = 40.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    _ok(f"{name} /health up on :{port}")
                    return
        except Exception as e:  # noqa: BLE001 — polling
            last = str(e)
        time.sleep(0.4)
    _fail(f"{name} health never came up on :{port} ({last})")


def _verify_python() -> str:
    """The mcp-verify venv python (has ruff/mypy/pytest). Build it if absent so
    a bare `python smoke_test.py` run is still self-contained."""
    py = VERIFY_DIR / ".venv" / "bin" / "python"
    if py.exists():
        return str(py)
    print("  [setup] mcp-verify venv missing — creating it for the integration boundary")
    subprocess.run([sys.executable, "-m", "venv", str(VERIFY_DIR / ".venv")], check=True)
    subprocess.run(
        [str(py), "-m", "pip", "install", "-q", "-r", str(VERIFY_DIR / "requirements.txt")],
        check=True,
    )
    return str(py)


def _start_verify() -> subprocess.Popen:
    env = dict(os.environ, MCP_VERIFY_PORT=str(VERIFY_PORT), MCP_BIND_HOST="127.0.0.1")
    proc = subprocess.Popen(
        [_verify_python(), str(VERIFY_DIR / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _wait_health(VERIFY_PORT, "mcp-verify")
    return proc


def _make_repo(tmp: Path, module_src: str) -> None:
    (tmp / "mathy.py").write_text(module_src)
    (tmp / "test_mathy.py").write_text(GOOD_TEST)


def part_a() -> None:
    print("[A] core logic against a real verify server (throwaway repo)")
    import tempfile

    # Point checkpoint_core at our throwaway verify server BEFORE importing it.
    os.environ["MCP_VERIFY_PORT"] = str(VERIFY_PORT)
    os.environ["MCP_BIND_HOST"] = "127.0.0.1"
    os.environ["CHECKPOINT_STATE_DIR"] = tempfile.mkdtemp(prefix="checkpoint-state-")
    import checkpoint_core

    verify_proc = _start_verify()
    tmpdir = tempfile.mkdtemp(prefix="checkpoint-smoke-")
    try:
        _git(["init"], tmpdir)
        _make_repo(Path(tmpdir), GOOD_MODULE)

        # 1. green tree -> checkpoint commits.
        r = checkpoint_core.checkpoint("initial green", verify=True, repo_path=tmpdir)
        if not (r.get("ok") and r.get("checkpointed") and r.get("verified")):
            _fail(f"checkpoint on green should commit+verify: {r}")
        green_sha = r["sha"]
        _ok(f"green checkpoint committed verified: {green_sha[:12]}")

        # 1b. FIX 3: caches/build artifacts must be IGNORED, never checkpointed.
        cache = Path(tmpdir) / "__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "x.pyc").write_text("junk-bytecode")
        checkpoint_core.checkpoint("after pyc", verify=True, repo_path=tmpdir)
        tracked = _git(["ls-files"], tmpdir).stdout.split()
        if any(t.endswith(".pyc") or t.startswith("__pycache__/") for t in tracked):
            _fail(f"FIX 3 broken: __pycache__/.pyc leaked into checkpoint: {tracked}")
        if ".gitignore" not in tracked:
            _fail(f"FIX 3 broken: .gitignore was not created/tracked: {tracked}")
        _ok("FIX 3: __pycache__/x.pyc ignored (not in git ls-files); .gitignore present")

        # 2. breaking change -> verify RED -> checkpoint REFUSES.
        (Path(tmpdir) / "mathy.py").write_text(BROKEN_MODULE)
        r = checkpoint_core.checkpoint("broken change", verify=True, repo_path=tmpdir)
        if r.get("ok") or r.get("checkpointed"):
            _fail(f"checkpoint on RED should refuse: {r}")
        if "verify" not in r:
            _fail(f"refusal should carry verify diagnostics: {r}")
        _ok(f"checkpoint refused on red: {r.get('reason')}")

        # confirm the broken commit was NOT created.
        log = _git(["log", "--format=%H"], tmpdir).stdout.split()
        if len(log) != 1:
            _fail(f"a red commit leaked into history: {log}")
        _ok("no red commit in history (invariant held)")

        # 3. revert_to_last_green restores the green tree exactly.
        r = checkpoint_core.revert_to_last_green(repo_path=tmpdir)
        if not (r.get("ok") and r.get("reverted_to") == green_sha):
            _fail(f"revert should land on the green sha: {r}")
        restored = (Path(tmpdir) / "mathy.py").read_text()
        if restored != GOOD_MODULE:
            _fail(f"revert did not restore the green file content:\n{restored!r}")
        _ok("revert_to_last_green restored the exact green tree")

        # 4. status sanity
        st = checkpoint_core.checkpoint_status(repo_path=tmpdir)
        if not (st.get("ok") and st.get("last_green_sha") == green_sha and st.get("dirty") is False):
            _fail(f"status after revert unexpected: {st}")
        _ok(f"status clean at last green: {st['last_green_label']}")

        # 6. agent-state snapshot/restore round-trip (Stage 0.5).
        plan_text = "# PLAN\n- [x] add()\n- [ ] subtract()\n"
        (Path(tmpdir) / "PLAN.md").write_text(plan_text)
        snap = checkpoint_core.snapshot_state("smoke-task", notes="chose SQLite over JSON",
                                              repo_path=tmpdir)
        if not (snap.get("ok") and snap.get("plan_chars") == len(plan_text)):
            _fail(f"snapshot_state did not capture PLAN.md: {snap}")
        # clobber the plan to prove restore brings it back
        (Path(tmpdir) / "PLAN.md").write_text("CORRUPTED")
        rs = checkpoint_core.restore_state("smoke-task", repo_path=tmpdir)
        if not (rs.get("ok") and rs.get("plan") == plan_text and rs.get("notes") == "chose SQLite over JSON"):
            _fail(f"restore_state did not round-trip plan+notes: {rs}")
        if (Path(tmpdir) / "PLAN.md").read_text() != plan_text or not rs.get("plan_restored_to_file"):
            _fail(f"restore_state did not rewrite PLAN.md: {rs}")
        _ok("snapshot_state/restore_state round-trip (plan + notes restored, PLAN.md rewritten)")
        rs_missing = checkpoint_core.restore_state("no-such-task")
        if rs_missing.get("ok"):
            _fail(f"restore_state on missing task should error cleanly: {rs_missing}")
        _ok("restore_state on missing task returns a clean error (no crash)")
    finally:
        verify_proc.terminate()
        try:
            verify_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            verify_proc.kill()

    # 5. graceful degradation: verify unreachable -> unverified commit + warning.
    os.environ["MCP_VERIFY_PORT"] = "1"  # nothing listens on :1
    import importlib

    importlib.reload(checkpoint_core)
    tmp2 = tempfile.mkdtemp(prefix="checkpoint-degrade-")
    _git(["init"], tmp2)
    _make_repo(Path(tmp2), GOOD_MODULE)
    r = checkpoint_core.checkpoint("degraded", verify=True, repo_path=tmp2)
    if not (r.get("ok") and r.get("checkpointed")):
        _fail(f"degraded checkpoint should still commit: {r}")
    if r.get("verified") or not r.get("warnings"):
        _fail(f"degraded checkpoint must be unverified WITH a loud warning: {r}")
    _ok(f"graceful degradation: {r['warnings'][0][:70]}...")
    # restore for any later use
    os.environ["MCP_VERIFY_PORT"] = str(VERIFY_PORT)
    importlib.reload(checkpoint_core)


async def _mcp_check(port: int) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://127.0.0.1:{port}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            expected = {"checkpoint", "revert_to_last_green", "list_checkpoints", "checkpoint_status",
                        "snapshot_state", "restore_state"}
            if not expected.issubset(names):
                _fail(f"missing tools; want {expected}, got {names}")
            _ok(f"tools advertised: {sorted(names)}")

            # checkpoint_status on a non-repo path returns a structured refusal, not a crash.
            res = await session.call_tool("checkpoint_status", {"repo_path": "/tmp"})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "ok" not in data:
                data = data["result"]
            if data.get("ok") is not False:
                _fail(f"status on a non-repo should be a clean refusal: {data}")
            _ok("checkpoint_status over MCP returns a structured result")


def part_b() -> None:
    print(f"[B] server over MCP streamable-http (:{TEST_PORT})")
    env = dict(os.environ, MCP_CHECKPOINT_PORT=str(TEST_PORT), MCP_BIND_HOST="127.0.0.1")
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_health(TEST_PORT, "mcp-checkpoint")
        asyncio.run(_mcp_check(TEST_PORT))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    part_a()
    part_b()
    print("mcp-checkpoint smoke test PASSED")
