"""Deterministic verification core: lint -> typecheck -> unit tests.

No model calls, no network, no randomness. Each stage shells out to a proven
tool and reports a structured pass/fail. A stage whose tool is missing is
reported as "skipped" (never silently passed, never flaky-failed). The overall
gate is green only when every stage that actually ran passed.

Supported languages: python (ruff, mypy/ty, pytest), ts/js (eslint, tsc,
vitest|jest), rust (clippy, cargo check, cargo test). Languages whose toolchain
is absent degrade to "skipped" rather than crashing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# Per-stage wall-clock cap. Deterministic: a hang becomes a reported error,
# never an indefinite block.
STAGE_TIMEOUT = int(os.environ.get("VERIFY_STAGE_TIMEOUT", "300"))
# Keep diagnostics useful but bounded so we never flood the agent's context.
MAX_OUTPUT_CHARS = int(os.environ.get("VERIFY_MAX_OUTPUT_CHARS", "4000"))


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    head = text[: MAX_OUTPUT_CHARS - 200]
    tail = text[-200:]
    return f"{head}\n...[truncated {len(text) - MAX_OUTPUT_CHARS} chars]...\n{tail}"


def _run(cmd: list[str], cwd: str) -> dict[str, Any]:
    """Run one command, returning a normalized result dict.

    status is one of: passed | failed | error. Callers map FileNotFoundError
    (tool absent) to "skipped" before getting here.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=STAGE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "returncode": None,
            "output": f"timed out after {STAGE_TIMEOUT}s",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return {
        "status": "passed" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "output": _truncate(out),
        "duration_ms": int((time.monotonic() - start) * 1000),
    }


def _skip(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "returncode": None, "output": reason, "duration_ms": 0}


def detect_language(path: str) -> str:
    p = Path(path)
    root = p if p.is_dir() else p.parent
    if p.is_file():
        suffix = p.suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            return "ts"
        if suffix == ".rs":
            return "rust"
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or list(root.glob("*.py")):
        return "python"
    if (root / "package.json").exists() or (root / "tsconfig.json").exists():
        return "ts"
    if (root / "Cargo.toml").exists():
        return "rust"
    # Last resort: any python files anywhere shallow
    if list(root.glob("**/*.py"))[:1]:
        return "python"
    return "unknown"


def _project_python(path: str) -> str:
    """Pick the interpreter that has the project's deps if we can find one.

    Honors VERIFY_PYTHON, then a local .venv/venv, then the running interpreter.
    Running ruff/mypy/pytest as `python -m` against the project's own venv is the
    boring, correct way to see the project's installed dependencies.
    """
    override = os.environ.get("VERIFY_PYTHON")
    if override and Path(override).exists():
        return override
    root = Path(path)
    root = root if root.is_dir() else root.parent
    for cand in (root / ".venv" / "bin" / "python", root / "venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    import sys

    return sys.executable


def _has_module(py: str, module: str) -> bool:
    try:
        r = subprocess.run([py, "-c", f"import {module}"], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _verify_python(path: str) -> list[dict[str, Any]]:
    py = _project_python(path)
    target = str(Path(path))
    stages: list[dict[str, Any]] = []

    # 1. lint (ruff)
    if _has_module(py, "ruff"):
        r = _run([py, "-m", "ruff", "check", target], cwd=os.getcwd())
        r.update(name="lint", tool="ruff")
    else:
        r = _skip("ruff not installed in target environment")
        r.update(name="lint", tool="ruff")
    stages.append(r)

    # 2. typecheck (prefer ty, fall back to mypy)
    if _has_module(py, "ty"):
        r = _run([py, "-m", "ty", "check", target], cwd=os.getcwd())
        r.update(name="typecheck", tool="ty")
    elif _has_module(py, "mypy"):
        r = _run([py, "-m", "mypy", "--ignore-missing-imports", target], cwd=os.getcwd())
        r.update(name="typecheck", tool="mypy")
    else:
        r = _skip("neither ty nor mypy installed in target environment")
        r.update(name="typecheck", tool="ty|mypy")
    stages.append(r)

    # 3. unit tests (pytest)
    test_dir = Path(path) if Path(path).is_dir() else Path(path).parent
    if _has_module(py, "pytest"):
        r = _run([py, "-m", "pytest", "-q", str(test_dir)], cwd=str(test_dir))
        # pytest exit code 5 == "no tests collected": treat as skipped, not failed.
        if r.get("returncode") == 5:
            r = _skip("no tests collected by pytest")
        r.update(name="tests", tool="pytest")
    else:
        r = _skip("pytest not installed in target environment")
        r.update(name="tests", tool="pytest")
    stages.append(r)

    return stages


def _verify_ts(path: str) -> list[dict[str, Any]]:
    target = str(Path(path))
    root = Path(path) if Path(path).is_dir() else Path(path).parent
    stages: list[dict[str, Any]] = []
    npx = shutil.which("npx")

    # 1. lint (eslint)
    if npx and (root / "node_modules" / ".bin" / "eslint").exists():
        r = _run([npx, "--no-install", "eslint", target], cwd=str(root))
        r.update(name="lint", tool="eslint")
    else:
        r = _skip("eslint not installed locally (node_modules/.bin/eslint absent)")
        r.update(name="lint", tool="eslint")
    stages.append(r)

    # 2. typecheck (tsc --noEmit)
    if npx and (root / "node_modules" / ".bin" / "tsc").exists():
        r = _run([npx, "--no-install", "tsc", "--noEmit"], cwd=str(root))
        r.update(name="typecheck", tool="tsc")
    else:
        r = _skip("tsc not installed locally (node_modules/.bin/tsc absent)")
        r.update(name="typecheck", tool="tsc")
    stages.append(r)

    # 3. unit tests (vitest|jest via npm test)
    runner = None
    if (root / "node_modules" / ".bin" / "vitest").exists():
        runner = ["npx", "--no-install", "vitest", "run"]
    elif (root / "node_modules" / ".bin" / "jest").exists():
        runner = ["npx", "--no-install", "jest"]
    if npx and runner:
        r = _run(runner, cwd=str(root))
        r.update(name="tests", tool=runner[2])
    else:
        r = _skip("no vitest/jest found in node_modules/.bin")
        r.update(name="tests", tool="vitest|jest")
    stages.append(r)

    return stages


def _verify_rust(path: str) -> list[dict[str, Any]]:
    root = Path(path) if Path(path).is_dir() else Path(path).parent
    stages: list[dict[str, Any]] = []
    cargo = shutil.which("cargo")
    if not cargo:
        for name, tool in (("lint", "clippy"), ("typecheck", "cargo check"), ("tests", "cargo test")):
            s = _skip("cargo not installed")
            s.update(name=name, tool=tool)
            stages.append(s)
        return stages

    r = _run([cargo, "clippy", "--", "-D", "warnings"], cwd=str(root))
    r.update(name="lint", tool="clippy")
    stages.append(r)
    r = _run([cargo, "check"], cwd=str(root))
    r.update(name="typecheck", tool="cargo check")
    stages.append(r)
    r = _run([cargo, "test"], cwd=str(root))
    r.update(name="tests", tool="cargo test")
    stages.append(r)
    return stages


def verify(path: str, language: str = "auto") -> dict[str, Any]:
    """Run the deterministic gate against `path`.

    Returns a structured result. `passed` is True only if at least one stage ran
    and no stage failed or errored. Missing tools are "skipped" and do not pass
    or fail the gate on their own.
    """
    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abspath):
        return {
            "path": abspath,
            "language": language,
            "passed": False,
            "stages": [],
            "summary": f"path does not exist: {abspath}",
        }

    lang = language if language and language != "auto" else detect_language(abspath)
    if lang in ("js", "javascript", "typescript"):
        lang = "ts"

    if lang == "python":
        stages = _verify_python(abspath)
    elif lang == "ts":
        stages = _verify_ts(abspath)
    elif lang == "rust":
        stages = _verify_rust(abspath)
    else:
        return {
            "path": abspath,
            "language": lang,
            "passed": False,
            "stages": [],
            "summary": f"unsupported or undetected language: {lang}",
        }

    ran = [s for s in stages if s["status"] in ("passed", "failed", "error")]
    bad = [s for s in stages if s["status"] in ("failed", "error")]
    passed = bool(ran) and not bad

    if not ran:
        summary = "no stages ran (all tools skipped) — gate cannot certify; install toolchain"
    elif passed:
        summary = "PASS: " + ", ".join(f"{s['name']}({s['tool']})" for s in ran)
    else:
        summary = "FAIL: " + ", ".join(f"{s['name']} {s['status']}" for s in bad)

    return {
        "path": abspath,
        "language": lang,
        "passed": passed,
        "stages": stages,
        "summary": summary,
    }
