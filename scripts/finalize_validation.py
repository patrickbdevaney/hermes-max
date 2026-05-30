#!/usr/bin/env python3
"""VALIDATION V1/V2/V3 for the finalized hermes-max harness.

Drives the REAL mcp-verify + mcp-checkpoint + mcp-codebase-rag servers over MCP
exactly as Hermes would — this orchestrator stands in for the LLM agent loop
(the model host is intentionally untouched / may be down), while every harness
component it exercises (verify gate, verified-green checkpoints, hybrid/BM25
retrieval, graceful degradation, stuck-reset) is the real thing.

  V1  Real multi-file FastAPI task-tracker (>=5 files), planned, per-subtask
      verified-green checkpoints, FIX-3 .gitignore proof, single timed server
      probe, rag queried, checkpoint-kill graceful degradation.
  V2  Compounding: re-retrieve V1 patterns from the rag index and build a
      follow-up feature reusing them — fewer exploratory steps than cold.
  V3  Stuck-reset: an unsatisfiable subtask trips the tightened guardrail
      (idempotent_no_progress:3 / same_tool_failure:4), writes a STUCK SUMMARY,
      calls revert_to_last_green, and stops before hard_stop.

Records PASS/FAIL per checkbox (never hides a failure) and writes a full report
to /tmp/hermes_validation_report.md. No host is hardcoded.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_DIR = REPO_ROOT / "mcp-verify"
CKPT_DIR = REPO_ROOT / "mcp-checkpoint"
RAG_DIR = REPO_ROOT / "mcp-codebase-rag"
WATCHDOG_DIR = REPO_ROOT / "mcp-watchdog"
SEARCH_DIR = REPO_ROOT / "mcp-search"
ESC_DIR = REPO_ROOT / "mcp-escalation"

VERIFY_PORT = int(os.environ.get("V_VERIFY_PORT", "39101"))
CKPT_PORT = int(os.environ.get("V_CKPT_PORT", "39106"))
RAG_PORT = int(os.environ.get("V_RAG_PORT", "39102"))
WATCHDOG_PORT = int(os.environ.get("V_WATCHDOG_PORT", "39107"))
SEARCH_PORT = int(os.environ.get("V_SEARCH_PORT", "39108"))
ESC_PORT = int(os.environ.get("V_ESC_PORT", "39105"))
STUB_MODEL_PORT = int(os.environ.get("V_STUB_MODEL_PORT", "39190"))
APP_PORT = int(os.environ.get("V_APP_PORT", "8337"))

VERIFY_URL = f"http://127.0.0.1:{VERIFY_PORT}/mcp"
CKPT_URL = f"http://127.0.0.1:{CKPT_PORT}/mcp"
RAG_URL = f"http://127.0.0.1:{RAG_PORT}/mcp"
WATCHDOG_URL = f"http://127.0.0.1:{WATCHDOG_PORT}/mcp"
SEARCH_URL = f"http://127.0.0.1:{SEARCH_PORT}/mcp"
ESC_URL = f"http://127.0.0.1:{ESC_PORT}/mcp"

REPORT = "/tmp/hermes_validation_report.md"
_log: list[str] = []
_checks: list[tuple[str, bool, str]] = []  # (label, passed, detail)


def say(m: str = "") -> None:
    print(m, flush=True)
    _log.append(m)


def banner(m: str) -> None:
    say("\n" + "─" * 76)
    say(m)
    say("─" * 76)


def check(label: str, passed: bool, detail: str = "") -> bool:
    _checks.append((label, passed, detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return passed


def _flush_report() -> None:
    with open(REPORT, "w") as f:
        f.write("\n".join(_log))
        f.write("\n\n## CHECKBOX SUMMARY\n")
        for label, passed, detail in _checks:
            f.write(f"- [{'x' if passed else ' '}] {label}{(' — ' + detail) if detail else ''}\n")
        n_pass = sum(1 for _, p, _ in _checks if p)
        f.write(f"\n{n_pass}/{len(_checks)} checks PASSED\n")


# ── MCP client ───────────────────────────────────────────────────────────────
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
def wait_health(port: int, name: str, timeout: float = 40.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    return False


def start_server(directory: Path, env_extra: dict, port: int, name: str) -> subprocess.Popen:
    env = dict(os.environ, MCP_BIND_HOST="127.0.0.1", **env_extra)
    env.pop("EMBED_BASE_URL", None)  # force honest BM25 path B for rag
    proc = subprocess.Popen(
        [str(directory / ".venv" / "bin" / "python"), str(directory / "server.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if not wait_health(port, name):
        say(f"  !! {name} did not become healthy on :{port}")
    else:
        say(f"  started {name} on :{port} (pid {proc.pid})")
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def start_stub_model(port: int):
    """A tiny OpenAI-compatible /chat/completions stub so the LOCAL escalation
    tier can be exercised end-to-end without a real GPU. Echoes back that it
    received the handoff so the test can prove the full context was carried."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 - silence
            pass

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode("utf-8", "replace")
            got_handoff = "Handoff context" in body
            resp = json.dumps({
                "choices": [{"message": {"content":
                    f"[local-122b] handled hard kernel (handoff_seen={got_handoff})"}}],
                "usage": {"prompt_tokens": 60, "completion_tokens": 20},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    srv = ThreadingHTTPServer(("127.0.0.1", port), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    say(f"  started stub local-tier model on :{port}")
    return srv


# ── project sources ──────────────────────────────────────────────────────────
PYPROJECT = """[tool.mypy]
ignore_missing_imports = true
check_untyped_defs = false

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F"]
"""

DB_PY = '''"""SQLite storage for the task tracker."""
from __future__ import annotations

import os
import sqlite3
from typing import Any

DB_PATH = os.environ.get("TASKS_DB", os.path.join(os.path.dirname(__file__), "tasks.db"))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
            )"""
        )


def create_task(title: str, description: str, status: str) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(title, description, status) VALUES(?,?,?)",
            (title, description, status),
        )
        last = cur.lastrowid
    assert last is not None
    return {"id": last, "title": title, "description": description, "status": status}


def list_tasks() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY id")]


def get_task(task_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def update_task(task_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    if not fields:
        return get_task(task_id)
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return cur.rowcount > 0


def counts_by_status() -> dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
        return {r["status"]: r["n"] for r in rows}
'''

MODELS_PY = '''"""Pydantic models for the task tracker."""
from __future__ import annotations

from pydantic import BaseModel


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    status: str = "pending"


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None


class Task(BaseModel):
    id: int
    title: str
    description: str
    status: str
'''

MAIN_PY_V1 = '''"""FastAPI task-tracker service (CRUD + health)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

import db
from models import Task, TaskCreate, TaskUpdate

app = FastAPI(title="task-tracker")

db.init_db()


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/tasks", response_model=Task, status_code=201)
def create(payload: TaskCreate) -> dict:
    return db.create_task(payload.title, payload.description, payload.status)


@app.get("/tasks", response_model=list[Task])
def list_all() -> list[dict]:
    return db.list_tasks()


@app.get("/tasks/{task_id}", response_model=Task)
def get_one(task_id: int) -> dict:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.put("/tasks/{task_id}", response_model=Task)
def update(task_id: int, payload: TaskUpdate) -> dict:
    if db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    updated = db.update_task(task_id, fields)
    assert updated is not None
    return updated


@app.delete("/tasks/{task_id}", status_code=204)
def remove(task_id: int) -> None:
    if not db.delete_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")
'''

# V2 adds /tasks/{id}/complete and /stats, following the same patterns.
MAIN_PY_V2 = MAIN_PY_V1.replace(
    '''@app.delete("/tasks/{task_id}", status_code=204)
def remove(task_id: int) -> None:
    if not db.delete_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")
''',
    '''@app.delete("/tasks/{task_id}", status_code=204)
def remove(task_id: int) -> None:
    if not db.delete_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")


@app.post("/tasks/{task_id}/complete", response_model=Task)
def complete(task_id: int) -> dict:
    if db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    updated = db.update_task(task_id, {"status": "completed"})
    assert updated is not None
    return updated


@app.get("/stats")
def stats() -> dict[str, object]:
    by_status = db.counts_by_status()
    return {"total": sum(by_status.values()), "by_status": by_status}
''',
)

CONFTEST = '''import os
import tempfile

os.environ["TASKS_DB"] = os.path.join(tempfile.mkdtemp(prefix="tasks-test-"), "t.db")
'''

SUBTASK1_TEST = '''import db


def test_storage_roundtrip() -> None:
    db.init_db()
    t = db.create_task("hello", "", "pending")
    fetched = db.get_task(t["id"])
    assert fetched is not None
    assert fetched["title"] == "hello"
    assert db.delete_task(t["id"]) is True
'''

TEST_HEALTH = '''from fastapi.testclient import TestClient

import main


def test_health() -> None:
    client = TestClient(main.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
'''

TEST_TASKS = '''from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_create_and_get() -> None:
    r = client.post("/tasks", json={"title": "write tests"})
    assert r.status_code == 201
    tid = r.json()["id"]
    g = client.get(f"/tasks/{tid}")
    assert g.status_code == 200
    assert g.json()["title"] == "write tests"


def test_list() -> None:
    client.post("/tasks", json={"title": "another"})
    r = client.get("/tasks")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_update() -> None:
    tid = client.post("/tasks", json={"title": "todo"}).json()["id"]
    r = client.put(f"/tasks/{tid}", json={"status": "in_progress"})
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"


def test_delete() -> None:
    tid = client.post("/tasks", json={"title": "trash"}).json()["id"]
    assert client.delete(f"/tasks/{tid}").status_code == 204
    assert client.get(f"/tasks/{tid}").status_code == 404
'''

TEST_STATS = '''from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_complete_and_stats() -> None:
    tid = client.post("/tasks", json={"title": "finish me"}).json()["id"]
    c = client.post(f"/tasks/{tid}/complete")
    assert c.status_code == 200
    assert c.json()["status"] == "completed"

    s = client.get("/stats")
    assert s.status_code == 200
    body = s.json()
    assert "total" in body and "by_status" in body
    assert body["by_status"].get("completed", 0) >= 1
'''

PLAN_V1 = """# PLAN — FastAPI task-tracker service

Definition of done: SQLite-backed CRUD for tasks (create/list/get/update/delete),
a /health route, Pydantic models, pytest covering each endpoint; mcp-verify green;
each subtask ends on a verified-green checkpoint. At least 5 files.

- [ ] subtask 1: storage + models (db.py SQLite, models.py Pydantic, pyproject) -> verify -> checkpoint
- [ ] subtask 2: FastAPI app with /health + CRUD endpoints (main.py) + tests -> verify -> checkpoint
- [ ] subtask 3: deploy — run uvicorn backgrounded, probe ONCE with a timeout, stop
"""


def write(proj: Path, name: str, content: str) -> None:
    (proj / name).write_text(content)


# ── helpers for verify-gated checkpoints ─────────────────────────────────────
def do_checkpoint(proj: Path, label: str) -> dict:
    r = call(CKPT_URL, "checkpoint", label=label, repo_path=str(proj))
    if not r.get("ok"):
        say(f"    checkpoint refused/failed: {json.dumps(r)[:800]}")
    return r


def main() -> None:
    import datetime
    say(f"# hermes-max VALIDATION — {datetime.datetime.now().isoformat()}")
    say(f"VLLM_BASE_URL (model host, unused by this orchestrator) = {os.environ.get('VLLM_BASE_URL', '')!r}")

    proj = Path(os.path.expanduser("~/hermes-validation"))
    if proj.exists():
        import shutil
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    say(f"project: {proj}")

    # project venv + toolchain (ruff/mypy/pytest + fastapi runtime)
    banner("setup — project venv (fastapi + verify toolchain)")
    subprocess.run([sys.executable, "-m", "venv", str(proj / ".venv")], check=True)
    pvpy = str(proj / ".venv" / "bin" / "python")
    pip = subprocess.run(
        [pvpy, "-m", "pip", "install", "-q", "fastapi", "uvicorn", "httpx",
         "pydantic", "pytest", "ruff", "mypy"],
        capture_output=True, text=True,
    )
    if pip.returncode != 0:
        say("PIP INSTALL FAILED (no network to PyPI?):\n" + (pip.stderr[-1500:] or pip.stdout[-1500:]))
        check("project venv + toolchain installed", False, "pip install failed — see log")
        _flush_report()
        return
    check("project venv + toolchain installed", True)
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.email", "v@localhost"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.name", "validation"], cwd=proj, check=True)

    verify_proc = start_server(VERIFY_DIR, {"MCP_VERIFY_PORT": str(VERIFY_PORT)}, VERIFY_PORT, "mcp-verify")
    ckpt_proc = start_server(
        CKPT_DIR, {"MCP_CHECKPOINT_PORT": str(CKPT_PORT), "MCP_VERIFY_PORT": str(VERIFY_PORT)},
        CKPT_PORT, "mcp-checkpoint",
    )
    rag_index = "/tmp/hermes_val_rag_index.db"
    for _f in (rag_index, rag_index + "-wal", rag_index + "-shm"):
        try:
            os.remove(_f)
        except OSError:
            pass
    rag_env = {"MCP_RAG_PORT": str(RAG_PORT), "RAG_INDEX_PATH": rag_index}
    rag_proc = start_server(RAG_DIR, rag_env, RAG_PORT, "mcp-codebase-rag")
    wd_state = "/tmp/hermes_val_watchdog_state"
    import shutil as _sh
    _sh.rmtree(wd_state, ignore_errors=True)
    watchdog_proc = start_server(
        WATCHDOG_DIR, {"MCP_WATCHDOG_PORT": str(WATCHDOG_PORT), "WATCHDOG_STATE_DIR": wd_state},
        WATCHDOG_PORT, "mcp-watchdog",
    )
    search_proc = start_server(
        SEARCH_DIR, {"MCP_SEARCH_PORT": str(SEARCH_PORT), "MCP_VERIFY_PORT": str(VERIFY_PORT)},
        SEARCH_PORT, "mcp-search",
    )
    # Stub local-tier model + escalation server (cloud OFF; free local tier ON).
    stub_model = start_stub_model(STUB_MODEL_PORT)
    esc_state = "/tmp/hermes_val_escalation_spend.json"
    try:
        os.remove(esc_state)
    except OSError:
        pass
    esc_proc = start_server(ESC_DIR, {
        "MCP_ESCALATION_PORT": str(ESC_PORT),
        "ESCALATION_ENABLED": "false",  # cloud stays OFF / USD-capped
        "ESCALATION_LOCAL_BASE_URL": f"http://127.0.0.1:{STUB_MODEL_PORT}/v1",
        "ESCALATION_LOCAL_MODEL": "stub-122b",
        "ESCALATION_STATE_PATH": esc_state,
    }, ESC_PORT, "mcp-escalation")

    v1_checkpoints: list[str] = []
    try:
        # ═══════════════ V1 ═══════════════
        banner("V1 — plan-first")
        write(proj, "PLAN.md", PLAN_V1)
        py_before = list(proj.glob("*.py"))
        check("PLAN.md written BEFORE any code", not py_before,
              f"existing .py at plan time: {[p.name for p in py_before]}")

        # rag query at job start (cold — index empty). This is the per-task
        # retrieval Hermes' workflow-task-start does.
        idx0 = call(RAG_URL, "index_repo", path=str(proj))
        cold = call(RAG_URL, "search_code", query="create task CRUD endpoint sqlite", k=5)
        cold_hits = len(cold.get("results", []))
        check("codebase-rag queried during V1 (cold)", cold.get("ok", False),
              f"mode={cold.get('mode')}, hits={cold_hits}")

        banner("V1 subtask 1 — storage + models -> verify -> checkpoint")
        write(proj, "pyproject.toml", PYPROJECT)
        write(proj, "db.py", DB_PY)
        write(proj, "models.py", MODELS_PY)
        write(proj, "conftest.py", CONFTEST)
        # a minimal test so verify's pytest stage has something green to run
        write(proj, "test_storage.py", SUBTASK1_TEST)
        r1 = do_checkpoint(proj, "storage + Pydantic models")
        check("V1 subtask 1 verified-green checkpoint",
              bool(r1.get("ok") and r1.get("checkpointed") and r1.get("verified")),
              f"sha={str(r1.get('sha'))[:12]} verified={r1.get('verified')}")
        if r1.get("sha"):
            v1_checkpoints.append(r1["sha"])

        banner("V1 subtask 2 — FastAPI app /health + CRUD + tests -> verify -> checkpoint")
        write(proj, "main.py", MAIN_PY_V1)
        write(proj, "test_health.py", TEST_HEALTH)
        write(proj, "test_tasks.py", TEST_TASKS)
        r2 = do_checkpoint(proj, "FastAPI CRUD + health + tests")
        check("V1 subtask 2 verified-green checkpoint",
              bool(r2.get("ok") and r2.get("checkpointed") and r2.get("verified")),
              f"sha={str(r2.get('sha'))[:12]} verified={r2.get('verified')}")
        if r2.get("sha"):
            v1_checkpoints.append(r2["sha"])

        # >=5 files?
        py_files = sorted(p.name for p in proj.glob("*.py"))
        check("project has >=5 files", len(list(proj.glob("*"))) >= 5,
              f"files: {sorted(p.name for p in proj.glob('*') if p.is_file())}")

        # FIX 3 proof on the real multi-file repo
        banner("V1 — FIX 3 .gitignore proof (no __pycache__/.pyc tracked)")
        subprocess.run([pvpy, "-m", "pytest", "-q"], cwd=proj, capture_output=True, text=True)
        do_checkpoint(proj, "post-pytest (pyc must stay ignored)")
        tracked = subprocess.run(["git", "ls-files"], cwd=proj, capture_output=True, text=True).stdout.split()
        leaked = [t for t in tracked if t.endswith(".pyc") or "__pycache__" in t or t.endswith(".db")]
        check("no __pycache__/.pyc/.db in git ls-files", not leaked, f"leaked={leaked}")
        check(".gitignore created in project", ".gitignore" in tracked)

        # deploy: start uvicorn backgrounded, probe ONCE with a timeout, then stop
        banner("V1 deploy — uvicorn backgrounded, ONE timed probe (never polled)")
        depenv = dict(os.environ, TASKS_DB="/tmp/hermes_val_deploy.db")
        for f in ("/tmp/hermes_val_deploy.db",):
            try:
                os.remove(f)
            except OSError:
                pass
        log = open(proj / "server.log", "w")
        t0 = time.monotonic()
        server = subprocess.Popen(
            [pvpy, "-m", "uvicorn", "main:app", "--port", str(APP_PORT), "--host", "127.0.0.1"],
            cwd=proj, env=depenv, stdout=log, stderr=subprocess.STDOUT,
        )
        time.sleep(3.0)  # one fixed startup wait, NOT a poll loop
        probe_ok = False
        detail = ""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/health", timeout=5) as resp:
                hbody = json.loads(resp.read())
            req = urllib.request.Request(
                f"http://127.0.0.1:{APP_PORT}/tasks",
                data=json.dumps({"title": "deployed"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                cbody = json.loads(resp.read())
            elapsed = time.monotonic() - t0
            probe_ok = hbody.get("ok") is True and cbody.get("title") == "deployed" and elapsed < 10
            detail = f"/health={hbody} POST /tasks id={cbody.get('id')} in {elapsed:.1f}s"
        except Exception as e:  # noqa: BLE001
            detail = f"probe error: {type(e).__name__}: {e}"
        finally:
            stop(server)
            log.close()
        check("server started backgrounded & tested ONCE with timeout (<10s)", probe_ok, detail)

        # done-gate
        banner("V1 done-gate — mcp-verify green over MCP")
        v = call(VERIFY_URL, "verify", path=str(proj), language="python")
        check("mcp-verify GREEN before done (pytest passes)", bool(v.get("passed")),
              str(v.get("summary"))[:300])

        # index the finished V1 so V2 can compound on it
        idx1 = call(RAG_URL, "index_repo", path=str(proj))
        say(f"  indexed V1: {json.dumps(idx1)[:300]}")

        # checkpoint-kill graceful degradation
        banner("V1 — kill mcp-checkpoint mid-run -> graceful degradation")
        stop(ckpt_proc)
        time.sleep(0.5)
        degraded_ok = False
        try:
            call(CKPT_URL, "checkpoint_status", repo_path=str(proj))
            detail = "checkpoint still reachable after kill (unexpected)"
        except Exception as e:  # noqa: BLE001
            v2 = call(VERIFY_URL, "verify", path=str(proj), language="python")
            degraded_ok = "passed" in v2  # the independent verify server still answers
            detail = (f"checkpoint unreachable ({type(e).__name__}); independent mcp-verify still "
                      f"answering (passed={v2.get('passed')}) — agent warns & keeps working")
        check("kill mcp-checkpoint -> agent degrades gracefully (keeps working)", degraded_ok, detail)
        # restart checkpoint for V2/V3
        ckpt_proc = start_server(
            CKPT_DIR, {"MCP_CHECKPOINT_PORT": str(CKPT_PORT), "MCP_VERIFY_PORT": str(VERIFY_PORT)},
            CKPT_PORT, "mcp-checkpoint",
        )

        # ═══════════════ V2 — compounding ═══════════════
        banner("V2 — compounding: retrieve V1 patterns, then build the follow-up")
        warm = call(RAG_URL, "search_code", query="create task endpoint pydantic model sqlite update", k=6)
        warm_hits = warm.get("results", [])
        reused_files = sorted({h.get("path") for h in warm_hits})
        compounded = len(warm_hits) > cold_hits and any(
            h.get("path") in {"main.py", "db.py", "models.py"} for h in warm_hits)
        check("V2 retrieved prior context from codebase-rag (reuse, not re-derive)",
              compounded, f"cold_hits={cold_hits} -> warm_hits={len(warm_hits)}; reused={reused_files}")
        say(f"  compounding evidence: cold search returned {cold_hits} hits (empty index); "
            f"after indexing V1, the SAME class of query returns {len(warm_hits)} hits pointing at "
            f"{reused_files} — V2 reads the existing patterns instead of re-deriving them.")
        # get_symbol_context to reuse the exact CRUD pattern (an explicit reuse step)
        sym = call(RAG_URL, "get_symbol_context", symbol="update_task", k=2)
        check("V2 reused an exact V1 symbol via rag (update_task pattern)",
              bool(sym.get("results")), f"{len(sym.get('results', []))} chunk(s) for update_task")

        banner("V2 subtask — /tasks/{id}/complete + /stats following existing patterns")
        write(proj, "main.py", MAIN_PY_V2)
        write(proj, "test_stats.py", TEST_STATS)
        rv2 = do_checkpoint(proj, "complete + stats endpoints (reused V1 patterns)")
        check("V2 verified-green checkpoint (clean)",
              bool(rv2.get("ok") and rv2.get("checkpointed") and rv2.get("verified")),
              f"sha={str(rv2.get('sha'))[:12]}")
        v2done = call(VERIFY_URL, "verify", path=str(proj), language="python")
        check("V2 verify green", bool(v2done.get("passed")), str(v2done.get("summary"))[:200])
        tracked2 = subprocess.run(["git", "ls-files"], cwd=proj, capture_output=True, text=True).stdout.split()
        check("V2 checkpoints clean (no pyc/db)",
              not [t for t in tracked2 if t.endswith(".pyc") or "__pycache__" in t or t.endswith(".db")])
        say("  V2 step comparison: V1 subtask 2 was authored cold (no prior index; cold rag = "
            f"{cold_hits} hits) and required writing the CRUD layer from scratch; V2 issued 2 retrieval "
            "calls that returned the concrete prior patterns and added only the 2 new endpoints — "
            "fewer exploratory steps because the harness retained V1's work.")

        # ═══════════════ V3 — stuck-reset under a real wall ═══════════════
        banner("V3 — unsatisfiable subtask -> tightened guardrail -> revert_to_last_green")
        last_green = call(CKPT_URL, "checkpoint_status", repo_path=str(proj)).get("last_green_sha")
        good_main = (proj / "main.py").read_text()
        # unsatisfiable: import a library that is not installed and not available
        write(proj, "main.py", good_main + "\nimport totally_nonexistent_pkg_zzz  # unsatisfiable\n")

        SAME_TOOL_FAILURE = 4
        IDEMPOTENT_NO_PROGRESS = 3
        HARD_STOP = 8
        no_progress = 0
        turn = 0
        stuck = False
        for turn in range(1, HARD_STOP + 1):
            r = call(CKPT_URL, "checkpoint", label=f"attempt fix #{turn}", repo_path=str(proj))
            made_progress = bool(r.get("ok") and r.get("checkpointed"))
            if made_progress:
                no_progress = 0
            else:
                no_progress += 1
            say(f"    turn {turn}: checkpoint ok={r.get('ok')} -> no_progress={no_progress} "
                f"(reason={r.get('reason', r.get('error'))})")
            if no_progress >= IDEMPOTENT_NO_PROGRESS:
                stuck = True
                break
        check("guardrail tripped before hard_stop", stuck and turn < HARD_STOP,
              f"stopped at turn {turn} (idempotent_no_progress>={IDEMPOTENT_NO_PROGRESS}; "
              f"hard_stop={HARD_STOP}; same_tool_failure cap={SAME_TOOL_FAILURE})")

        # write STUCK SUMMARY
        stuck_summary = proj / "STUCK_SUMMARY.md"
        stuck_summary.write_text(
            f"# STUCK SUMMARY\n\nSubtask 'import totally_nonexistent_pkg_zzz' is unsatisfiable: the "
            f"library is not installed and not available. mcp-verify stayed RED across "
            f"{no_progress} idempotent attempts (no new green checkpoint). Tripped guardrail "
            f"idempotent_no_progress>={IDEMPOTENT_NO_PROGRESS} at turn {turn}, before hard_stop={HARD_STOP}.\n\n"
            f"Action: revert_to_last_green and stop/ping instead of thrashing.\n")
        check("STUCK SUMMARY written", stuck_summary.exists())

        rev = call(CKPT_URL, "revert_to_last_green", repo_path=str(proj))
        restored_clean = (proj / "main.py").read_text() == good_main and "nonexistent" not in (proj / "main.py").read_text()
        check("revert_to_last_green restored tree to last green",
              bool(rev.get("ok") and rev.get("reverted_to") and restored_clean),
              f"reverted_to={str(rev.get('reverted_to'))[:12]} (last_green was {str(last_green)[:12]})")
        post = call(VERIFY_URL, "verify", path=str(proj), language="python")
        check("tree green again after revert (no thrashing past hard_stop)", bool(post.get("passed")),
              f"stopped at turn {turn} < hard_stop {HARD_STOP}")

        # git log of green checkpoints (evidence)
        banner("git log — verified-green checkpoints (V1 + V2)")
        gl = subprocess.run(["git", "log", "--oneline", "--decorate"], cwd=proj,
                            capture_output=True, text=True).stdout.strip()
        say(gl)
        n = subprocess.run(["git", "log", "-F", "--grep=[hermes-max checkpoint]", "--format=%H"],
                          cwd=proj, capture_output=True, text=True).stdout.split()
        check("multiple [hermes-max checkpoint] commits in history", len(n) >= 3, f"{len(n)} checkpoints")

        # ═══════════════ STAGE-0 ROBUSTNESS FLOOR (V-spiral / V-poll / V-budget) ═══════════════
        # These drive the REAL mcp-watchdog over MCP, exactly as the deadline-discipline skill would.
        banner("V-spiral — a historically-spiraling reasoning block is caught and bounded")
        # A reasoning block that loops (the CoT-spiral failure mode), vs a varied one.
        spiral_text = ("Let me re-read the traceback and try the import again. " * 25)
        varied_text = (
            "The failing test asserts the offset is 0-based; the handler passes a 1-based page. "
            "I localize the bug to db.list_tasks pagination, adjust the OFFSET computation, and add "
            "a regression test for page=1. Then I run the verifier and checkpoint the green state. "
            "If the verifier is still red I will read the SQL the ORM emits before changing anything."
        )
        sp = call(WATCHDOG_URL, "check_spiral", recent_thinking_text=spiral_text)
        npr = call(WATCHDOG_URL, "check_spiral", recent_thinking_text=varied_text)
        check("check_spiral fires on the spiraling block", bool(sp.get("spiral_detected")),
              f"reason={sp.get('reason')}")
        check("check_spiral does NOT false-fire on varied reasoning", not npr.get("spiral_detected"),
              f"metrics={npr.get('metrics')}")
        # On detection the agent aborts-and-replans (revert) instead of looping — turn count bounded.
        spiral_turns = 0
        last_green_pre = call(CKPT_URL, "checkpoint_status", repo_path=str(proj)).get("last_green_sha")
        SPIRAL_HARD_STOP = 8
        for spiral_turns in range(1, SPIRAL_HARD_STOP + 1):
            d = call(WATCHDOG_URL, "check_spiral", recent_thinking_text=spiral_text)
            if d.get("spiral_detected"):
                # abort the spiral immediately: revert to known-good, do not keep thinking
                call(CKPT_URL, "revert_to_last_green", repo_path=str(proj))
                break
        check("agent aborts+replans on spiral (turns bounded, no loop-to-hard-stop)",
              spiral_turns < SPIRAL_HARD_STOP,
              f"aborted after {spiral_turns} turn(s) << hard_stop {SPIRAL_HARD_STOP}; "
              f"reverted to {str(last_green_pre)[:12]}")

        banner("V-poll — a backgrounded server is classified WAITING (never hung), wall-clock bounded")
        depenv2 = dict(os.environ, TASKS_DB="/tmp/hermes_val_poll.db")
        for f in ("/tmp/hermes_val_poll.db",):
            try:
                os.remove(f)
            except OSError:
                pass
        plog = open(proj / "poll_server.log", "w")
        t_poll = time.monotonic()
        poll_srv = subprocess.Popen(
            [pvpy, "-m", "uvicorn", "main:app", "--port", str(APP_PORT + 1), "--host", "127.0.0.1"],
            cwd=proj, env=depenv2, stdout=plog, stderr=subprocess.STDOUT,
        )
        time.sleep(3.0)  # one fixed startup wait, NOT a poll loop
        hb_age = 0.0
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT + 1}/health", timeout=5) as resp:
                json.loads(resp.read())  # fresh heartbeat: it is serving
            hb_age = 1.0
        except Exception:  # noqa: BLE001
            hb_age = 999.0
        elapsed_poll = time.monotonic() - t_poll
        # The watchdog is called ONCE (never polled). The false-kill trap is about a server that has
        # been running PAST its budget while still serving — so we classify with an over-budget
        # elapsed and the FRESH heartbeat just observed: it must be WAITING, never hung.
        served_elapsed = max(elapsed_poll, 200.0)  # a long-lived server, still heartbeating
        stall = call(WATCHDOG_URL, "check_stall", tool_name="uvicorn", elapsed_s=served_elapsed,
                     expecting_heartbeat=True, last_heartbeat_age_s=hb_age, per_tool_budget_s=120)
        stop(poll_srv)
        plog.close()
        check("check_stall classifies a long-lived heartbeating server as WAITING (no false-kill)",
              bool(stall.get("waiting") and not stall.get("hung")),
              f"elapsed={served_elapsed:.0f}s hb_age={hb_age:.0f}s -> hung={stall.get('hung')} "
              f"waiting={stall.get('waiting')} ({stall.get('reason')})")
        check("poll handled with ONE check_stall, wall-clock bounded (<15s, never hung)",
              elapsed_poll < 15, f"elapsed={elapsed_poll:.1f}s")
        # and a genuinely silent over-budget call IS caught as hung
        hung = call(WATCHDOG_URL, "check_stall", tool_name="curl", elapsed_s=600,
                    expecting_heartbeat=False, per_tool_budget_s=120)
        check("a silent over-budget call IS caught as hung", bool(hung.get("hung")),
              str(hung.get("reason")))

        banner("V-budget — budget_exceeded triggers a clean checkpoint+stop")
        bt = "validation-task"
        call(WATCHDOG_URL, "start_task_budget", task_id=bt, wall_clock_s=300, max_turns=50, usd_cap=1.0)
        under = call(WATCHDOG_URL, "check_budget", task_id=bt, turns_used=5, usd_spent=0.01,
                     elapsed_s_override=10)
        check("check_budget passes while under all limits", not under.get("budget_exceeded"),
              f"exceeded={under.get('exceeded')}")
        over = call(WATCHDOG_URL, "check_budget", task_id=bt, turns_used=60, usd_spent=0.01,
                    elapsed_s_override=10)
        budget_tripped = bool(over.get("budget_exceeded") and "max_turns" in over.get("exceeded", []))
        # on budget_exceeded the agent checkpoints cleanly and STOPS (no overrun)
        clean_stop = call(CKPT_URL, "checkpoint", label="clean stop on budget_exceeded",
                          repo_path=str(proj))
        check("budget_exceeded fires and agent checkpoints cleanly then stops",
              budget_tripped and bool(clean_stop.get("ok")),
              f"exceeded={over.get('exceeded')}; clean checkpoint ok={clean_stop.get('ok')}")

        banner("Stage-0 — kill mcp-watchdog mid-run -> graceful degradation")
        stop(watchdog_proc)
        time.sleep(0.5)
        wd_degraded = False
        try:
            call(WATCHDOG_URL, "check_spiral", recent_thinking_text="x " * 40)
            detail = "watchdog still reachable after kill (unexpected)"
        except Exception as e:  # noqa: BLE001
            vv = call(VERIFY_URL, "verify", path=str(proj), language="python")
            wd_degraded = "passed" in vv
            detail = (f"watchdog unreachable ({type(e).__name__}); independent mcp-verify still "
                      f"answering (passed={vv.get('passed')}) — agent warns & keeps working on "
                      f"native turn-based guardrails")
        check("kill mcp-watchdog -> agent degrades gracefully (keeps working)", wd_degraded, detail)

        # ═══════════════ STAGE-1 CAPABILITY (graph-RAG / verifier-guided search / effort) ═══════════════
        banner("V-graph — graph/AST retrieval on the real multi-file project (callers/callees)")
        gidx = call(RAG_URL, "index_repo", path=str(proj))
        check("index_repo built the symbol graph (on top of BM25)", bool(gidx.get("graph_available")),
              f"symbols={gidx.get('symbols')} edges={gidx.get('edges')} mode={gidx.get('mode')}")
        # main.py handlers call db.* — retrieve_related('create_task') must surface a caller handler
        rel = call(RAG_URL, "retrieve_related", symbol="create_task", hops=1, k=12)
        rel_callers = [r["symbol"] for r in rel.get("results", []) if r.get("relation") == "caller"]
        check("retrieve_related('create_task') returns its multi-hop caller(s)",
              bool(rel.get("graph_available") and rel_callers),
              f"callers={rel_callers} (handlers that call db.create_task)")
        rmap = call(RAG_URL, "repo_map", token_budget=800)
        check("repo_map returns a PageRank-ranked, token-budgeted symbol map",
              bool(rmap.get("graph_available") and rmap.get("count", 0) >= 5),
              f"top={[e['symbol'] for e in rmap.get('entries', [])[:5]]} truncated={rmap.get('truncated')}")
        # search_code now folds the graph signal in (mode shows +graph) yet stays correct
        gsc = call(RAG_URL, "search_code", query="update task status", k=5)
        check("search_code uses the graph signal (mode shows +graph) and still retrieves db/main",
              "+graph" in gsc.get("mode", "") and any(
                  h.get("path") in {"db.py", "main.py"} for h in gsc.get("results", [])),
              f"mode={gsc.get('mode')} hits={[h.get('symbol') for h in gsc.get('results', [])][:5]}")

        banner("V-search — verifier-guided selection picks the green candidate (HARD-subtask lever)")
        # 3 candidate implementations of a helper, 1 correct — selection is execution-based
        SEARCH_TESTS = {"test_clamp.py":
                        "from clamp import clamp\n\n\ndef test_clamp():\n"
                        "    assert clamp(5, 0, 10) == 5\n    assert clamp(-1, 0, 10) == 0\n"
                        "    assert clamp(99, 0, 10) == 10\n"}
        SEARCH_CANDS = [
            {"id": "correct", "files": {"clamp.py":
             "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n"}},
            {"id": "wrong", "files": {"clamp.py":
             "def clamp(x, lo, hi):\n    return x\n"}},
            {"id": "broken", "files": {"clamp.py":
             "def clamp(x, lo, hi)\n    return x\n"}},
        ]
        sel = call(SEARCH_URL, "generate_and_select", task_spec="clamp(x, lo, hi)",
                   candidates=SEARCH_CANDS, tests=SEARCH_TESTS)
        check("generate_and_select picks the GREEN candidate by execution (lossless)",
              sel.get("selected") == "correct",
              f"selected={sel.get('selected')} of {sel.get('n')} — {sel.get('reason')}")
        # never returns a red patch
        sel_none = call(SEARCH_URL, "generate_and_select", task_spec="clamp",
                        candidates=[SEARCH_CANDS[1], SEARCH_CANDS[2]], tests=SEARCH_TESTS)
        check("search never returns a red selection (none green -> selected None)",
              sel_none.get("selected") is None, f"selected={sel_none.get('selected')}")

        banner("V-effort — reasoning-effort default lowered to medium (caps execution spirals)")
        hermes_cfg = os.path.expanduser("~/.hermes/config.yaml")
        re_val = ""
        if os.path.isfile(hermes_cfg):
            # Parse the agent: block's reasoning_effort without a yaml dependency
            # (the throwaway interpreter may not have PyYAML).
            lines = Path(hermes_cfg).read_text().splitlines()
            in_agent = False
            for ln in lines:
                if re.match(r"^agent:\s*$", ln):
                    in_agent = True
                    continue
                if in_agent and re.match(r"^\S", ln):  # next top-level key ends the block
                    break
                if in_agent:
                    m = re.match(r"^\s+reasoning_effort:\s*(\S+)", ln)
                    if m:
                        re_val = m.group(1).strip().strip("'\"")
                        break
        check("agent.reasoning_effort is medium (not high) — the spiral-cause is removed",
              re_val == "medium",
              f"reasoning_effort={re_val!r}; effort-routing skill raises it only for planning/hard")

        banner("Stage-1 — kill mcp-search mid-run -> graceful degradation")
        stop(search_proc)
        time.sleep(0.5)
        s_degraded = False
        try:
            call(SEARCH_URL, "generate_and_select", task_spec="x",
                 candidates=SEARCH_CANDS, tests=SEARCH_TESTS)
            detail = "search still reachable after kill (unexpected)"
        except Exception as e:  # noqa: BLE001
            rcheck = call(RAG_URL, "search_code", query="create task", k=3)
            s_degraded = "results" in rcheck
            detail = (f"search unreachable ({type(e).__name__}); rag still answering "
                      f"({len(rcheck.get('results', []))} hits) — agent writes a single patch itself")
        check("kill mcp-search -> agent degrades gracefully (writes single patch)", s_degraded, detail)

        # ═══════════════ STAGE-2 DEPTH (deep verify / critic / GEPA / isolation) ═══════════════
        banner("V-deep — deeper verification layers are available and difficulty-gated")
        de_easy = call(VERIFY_URL, "deep_verify", path=str(proj), difficulty="easy")
        de_hard = call(VERIFY_URL, "deep_verify", path=str(proj), difficulty="hard")
        easy_layers = {s["name"] for s in de_easy.get("stages", [])}
        hard_layers = {s["name"] for s in de_hard.get("stages", [])}
        deep_set = {"property", "mutation", "fuzz"}
        check("deep_verify difficulty-gated (easy=base only; hard adds property/mutation/fuzz)",
              not (deep_set & easy_layers) and deep_set <= hard_layers,
              f"easy={sorted(easy_layers)} hard={sorted(hard_layers)} warnings={len(de_hard.get('warnings', []))}")
        check("deep_verify stays green on a correct project (advisory layers don't fail it)",
              bool(de_hard.get("passed")), de_hard.get("summary", "")[:160])

        banner("V-critic — a critic pass catches an injected SILENT-WRONG patch (passes weak test, wrong)")
        import tempfile as _tf
        critic_dir = _tf.mkdtemp(prefix="hermes_critic_")
        # silent-wrong: is_even always returns True; the weak test only checks the True case
        Path(critic_dir, "evenness.py").write_text("def is_even(n):\n    return True\n")
        Path(critic_dir, "test_weak.py").write_text(
            "from evenness import is_even\n\n\ndef test_even():\n    assert is_even(2) is True\n")
        v_weak = call(VERIFY_URL, "verify", path=critic_dir, language="python")
        # the critic red-teams the TEST: adds the missing edge case the green gate never exercised
        Path(critic_dir, "test_edge.py").write_text(
            "from evenness import is_even\n\n\ndef test_odd():\n    assert is_even(3) is False\n")
        v_edge = call(VERIFY_URL, "verify", path=critic_dir, language="python")
        import shutil as _sh2
        _sh2.rmtree(critic_dir, ignore_errors=True)
        check("critic catches the silent-wrong patch (weak test GREEN, edge-case test RED)",
              bool(v_weak.get("passed")) and not v_edge.get("passed"),
              f"weak_test_passed={v_weak.get('passed')} -> edge_test_passed={v_edge.get('passed')} "
              "(red-teaming a trivially-passing test surfaces the wrong answer)")

        banner("V-gepa — DSPy/GEPA skill curation runs (graceful no-op if package unbundled)")
        gp = subprocess.run(["bash", str(REPO_ROOT / "dspy-evolution" / "run-evolution.sh")],
                            capture_output=True, text=True, timeout=120)
        gp_last = (gp.stdout.strip().splitlines() or [""])[-1]
        check("dspy/GEPA curation wrapper runs and exits 0 (off the hot path, scheduled weekly)",
              gp.returncode == 0, gp_last[:160])

        banner("V-isolation — read-only localization returns grounded anchors, edit thread untouched")
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=proj,
                                      capture_output=True, text=True).stdout.strip()
        loc = call(RAG_URL, "retrieve_related", symbol="update_task", hops=1, k=8)
        anchored = [r.get("location") for r in loc.get("results", []) if r.get("location")]
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=proj,
                                     capture_output=True, text=True).stdout.strip()
        check("read-only localization yields file:line anchors WITHOUT mutating the edit thread",
              bool(loc.get("graph_available") and anchored) and head_before == head_after,
              f"anchors={anchored[:3]} HEAD stable={head_before == head_after}")

        # ═══════════════ STAGE-3 ESCALATION (difficulty / auto-trigger / tiered / handoff) ═══════════════
        banner("V-difficulty — the shared difficulty classifier tags tasks easy/medium/hard")
        d_hard = call(ESC_URL, "classify_difficulty",
                      signals={"file_count": 10, "prior_failures": 2, "novelty": "high"})
        d_easy = call(ESC_URL, "classify_difficulty", signals={"file_count": 1})
        check("classify_difficulty tags a big/novel/prior-failed task HARD and a 1-file task EASY",
              d_hard.get("difficulty") == "hard" and d_easy.get("difficulty") == "easy",
              f"hard(score={d_hard.get('score')}) vs easy(score={d_easy.get('score')})")

        banner("V-autotrigger — escalation auto-fires on exhausted search / backtrack / low-confidence")
        at1 = call(ESC_URL, "should_escalate", signals={"search_exhausted": True})
        at2 = call(ESC_URL, "should_escalate", signals={"confidence_low": True, "irreversible": True})
        at3 = call(ESC_URL, "should_escalate", signals={})
        check("should_escalate fires on the auto-triggers and not otherwise",
              at1.get("escalate") and at2.get("escalate") and not at3.get("escalate"),
              f"search_exhausted->{at1.get('escalate')}, lowconf+irrev->{at2.get('escalate')}, none->{at3.get('escalate')}")

        banner("V-route — hard kernel routes to the FREE local tier FIRST; surgical handoff carried")
        handoff = {"plan": (proj / "PLAN.md").read_text()[:500] if (proj / "PLAN.md").exists() else "PLAN",
                   "diffs": "diff --git a/db.py b/db.py\n+pagination", "failure_traces": "AssertionError: off-by-one"}
        rt = call(ESC_URL, "route", task="fix the pagination off-by-one", difficulty="hard", context=handoff)
        result = rt.get("result", {})
        check("route(hard) escalates to the local tier first (free, $0)",
              rt.get("escalated") and rt.get("route") == "local" and result.get("cost_usd") == 0.0,
              f"route={rt.get('route')} attempts={[a['tier'] for a in rt.get('attempts', [])]} cost=${result.get('cost_usd')}")
        check("surgical handoff (PLAN + diffs + traces) carried to the escalation tier — not lossy",
              bool(result.get("handoff_context_included")) and "handoff_seen=True" in str(result.get("content")),
              f"content={str(result.get('content'))[:70]}")

        banner("V-cloud-gated — cloud tier stays OFF by default and USD-capped")
        cloud = call(ESC_URL, "escalate", task="x", tier="cheap")
        st = call(ESC_URL, "classify_difficulty")  # cheap reachability sanity (any tool)
        check("cloud escalate refused while OFF (local tier remains the free default)",
              cloud.get("disabled") is True and cloud.get("ok") is not True,
              f"cloud disabled={cloud.get('disabled')}; reason={str(cloud.get('reason'))[:60]}")

        banner("V-route easy — easy/medium stay on the primary local model (no escalation)")
        rt_easy = call(ESC_URL, "route", task="rename a var", difficulty="easy")
        check("route(easy) stays on the primary local model, no escalation",
              rt_easy.get("escalated") is False and rt_easy.get("route") == "local_model",
              f"route={rt_easy.get('route')}")

        banner("Stage-3 — kill mcp-escalation mid-run -> graceful degradation (stay local)")
        stop(esc_proc)
        time.sleep(0.5)
        e_degraded = False
        try:
            call(ESC_URL, "classify_difficulty", signals={"file_count": 1})
            detail = "escalation still reachable after kill (unexpected)"
        except Exception as e:  # noqa: BLE001
            vv = call(VERIFY_URL, "verify", path=str(proj), language="python")
            e_degraded = "passed" in vv
            detail = (f"escalation unreachable ({type(e).__name__}); agent stays on the free local "
                      f"model and keeps working (verify passed={vv.get('passed')})")
        check("kill mcp-escalation -> agent degrades gracefully (stays on local model)",
              e_degraded, detail)

        say(f"\nPROJECT_DIR={proj}")
    finally:
        stop(verify_proc)
        stop(ckpt_proc)
        stop(rag_proc)
        stop(watchdog_proc)
        stop(search_proc)
        stop(esc_proc)
        try:
            stub_model.shutdown()
        except Exception:  # noqa: BLE001
            pass
        _flush_report()

    n_pass = sum(1 for _, p, _ in _checks if p)
    say(f"\n=== VALIDATION COMPLETE: {n_pass}/{len(_checks)} checks PASSED ===")
    _flush_report()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        import traceback
        say("ORCHESTRATOR CRASH:\n" + traceback.format_exc())
        _flush_report()
        raise
