"""formal_core.py — the formal-verification LADDER (mcp-verify-formal, Part A Phase 1).

`verify_formal(path, language, task_spec?, sibling_files?, agent_tests?)` runs the
cheapest→heaviest ladder on the AGENT's working-directory output and returns EXACTLY ONE
of four values:
  verified{property, method}            — a strong property holds (mutation-guarded)
  counterexample{input, trace, mutant?} — concrete evidence it's broken (compile error,
                                          failing property, or a surviving mutant)
  unknown{reason}                       — tool/model incapacity; NEVER a hard fail
  spec_rejected{reason}                 — the generated spec was too weak/vacuous to trust

Governing principle: the cheap LLM only PROPOSES properties; only the compiler / pytest
oracle / mutation engine ADJUDICATES (mirrors mcp-search's "select by EXECUTION, never
self-judgment"). Two guards make the proposal trustworthy:
  • VACUITY check — a property with no assertion on the output constrains nothing → reject.
  • MUTATION CROSS-CHECK — break the module; if the generated properties still pass on the
    broken code they're too weak → spec_rejected, DOWNGRADE (PBT → metamorphic → smoke),
    never report a pass.

Per-language ladder (route by extension):
  Rung 0 compile/type  (deterministic, HARD gate): py_compile/mypy · cargo build · tsc
                       --strict · go build+vet. A non-compiling candidate fails first.
  Rung 1 lint          (deterministic, ADVISORY): ruff · clippy · eslint · staticcheck.
  Rung 2 PBT+mutation  (cheap LLM proposes, runner adjudicates): Python is complete here
                       (Hypothesis + mutation cross-check + vacuity). Rust/TS/Go run rungs
                       0-1 and return `unknown` for rung 2 until their PBT is wired (honest
                       degradation — never a false `verified`).

Sovereign/deterministic-first: no model → rung 2 degrades to smoke (the pytest oracle) and
returns `unknown`, never a fabricated pass. Never raises.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import enhanced_verify as _ev  # _llm, _public_functions, _extract_code, _Mutator, mutation_test
import verify_core

try:
    import pool as _pool  # cheap multi-provider fan-out for spec/property generation
except Exception:  # noqa: BLE001
    _pool = None  # type: ignore

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

# ── config ────────────────────────────────────────────────────────────────────
FORMAL_MUTANTS = int(os.environ.get("VERIFY_FORMAL_MUTANTS", "12"))     # cross-check budget
FORMAL_MIN_KILL = float(os.environ.get("VERIFY_FORMAL_MIN_KILL", "0.5"))  # below → spec too weak
FORMAL_MAX_EXAMPLES = int(os.environ.get("VERIFY_FORMAL_MAX_EXAMPLES", "100"))
FORMAL_GEN_TOKENS = int(os.environ.get("VERIFY_FORMAL_GEN_TOKENS", "4000"))
STAGE_TIMEOUT = int(os.environ.get("VERIFY_STAGE_TIMEOUT", "300"))


# ── four-value result constructors ────────────────────────────────────────────
def _verified(prop: str, method: str, **x: Any) -> dict[str, Any]:
    return {"result": "verified", "property": prop, "method": method, **x}


def _counterexample(inp: Any, trace: str, **x: Any) -> dict[str, Any]:
    return {"result": "counterexample", "input": inp, "trace": trace[:2000], **x}


def _unknown(reason: str, **x: Any) -> dict[str, Any]:
    return {"result": "unknown", "reason": reason, **x}


def _spec_rejected(reason: str, **x: Any) -> dict[str, Any]:
    return {"result": "spec_rejected", "reason": reason, **x}


def _model_available() -> bool:
    return bool((_pool and _pool.available()) or _ev.VLLM_BASE_URL or
                os.environ.get("ESCALATION_MCP_URL"))


# ── Rung 0+1: compile/type (HARD) + lint (ADVISORY), per language, no LLM ───────
def _run(cmd: list[str], cwd: str, timeout: int = STAGE_TIMEOUT) -> dict[str, Any]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"status": "skipped", "output": f"{cmd[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": f"timed out after {timeout}s"}
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    return {"status": "passed" if p.returncode == 0 else "failed",
            "returncode": p.returncode, "output": out[-3000:]}


def _has_module(py: str, mod: str) -> bool:
    try:
        return subprocess.run([py, "-c", f"import {mod}"], capture_output=True, timeout=20).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _compile_gate(path: str, language: str) -> dict[str, Any]:
    """Rung 0 (compile/type, hard) + Rung 1 (lint, advisory). Returns
    {stages:[...], compile_ok:bool|None, hard_fail:stage|None, advisories:[...]}.
    compile_ok is None when every hard rung was skipped (tool absent → `unknown`)."""
    p = Path(path)
    root = str(p if p.is_dir() else p.parent)
    target = str(p)
    stages: list[dict[str, Any]] = []
    hard: list[dict[str, Any]] = []   # rung-0 stages that actually ran
    advisories: list[dict[str, Any]] = []

    def add(name: str, tool: str, rung: int, res: dict[str, Any]) -> None:
        rec = {"name": name, "tool": tool, "rung": rung, **res}
        stages.append(rec)
        if rung == 0 and res["status"] in ("passed", "failed", "error"):
            hard.append(rec)
        if rung == 1 and res["status"] in ("failed", "error"):
            advisories.append(rec)

    if language == "python":
        py = verify_core._project_python(path)
        add("compile", "py_compile", 0, _run([py, "-m", "py_compile", target], root))
        if _has_module(py, "mypy"):
            add("typecheck", "mypy", 0, _run([py, "-m", "mypy", "--ignore-missing-imports", target], root))
        elif _has_module(py, "pyright"):
            add("typecheck", "pyright", 0, _run([py, "-m", "pyright", target], root))
        else:
            add("typecheck", "mypy|pyright", 0, {"status": "skipped", "output": "no type checker"})
        if _has_module(py, "ruff"):
            add("lint", "ruff", 1, _run([py, "-m", "ruff", "check", target], root))
        else:
            add("lint", "ruff", 1, {"status": "skipped", "output": "ruff absent"})
    elif language == "rust":
        cargo = shutil.which("cargo")
        if cargo:
            add("compile", "cargo build", 0, _run([cargo, "build"], root))
            add("lint", "clippy", 1, _run([cargo, "clippy", "--", "-D", "warnings"], root))
        else:
            add("compile", "cargo build", 0, {"status": "skipped", "output": "cargo absent"})
    elif language == "ts":
        root_p = Path(root)
        npx = shutil.which("npx")
        if npx and (root_p / "node_modules" / ".bin" / "tsc").exists():
            add("typecheck", "tsc --strict", 0, _run([npx, "--no-install", "tsc", "--noEmit", "--strict"], root))
        else:
            add("typecheck", "tsc", 0, {"status": "skipped", "output": "tsc absent"})
        if npx and (root_p / "node_modules" / ".bin" / "eslint").exists():
            add("lint", "eslint", 1, _run([npx, "--no-install", "eslint", target], root))
        else:
            add("lint", "eslint", 1, {"status": "skipped", "output": "eslint absent"})
    elif language == "go":
        go = shutil.which("go")
        if go:
            add("compile", "go build", 0, _run([go, "build", "./..."], root))
            add("vet", "go vet", 0, _run([go, "vet", "./..."], root))
        else:
            add("compile", "go build", 0, {"status": "skipped", "output": "go absent"})

    ran_hard = [s for s in hard]
    failed = next((s for s in ran_hard if s["status"] in ("failed", "error")), None)
    compile_ok = None if not ran_hard else (failed is None)
    return {"stages": stages, "compile_ok": compile_ok, "hard_fail": failed, "advisories": advisories}


# ── Rung 2 (Python): cheap-pool PBT + vacuity + mutation cross-check ────────────
_PROP_SYS = (
    "You generate Hypothesis @given property tests that try to BREAK the code. Each "
    "property must be FALSIFIABLE and ASSERT something about the OUTPUT (a real invariant: "
    "bounds, ordering, idempotence, round-trip, agreement with a slow reference) — never a "
    "restatement of the body, never a property with no assertion. Import with "
    "`from {mod} import *`. Output ONLY one ```python code block, no prose."
)


def _evidence_block(src: str, funcs: list[str], task_spec: str | None,
                    agent_tests: str | None) -> str:
    """Build the generation context in the spec's reliability order:
    (1) agent's own passing tests, (2) signature+types (the source), (3) docstring,
    (4) NL task spec. Highest-reliability evidence first."""
    parts = []
    if agent_tests:
        parts.append("# The agent's OWN passing tests (highest-trust oracle):\n" + agent_tests[:3000])
    parts.append(f"# Module under test (functions: {', '.join(funcs)}):\n{src[:8000]}")
    if task_spec:
        parts.append("# Natural-language task spec (lowest-trust; use only to disambiguate):\n"
                     + task_spec[:1500])
    return "\n\n".join(parts)


def _generate_properties(mod: str, src: str, funcs: list[str], task_spec: str | None,
                         agent_tests: str | None) -> str | None:
    sys_prompt = _PROP_SYS.replace("{mod}", mod)
    user = _evidence_block(src, funcs, task_spec, agent_tests)
    if _pool and _pool.available():
        outs = _pool.map_cheap([user], system=sys_prompt, temperature=0.1, max_tokens=FORMAL_GEN_TOKENS)
        gen = outs[0] if outs else None
    else:
        gen = _ev._llm(sys_prompt + "\n\n" + user, FORMAL_GEN_TOKENS)
    return _ev._extract_code(gen) if gen else None


def _vacuity(test_code: str) -> tuple[list[str], list[str]]:
    """Return (test_function_names, vacuous_names). Vacuous = a test with NO assert on
    output (an impl returning anything would pass) — the spec-vacuity guard."""
    try:
        tree = ast.parse(test_code)
    except Exception:  # noqa: BLE001
        return [], []
    tests, vacuous = [], []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            tests.append(n.name)
            if not any(isinstance(m, ast.Assert) for m in ast.walk(n)):
                vacuous.append(n.name)
    return tests, vacuous


def _run_properties(mod: str, src: str, test_code: str, max_examples: int) -> dict[str, Any]:
    """Run generated properties against the ORIGINAL module via the pytest oracle."""
    tmp = tempfile.mkdtemp(prefix="formal-prop-")
    try:
        (Path(tmp) / f"{mod}.py").write_text(src)
        (Path(tmp) / "conftest.py").write_text(
            "from hypothesis import settings\nsettings.register_profile('ci', max_examples=%d, "
            "deadline=None)\nsettings.load_profile('ci')\n" % int(max_examples))
        (Path(tmp) / "test_props.py").write_text(test_code)
        try:
            r = subprocess.run([_ev._py(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_props.py"],
                               cwd=tmp, capture_output=True, text=True, timeout=_ev.PROPERTY_TEST_TIMEOUT_S)
            out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "summary": "property run exceeded budget"}
        import re
        if "errors during collection" in out or " ERROR " in out:
            return {"status": "hallucinated", "summary": out[-1200:]}
        ce = re.findall(r"Falsifying example:\s*(.+)", out)
        failed = int(re.search(r"(\d+) failed", out).group(1)) if re.search(r"(\d+) failed", out) else 0
        passed = int(re.search(r"(\d+) passed", out).group(1)) if re.search(r"(\d+) passed", out) else 0
        status = "fail" if failed else ("pass" if passed else "none")
        return {"status": status, "passed": passed, "failed": failed,
                "counterexamples": [c.strip()[:200] for c in ce][:5], "summary": out[-1200:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mutation_crosscheck(mod: str, src: str, test_code: str) -> dict[str, Any]:
    """The spec-STRENGTH guard: write the module + generated properties to a temp dir and
    run the existing mutation engine — kill_rate is the fraction of injected bugs the
    properties catch. Low kill_rate ⇒ the properties are too weak to trust."""
    tmp = tempfile.mkdtemp(prefix="formal-mut-")
    try:
        mp = Path(tmp) / f"{mod}.py"
        tp = Path(tmp) / "test_props.py"
        mp.write_text(src)
        (Path(tmp) / "conftest.py").write_text(
            "from hypothesis import settings\nsettings.register_profile('ci', max_examples=%d, "
            "deadline=None)\nsettings.load_profile('ci')\n" % min(50, FORMAL_MAX_EXAMPLES))
        tp.write_text(test_code)
        old = os.environ.get("MUTATION_MAX_MUTANTS")
        os.environ["MUTATION_MAX_MUTANTS"] = str(FORMAL_MUTANTS)
        try:
            return _ev.mutation_test(str(mp), str(tp))
        finally:
            if old is None:
                os.environ.pop("MUTATION_MAX_MUTANTS", None)
            else:
                os.environ["MUTATION_MAX_MUTANTS"] = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _metamorphic_fallback(path: str, funcs: list[str]) -> dict[str, Any] | None:
    """Downgrade rung after spec_rejected: try a cheap deterministic metamorphic relation
    (idempotence f(f(x))==f(x)) on each single-arg public function. Returns a clear
    pass/fail dict, or None if nothing applicable (caller falls through to smoke)."""
    for fn in funcs:
        res = _ev.metamorphic_test(path, fn, "idempotent", input_strategy="auto", max_examples=50)
        if res.get("status") in ("pass", "fail"):
            return {"function": fn, **res}
    return None


def _smoke(path: str, language: str) -> dict[str, Any]:
    """The deterministic floor: the existing pytest/cargo/etc. gate (no LLM)."""
    prev = os.environ.get("VERIFY_REQUIRE_PLAN")
    os.environ["VERIFY_REQUIRE_PLAN"] = "false"
    try:
        return verify_core.verify(path, language)
    finally:
        if prev is None:
            os.environ.pop("VERIFY_REQUIRE_PLAN", None)
        else:
            os.environ["VERIFY_REQUIRE_PLAN"] = prev


def _rung2_python(path: str, src: str, task_spec: str | None,
                  agent_tests: str | None) -> dict[str, Any]:
    mod = Path(path).stem
    funcs = _ev._public_functions(src)
    if not funcs:
        return _unknown("no public functions to property-test", rungs=["0", "1"])
    if not _model_available():
        # deterministic-first: no model → cannot propose properties → smoke floor
        sm = _smoke(path, "python")
        if sm.get("passed"):
            return _unknown("no model for property generation; smoke (pytest) green",
                            method="smoke", rungs=["0", "1", "2:smoke"])
        return _counterexample(None, sm.get("summary", "")[:1500], method="smoke", stage="tests")

    test_code = _generate_properties(mod, src, funcs, task_spec, agent_tests)
    if not test_code:
        sm = _smoke(path, "python")
        return (_unknown("property generation failed; smoke green", method="smoke")
                if sm.get("passed") else
                _counterexample(None, sm.get("summary", "")[:1500], method="smoke", stage="tests"))

    test_names, vacuous = _vacuity(test_code)
    if test_names and len(vacuous) == len(test_names):
        meta = _metamorphic_fallback(path, funcs)
        return _spec_rejected("all generated properties are vacuous (no assertion on output)",
                              downgrade=("metamorphic" if meta else "smoke"),
                              metamorphic=meta, vacuous=vacuous)

    ran = _run_properties(mod, src, test_code, FORMAL_MAX_EXAMPLES)
    if ran["status"] == "fail":
        return _counterexample(ran.get("counterexamples") or None, ran.get("summary", ""),
                               method="hypothesis", property="generated PBT")
    if ran["status"] in ("hallucinated", "timeout", "none"):
        # can't trust a non-running property set → fall to smoke, proceed-with-flag
        sm = _smoke(path, "python")
        return (_unknown(f"properties did not run cleanly ({ran['status']}); smoke green",
                         method="smoke", detail=ran.get("summary", "")[:600])
                if sm.get("passed") else
                _counterexample(None, sm.get("summary", "")[:1500], method="smoke", stage="tests"))

    # properties PASS on the real code → now prove they're STRONG (mutation cross-check)
    mut = _mutation_crosscheck(mod, src, test_code)
    kr = mut.get("kill_rate")
    if mut.get("status") != "ok" or kr is None:
        return _unknown(f"mutation cross-check inconclusive ({mut.get('status')})",
                        method="hypothesis", mutation=mut)
    if kr < FORMAL_MIN_KILL:
        meta = _metamorphic_fallback(path, funcs)
        return _spec_rejected(
            f"properties too weak: mutation kill-rate {kr} < {FORMAL_MIN_KILL} "
            f"({mut.get('surviving_mutants')} mutant(s) survived the properties)",
            downgrade=("metamorphic" if meta else "smoke"), metamorphic=meta,
            surviving_examples=mut.get("surviving_examples"))
    return _verified("generated PBT", "hypothesis + mutation-crosscheck",
                     kill_rate=kr, properties=len(test_names), mutants_run=mut.get("mutants_run"))


# ── the public tool ─────────────────────────────────────────────────────────
def verify_formal(path: str, language: str = "auto", task_spec: str | None = None,
                  sibling_files: list[str] | None = None,
                  agent_tests: str | None = None) -> dict[str, Any]:
    """Run the formal-verification ladder on `path`. Returns one four-value result
    (verified | counterexample | unknown | spec_rejected) plus rung diagnostics.
    Deterministic-first; never raises. `agent_tests` (the agent's own passing tests,
    as source) is the highest-trust oracle for property generation; `task_spec` is the
    lowest. `sibling_files` reserved for Phase-4 cross-module context."""
    t0 = time.monotonic()
    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abspath):
        return {**_unknown(f"path does not exist: {abspath}"), "language": language}
    lang = language if language != "auto" else verify_core.detect_language(abspath)

    gate = _compile_gate(abspath, lang)
    base = {"path": abspath, "language": lang, "rung0_1": gate["stages"],
            "advisories": [a["tool"] for a in gate["advisories"]]}

    # Rung 0 HARD gate: a stage that ran and failed blocks before anything else.
    if gate["hard_fail"] is not None:
        hf = gate["hard_fail"]
        res = {**_counterexample(None, hf.get("output", ""), method=hf["tool"],
                                 stage=hf["name"]), **base}
        otel_emit.record("verify_formal", {"path": abspath, "language": lang,
                                            "result": "counterexample", "stage": hf["name"],
                                            "elapsed_s": round(time.monotonic() - t0, 2)})
        return res

    # Rung 2.
    if lang == "python":
        r2 = _rung2_python(abspath, Path(abspath).read_text(errors="replace") if Path(abspath).is_file() else "",
                           task_spec, agent_tests)
        # a directory (no single source) → smoke only
        if not Path(abspath).is_file():
            sm = _smoke(abspath, "python")
            r2 = (_unknown("directory target — ran smoke (pytest) only", method="smoke")
                  if sm.get("passed") else
                  _counterexample(None, sm.get("summary", "")[:1500], method="smoke", stage="tests"))
    elif lang == "rust" and gate["compile_ok"] is True:
        # Rung 3 (CRITICAL-only): route critical, non-concurrent Rust to Kani; else stop at
        # rungs 0-1. Concurrent or non-critical Rust is not sent to the solver.
        r2 = _rust_rung3(abspath)
    else:
        # Rungs 0-1 enforced for every language; rung-2 PBT for non-Python is honest TODO.
        if gate["compile_ok"] is True:
            r2 = _unknown(f"compile+lint passed; rung-2 PBT for {lang} not yet wired "
                          "(proptest/fast-check/gopter) — Phase-1 partial", method="compile-only")
        elif gate["compile_ok"] is None:
            r2 = _unknown(f"no {lang} toolchain available — nothing adjudicated", method="none")
        else:
            r2 = _unknown(f"{lang} compile/type indeterminate", method="none")

    result = {**r2, **base}
    otel_emit.record("verify_formal", {
        "path": abspath, "language": lang, "result": result["result"],
        "method": result.get("method"), "rungs": "0,1" + (",2" if lang == "python" else ""),
        "elapsed_s": round(time.monotonic() - t0, 2)})
    return result


def _rust_rung3(path: str) -> dict[str, Any]:
    """Rung 3 router for Rust: classify criticality; route critical, non-concurrent crates
    to Kani; degrade to proptest (honest `unknown` until proptest is wired) on Kani
    timeout/absence; leave non-critical Rust at rungs 0-1 (`unknown`)."""
    try:
        import criticality
        crit = criticality.criticality_classify(path, "rust")
    except Exception:  # noqa: BLE001
        crit = {"critical": False, "concurrent": False, "dimensions": []}
    if not crit.get("critical"):
        return _unknown("compile+lint passed; non-critical Rust → no solver rung "
                        "(rung-2 proptest not yet wired)", method="compile-only",
                        criticality=crit)
    try:
        import kani_verify
        kr = kani_verify.kani_verify(path, concurrent=bool(crit.get("concurrent")))
    except Exception as e:  # noqa: BLE001
        return _unknown(f"kani router error: {type(e).__name__}", method="none", criticality=crit)
    kind = kr.get("result")
    if kind == "verified":
        return _verified(kr.get("property", "kani"), kr.get("method", "kani"),
                         criticality=crit, harness_fns=kr.get("harness_fns"))
    if kind == "counterexample":
        return _counterexample(kr.get("input"), kr.get("trace", ""), method="kani",
                               criticality=crit)
    if kind == "degrade":
        # Kani unavailable/timeout → would fall to proptest; proptest-for-Rust not wired yet.
        return _unknown(f"critical Rust, but Kani degraded ({kr.get('reason')}) and proptest "
                        "for Rust is not yet wired", method="kani→proptest(unavailable)",
                        criticality=crit)
    return _unknown(kr.get("reason", "kani inconclusive"), method="kani", criticality=crit)


def compile_gate(path: str, language: str = "auto") -> dict[str, Any]:
    """FAST write-time gate: rungs 0-1 only (compile/type hard, lint advisory), no LLM,
    no PBT/mutation/tests. Returns a four-value result — `counterexample` on a hard
    compile/type failure, else `unknown` (clean-but-not-proven, or no toolchain). Cheap
    enough to fire on every file write; the full ladder (rung 2) runs at the done gate."""
    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abspath):
        return {**_unknown(f"path does not exist: {abspath}"), "language": language}
    lang = language if language != "auto" else verify_core.detect_language(abspath)
    g = _compile_gate(abspath, lang)
    base = {"language": lang, "rung0_1": g["stages"],
            "advisories": [a["tool"] for a in g["advisories"]]}
    if g["hard_fail"] is not None:
        hf = g["hard_fail"]
        return {**_counterexample(None, hf.get("output", ""), method=hf["tool"], stage=hf["name"]), **base}
    reason = ("compile/lint clean (rungs 0-1)" if g["compile_ok"] is True
              else f"no {lang} toolchain available")
    return {**_unknown(reason, compile_ok=g["compile_ok"]), **base}


def verify_formal_stats() -> dict[str, Any]:
    try:
        import kani_verify
        kani = kani_verify.kani_available()
    except Exception:  # noqa: BLE001
        kani = False
    return {"model_available": _model_available(),
            "pool": (_pool.pool_stats() if _pool else {"mode": "absent"}),
            "mutation_budget": FORMAL_MUTANTS, "min_kill_rate": FORMAL_MIN_KILL,
            "languages_rung01": ["python", "rust", "ts", "go"],
            "languages_rung2": ["python"],
            "rung3_kani": {"language": "rust", "available": kani,
                           "note": "critical, non-concurrent Rust only; degrades to proptest"}}
