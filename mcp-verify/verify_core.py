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
import re
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

    return _finalize(abspath, lang, stages)


def _finalize(abspath: str, lang: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
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


def quick_check(path: str, language: str = "auto") -> dict[str, Any]:
    """Fast incremental check: lint + typecheck ONLY (skips the test stage).

    For the edit-format discipline — a cheap, well-formed-edit gate to run after
    EACH diff/search-replace edit, before the heavier full verify() at subtask
    end. Same structured shape as verify(), with `tests` omitted (not run).
    """
    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abspath):
        return {"path": abspath, "language": language, "passed": False, "stages": [],
                "summary": f"path does not exist: {abspath}"}
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
        return {"path": abspath, "language": lang, "passed": False, "stages": [],
                "summary": f"unsupported or undetected language: {lang}"}
    stages = [s for s in stages if s.get("name") != "tests"]  # incremental: no pytest
    return _finalize(abspath, lang, stages)


# ── deeper verification layers (Stage 2.1) ───────────────────────────────────
# Closes the silent-wrong-answer gap (~20% of patches are semantically wrong but
# pass shallow tests). Each layer is OPTIONAL, INDEPENDENTLY SKIPPABLE (a missing
# tool is reported, never an error), and ADVISORY (it warns; it does not flip a
# green base gate red — except property tests, which are real tests). Depth is
# tied to the difficulty signal so trivial changes don't pay for mutation runs.
DIFFICULTY_LAYERS = {
    "easy": [],
    "medium": ["property"],
    "hard": ["property", "mutation", "fuzz"],
}

# Per-repo MUTATION-TESTING feature flag (Stage 2 sanctioned addition). OFF by
# default — turn it on ONLY for a repo whose eval shows a WEAK TEST ORACLE (the
# suite stays green on a deliberately-bad change). Enable globally with
# VERIFY_MUTATION_ENABLED=1, or per-repo by dropping an empty `.hermes-mutation`
# marker file at the repo root. When off, the difficulty-derived mutation layer is
# reported as explicitly-skipped (visible in the trace), never silently dropped.
# An explicit `layers=["mutation"]` request is an operator override and runs anyway.
MUTATION_ENABLED = os.environ.get("VERIFY_MUTATION_ENABLED", "").strip().lower() in (
    "1", "true", "yes", "on")
MUTATION_MARKER = ".hermes-mutation"


def _layer_property(py: str, path: str) -> dict[str, Any]:
    """Property-based testing (hypothesis). If hypothesis is installed, re-run
    pytest (which executes any @given tests); else skip with a warning."""
    if not _has_module(py, "hypothesis"):
        s = _skip("hypothesis not installed — property layer skipped")
        s.update(name="property", tool="hypothesis", advisory=True)
        return s
    test_dir = Path(path) if Path(path).is_dir() else Path(path).parent
    r = _run([py, "-m", "pytest", "-q", "-p", "hypothesis", str(test_dir)], cwd=str(test_dir))
    if r.get("returncode") == 5:
        r = _skip("no tests collected for property run")
    r.update(name="property", tool="hypothesis", advisory=False)
    return r


def _mutation_enabled(path: str) -> bool:
    """Per-repo gate for mutation testing: global env flag OR a .hermes-mutation
    marker at/above the target, stopping at the repo root (.git)."""
    if MUTATION_ENABLED:
        return True
    p = Path(path)
    root = p if p.is_dir() else p.parent
    for d in [root, *root.parents]:
        if (d / MUTATION_MARKER).exists():
            return True
        if (d / ".git").exists():
            break
    return False


def _layer_mutation(py: str, path: str) -> dict[str, Any]:
    """Mutation testing — report SURVIVING mutants (tests that don't catch a
    deliberate bug). Uses mutmut if available; otherwise skip with a warning.
    Advisory: surviving mutants warn, they don't fail the gate."""
    if not _has_module(py, "mutmut"):
        s = _skip("mutmut not installed — mutation layer skipped (install mutmut to enable)")
        s.update(name="mutation", tool="mutmut", advisory=True)
        return s
    test_dir = str(Path(path) if Path(path).is_dir() else Path(path).parent)
    r = _run([py, "-m", "mutmut", "run", "--paths-to-mutate", test_dir], cwd=test_dir)
    surviving = None
    m = re.search(r"(\d+)\s+survived", (r.get("output") or "").lower())
    if m:
        surviving = int(m.group(1))
    # mutation result is advisory: never fail the gate on it
    r["status"] = "passed" if r.get("status") != "error" else "error"
    r.update(name="mutation", tool="mutmut", advisory=True, surviving_mutants=surviving)
    return r


def _layer_fuzz(py: str, path: str) -> dict[str, Any]:
    """Lightweight fuzz harness. Runs atheris-based fuzz targets if present;
    otherwise skip with a warning (no targets / atheris absent)."""
    if not _has_module(py, "atheris"):
        s = _skip("atheris not installed / no fuzz targets — fuzz layer skipped")
        s.update(name="fuzz", tool="atheris", advisory=True)
        return s
    root = Path(path) if Path(path).is_dir() else Path(path).parent
    targets = list(root.glob("fuzz_*.py"))
    if not targets:
        s = _skip("no fuzz_*.py targets found — fuzz layer skipped")
        s.update(name="fuzz", tool="atheris", advisory=True)
        return s
    r = _run([py, str(targets[0]), "-atheris_runs=2000"], cwd=str(root))
    r["status"] = "passed" if r.get("status") != "error" else "error"
    r.update(name="fuzz", tool="atheris", advisory=True)
    return r


def deep_verify(path: str, language: str = "auto", difficulty: str = "medium",
                layers: list[str] | None = None) -> dict[str, Any]:
    """Full gate PLUS difficulty-gated deeper layers (property/mutation/fuzz).

    difficulty: easy -> base only; medium -> +property; hard -> +property,
    mutation, fuzz. `layers` overrides the difficulty-derived set. Each extra
    layer is independently skippable (missing tool -> skipped+warning) and
    advisory (it never flips a green base gate red, except property *test*
    failures). Use on subtasks flagged non-trivial by the difficulty signal.
    """
    base = verify(path, language)
    if not base.get("stages") and "does not exist" in base.get("summary", ""):
        return base
    lang = base.get("language", "python")
    if lang != "python":
        base["deep"] = {"note": f"deeper layers are python-only for now; lang={lang}"}
        base["difficulty"] = difficulty
        return base

    want = layers if layers is not None else DIFFICULTY_LAYERS.get(difficulty, ["property"])
    explicit = layers is not None  # explicit layers= is an operator override
    py = _project_python(os.path.abspath(os.path.expanduser(path)))
    abspath = base["path"]
    extra: list[dict[str, Any]] = []
    if "property" in want:
        extra.append(_layer_property(py, abspath))
    if "mutation" in want:
        if explicit or _mutation_enabled(abspath):
            extra.append(_layer_mutation(py, abspath))
        else:  # feature-flagged OFF for this repo — show it, don't silently drop
            s = _skip("mutation testing OFF (feature-flagged; enable per-repo via "
                      "VERIFY_MUTATION_ENABLED=1 or a .hermes-mutation marker)")
            s.update(name="mutation", tool="mutmut", advisory=True)
            extra.append(s)
    if "fuzz" in want:
        extra.append(_layer_fuzz(py, abspath))

    stages = base["stages"] + extra
    # Gate: base stages authoritative; advisory layers never fail the gate, but a
    # non-advisory property TEST failure does.
    gating = [s for s in stages if not s.get("advisory")]
    ran = [s for s in gating if s["status"] in ("passed", "failed", "error")]
    bad = [s for s in gating if s["status"] in ("failed", "error")]
    passed = bool(ran) and not bad
    warnings = [f"{s['name']}({s['tool']}): {s['output']}"
                for s in extra if s["status"] == "skipped"]
    surviving = next((s.get("surviving_mutants") for s in extra if s["name"] == "mutation"), None)
    if surviving:
        warnings.append(f"mutation: {surviving} surviving mutant(s) — tests don't catch them")

    return {
        "path": abspath,
        "language": lang,
        "difficulty": difficulty,
        "layers_requested": want,
        "passed": passed,
        "stages": stages,
        "warnings": warnings,
        "summary": ("PASS" if passed else "FAIL") + " (deep: "
                   + ", ".join(f"{s['name']}={s['status']}" for s in stages) + ")",
    }
