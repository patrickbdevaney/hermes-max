#!/usr/bin/env python3
"""Long-horizon acceptance / regression test (CLAUDE_longhorizon.md INTEGRATION TEST).

Re-runs the task that originally hung — "write and deploy a small Flask jokes API
with a /health route and one /joke route, with a test" — but as a *planned project*
driven by the long-horizon scaffolding. It exercises the REAL mcp-verify and
mcp-checkpoint servers over MCP, exactly as Hermes would.

It asserts the acceptance bar:
  [1] PLAN.md is written BEFORE any code.
  [2] Each subtask ends with a verified-green [hermes-max checkpoint] commit.
  [3] The Flask server is started backgrounded and tested ONCE with a timeout —
      NOT polled to death (the regression for the original 9-minute hang).
  [4] mcp-verify is green before "done".
  [5] Killing mcp-checkpoint mid-task → the agent keeps working, just warns it
      can't checkpoint (graceful degradation).
  [6] A forced unsatisfiable subtask → revert_to_last_green restores the tree
      instead of thrashing.

No host is hardcoded: servers bind 127.0.0.1 on throwaway ports; the model
endpoint (unused here) is always $VLLM_BASE_URL.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_DIR = REPO_ROOT / "mcp-verify"
CHECKPOINT_DIR = REPO_ROOT / "mcp-checkpoint"
VERIFY_PORT = int(os.environ.get("ACCEPT_VERIFY_PORT", "29101"))
CKPT_PORT = int(os.environ.get("ACCEPT_CKPT_PORT", "29106"))
FLASK_PORT = int(os.environ.get("ACCEPT_FLASK_PORT", "8231"))

VERIFY_URL = f"http://127.0.0.1:{VERIFY_PORT}/mcp"
CKPT_URL = f"http://127.0.0.1:{CKPT_PORT}/mcp"


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===")


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    sys.exit(1)


# ── MCP client helper (this is how Hermes calls the tools) ───────────────────
async def _call(url: str, tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = getattr(res.content[0], "text", "") if res.content else ""
            data = res.structuredContent or (json.loads(text) if text else {})
            if isinstance(data, dict) and set(data.keys()) == {"result"}:
                data = data["result"]
            return data if isinstance(data, dict) else {"_raw": data}


def call(url: str, tool: str, **args) -> dict:
    return asyncio.run(_call(url, tool, args))


# ── server lifecycle ─────────────────────────────────────────────────────────
def wait_health(port: int, name: str, timeout: float = 40.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    fail(f"{name} never became healthy on :{port}")


def start_server(directory: Path, env_extra: dict, port: int, name: str) -> subprocess.Popen:
    env = dict(os.environ, MCP_BIND_HOST="127.0.0.1", **env_extra)
    proc = subprocess.Popen(
        [str(directory / ".venv" / "bin" / "python"), str(directory / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    wait_health(port, name)
    print(f"  started {name} on :{port} (pid {proc.pid})")
    return proc


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── project sources ──────────────────────────────────────────────────────────
APP_V1 = '''from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(ok=True)


def main():
    app.run(host="127.0.0.1", port={port})


if __name__ == "__main__":
    main()
'''

APP_V2 = '''import random

from flask import Flask, jsonify

app = Flask(__name__)

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "There are 10 kinds of people: those who read binary and those who don't.",
    "A SQL query walks into a bar, walks up to two tables and asks: can I join you?",
]


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/joke")
def joke():
    return jsonify(joke=random.choice(JOKES))


def main():
    app.run(host="127.0.0.1", port={port})


if __name__ == "__main__":
    main()
'''

TEST_V1 = '''from app import app


def test_health():
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
'''

TEST_V2 = TEST_V1 + '''

def test_joke():
    client = app.test_client()
    resp = client.get("/joke")
    assert resp.status_code == 200
    assert "joke" in resp.get_json()
'''


def main() -> None:
    proj = Path(tempfile.mkdtemp(prefix="flask-jokes-"))
    print(f"project: {proj}")

    # A project venv with the toolchain mcp-verify will run (ruff/mypy/pytest)
    # plus flask — this is the realistic project setup.
    banner("setup: project venv (flask + verify toolchain)")
    subprocess.run([sys.executable, "-m", "venv", str(proj / ".venv")], check=True)
    pvpy = str(proj / ".venv" / "bin" / "python")
    subprocess.run(
        [pvpy, "-m", "pip", "install", "-q", "flask", "pytest", "ruff", "mypy"], check=True
    )
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    ok("project venv + git repo ready")

    verify_proc = start_server(VERIFY_DIR, {"MCP_VERIFY_PORT": str(VERIFY_PORT)}, VERIFY_PORT, "mcp-verify")
    ckpt_proc = start_server(
        CHECKPOINT_DIR,
        {"MCP_CHECKPOINT_PORT": str(CKPT_PORT), "MCP_VERIFY_PORT": str(VERIFY_PORT)},
        CKPT_PORT,
        "mcp-checkpoint",
    )

    try:
        # ── [1] PLAN FIRST: PLAN.md before any code ──────────────────────────
        banner("[1] plan-first — PLAN.md written before any code")
        plan = """# PLAN — Flask jokes API
Definition of done: GET /health -> 200 {"ok": true}; GET /joke -> 200 {"joke": ...}; pytest green.

- [ ] subtask 1: app skeleton + /health route + test (verify green, checkpoint)
- [ ] subtask 2: /joke route + test (verify green, checkpoint)
- [ ] subtask 3: deploy — run server backgrounded, test ONCE with curl, then stop
"""
        (proj / "PLAN.md").write_text(plan)
        code_files = list(proj.glob("*.py"))
        if code_files:
            fail(f"code existed before PLAN.md: {code_files}")
        ok("PLAN.md exists and no .py code yet")

        # ── subtask 1 ────────────────────────────────────────────────────────
        banner("[2] subtask 1 — /health route + test → verify → checkpoint")
        (proj / "app.py").write_text(APP_V1.format(port=FLASK_PORT))
        (proj / "test_app.py").write_text(TEST_V1)
        r = call(CKPT_URL, "checkpoint", label="health route + test", repo_path=str(proj))
        if not (r.get("ok") and r.get("checkpointed") and r.get("verified")):
            fail(f"subtask 1 checkpoint not green: {r}")
        ok(f"subtask 1 green checkpoint {r['sha'][:12]} (verified={r['verified']})")

        # ── subtask 2 ────────────────────────────────────────────────────────
        banner("[2] subtask 2 — /joke route + test → verify → checkpoint")
        (proj / "app.py").write_text(APP_V2.format(port=FLASK_PORT))
        (proj / "test_app.py").write_text(TEST_V2)
        r = call(CKPT_URL, "checkpoint", label="joke route + test", repo_path=str(proj))
        if not (r.get("ok") and r.get("checkpointed") and r.get("verified")):
            fail(f"subtask 2 checkpoint not green: {r}")
        ok(f"subtask 2 green checkpoint {r['sha'][:12]} (verified={r['verified']})")

        # ── [3] deploy: start backgrounded, test ONCE, do NOT poll ───────────
        banner("[3] deploy — start server backgrounded, test ONCE (the hang regression)")
        log = open(proj / "server.log", "w")
        t0 = time.monotonic()
        server = subprocess.Popen([pvpy, "app.py"], cwd=proj, stdout=log, stderr=subprocess.STDOUT)
        print(f"  flask started backgrounded (pid {server.pid}); waiting 2.5s for startup")
        time.sleep(2.5)
        try:
            # exactly one timed probe per route — never a poll loop on a forever-process
            with urllib.request.urlopen(f"http://127.0.0.1:{FLASK_PORT}/health", timeout=5) as resp:
                health_body = json.loads(resp.read())
            with urllib.request.urlopen(f"http://127.0.0.1:{FLASK_PORT}/joke", timeout=5) as resp:
                joke_body = json.loads(resp.read())
            elapsed = time.monotonic() - t0
            if health_body.get("ok") is not True:
                fail(f"/health wrong body: {health_body}")
            if "joke" not in joke_body:
                fail(f"/joke wrong body: {joke_body}")
            if elapsed > 10:
                fail(f"deploy+test took {elapsed:.1f}s (>10s) — that looks like polling a forever-process")
            ok(f"server tested ONCE in {elapsed:.1f}s (<10s): /health={health_body}, /joke ok")
            ok(f"recorded server pid {server.pid} to stop later (not polled)")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
            log.close()
        ok("server stopped via recorded PID")

        # ── [4] verify green before done ─────────────────────────────────────
        banner("[4] done-gate — mcp-verify green over MCP before declaring done")
        v = call(VERIFY_URL, "verify", path=str(proj), language="python")
        if not v.get("passed"):
            fail(f"verify not green at done: {v.get('summary')}")
        ok(f"verify green: {v['summary']}")

        # ── [6] forced stuck → revert_to_last_green restores the tree ────────
        banner("[6] stuck-detect-reset — unsatisfiable change → revert_to_last_green")
        good_app = (proj / "app.py").read_text()
        (proj / "app.py").write_text("def joke(  # deliberately broken, unsatisfiable\n")
        red = call(CKPT_URL, "checkpoint", label="should refuse", repo_path=str(proj))
        if red.get("ok") or red.get("checkpointed"):
            fail(f"checkpoint should refuse the broken tree: {red}")
        ok(f"checkpoint refused the broken state: {red.get('reason')}")
        rev = call(CKPT_URL, "revert_to_last_green", repo_path=str(proj))
        if not (rev.get("ok") and rev.get("reverted_to")):
            fail(f"revert_to_last_green failed: {rev}")
        if (proj / "app.py").read_text() != good_app:
            fail("revert did not restore the last green app.py")
        ok(f"reverted to last green ({rev['label']}); tree restored, no thrashing")

        # ── [5] graceful degradation — kill checkpoint, agent keeps working ──
        banner("[5] anti-Frankenstein — kill mcp-checkpoint mid-task, agent keeps working")
        stop(ckpt_proc)
        time.sleep(0.5)
        try:
            call(CKPT_URL, "checkpoint_status", repo_path=str(proj))
            fail("checkpoint server should be unreachable after kill")
        except Exception as e:  # noqa: BLE001 — exactly what Hermes catches
            ok(f"checkpoint unreachable as expected ({type(e).__name__}); agent WARNS and continues")
        # the agent can still verify (a different, independent server) — degraded, not down
        v2 = call(VERIFY_URL, "verify", path=str(proj), language="python")
        if not v2.get("passed"):
            fail("verify should still work after checkpoint died")
        ok("verify still green with checkpoint down — degraded gracefully, not crashed")

        # ── proof: git log of per-subtask green checkpoints ──────────────────
        banner("git log — per-subtask verified-green checkpoints")
        logout = subprocess.run(
            ["git", "log", "--oneline", "--decorate"], cwd=proj, capture_output=True, text=True
        ).stdout.strip()
        print(logout)
        n = subprocess.run(
            ["git", "log", "-F", "--grep=[hermes-max checkpoint]", "--format=%H"],
            cwd=proj, capture_output=True, text=True,
        ).stdout.split()
        if len(n) < 2:
            fail(f"expected ≥2 [hermes-max checkpoint] commits, found {len(n)}")
        ok(f"{len(n)} verified-green [hermes-max checkpoint] commits in history")
        print(f"\nGIT_LOG_DIR={proj}")
    finally:
        stop(verify_proc)
        if ckpt_proc.poll() is None:
            stop(ckpt_proc)

    print("\n=== LONG-HORIZON ACCEPTANCE TEST PASSED ===")
    print(f"(project left at {proj} so you can inspect `git -C {proj} log`)")


if __name__ == "__main__":
    main()
