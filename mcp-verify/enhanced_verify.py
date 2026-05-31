"""enhanced_verify.py — property-based + mutation testing for the verify gate
(M-Stage 3). Evidence: PGS (arXiv:2506.18315) lifts fix rate 23.1%->53.8% over a
TDD baseline; Meta ACH (arXiv:2501.12862) shows mutation beats coverage; Anthropic's
agentic PBT (arXiv:2510.09907) found real bugs in NumPy/HuggingFace. Verification
accuracy (~87%) >> generation accuracy (~63%), so spending model budget on STRONGER
tests pays off.

Both tools are best-effort and time-bounded; they NEVER raise (a failure returns a
structured status). property_test is gated behind an explicit opt-in at the verify
layer because the 35B model can hallucinate properties and PBT/mutation add wall time.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "/model")
PROPERTY_TEST_TIMEOUT_S = float(os.environ.get("PROPERTY_TEST_TIMEOUT_S", "120"))
MUTATION_TEST_TIMEOUT_S = float(os.environ.get("MUTATION_TEST_TIMEOUT_S", "300"))
PROPERTY_GEN_MAX_TOKENS = int(os.environ.get("PROPERTY_GEN_MAX_TOKENS", "4000"))


def _py() -> str:
    """The verify venv interpreter (has hypothesis + pytest + mutmut)."""
    import sys
    return sys.executable


def _public_functions(src: str) -> list[str]:
    try:
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001
        return []
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]


ESCALATION_MCP_URL = os.environ.get(
    "ESCALATION_MCP_URL", f"http://127.0.0.1:{os.environ.get('MCP_ESCALATION_PORT', '9105')}/mcp")


def _steer(prompt: str, max_tokens: int) -> str | None:
    """Generate via the conductor STEER tier (cheap cloud, e.g. DeepSeek) — FAST and
    good at code, unlike the local 35B reasoning model which burns ~100s+ of hidden
    thinking per call. None if steer is off/capped/unreachable."""
    async def _call():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(ESCALATION_MCP_URL) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("conductor_steer", {"prompt": prompt, "max_tokens": max_tokens})
                txt = getattr(res.content[0], "text", "") if res.content else ""
                d = res.structuredContent or (json.loads(txt) if txt else {})
                return d.get("result", d) if isinstance(d, dict) else {}
    try:
        import asyncio
        d = asyncio.run(asyncio.wait_for(_call(), timeout=90))
        if isinstance(d, dict) and not d.get("proceed_local") and d.get("content"):
            return str(d["content"]).strip()
    except Exception:  # noqa: BLE001
        return None
    return None


def _llm(prompt: str, max_tokens: int) -> str | None:
    # Prefer the fast steer tier; fall back to the local model (slow but sovereign).
    out = _steer(prompt, max_tokens)
    if out:
        return out
    if not VLLM_BASE_URL:
        return None
    try:
        with httpx.Client(timeout=PROPERTY_TEST_TIMEOUT_S) as c:
            r = c.post(f"{VLLM_BASE_URL}/chat/completions", json={
                "model": VLLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1, "max_tokens": max_tokens})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content")
        return content.strip() if content else None
    except Exception:  # noqa: BLE001
        return None


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


_PROP_SYS = (
    "Generate 3-5 Hypothesis @given property tests for the functions in this module. "
    "Each property must be FALSIFIABLE and testable — a real invariant (e.g. "
    "idempotence, round-trip, bounds, ordering, agreement with a slow reference), NOT "
    "a restatement of the function body. Import the functions with `from {mod} import *`. "
    "Output ONLY a complete Python test file in one ```python code block, no prose."
)


def property_test(path: str, max_examples: int = 100) -> dict[str, Any]:
    """Generate Hypothesis property tests for the module at `path`, run them, and
    report failures + minimal counterexamples. Filters hallucinated properties
    (those that fail to import/collect). Time-bounded; never raises."""
    p = Path(path)
    if not p.exists() or p.suffix != ".py":
        return {"status": "skipped", "reason": f"not a python file: {path}"}
    src = p.read_text(errors="replace")
    funcs = _public_functions(src)
    if not funcs:
        return {"status": "skipped", "reason": "no public functions to test"}
    if not VLLM_BASE_URL:
        return {"status": "skipped", "reason": "VLLM_BASE_URL unset — cannot generate properties"}

    mod = p.stem
    prompt = (_PROP_SYS.replace("{mod}", mod)
              + f"\n\nMODULE `{mod}.py` (functions: {', '.join(funcs)}):\n\n{src[:8000]}")
    gen = _llm(prompt, PROPERTY_GEN_MAX_TOKENS)
    if not gen:
        return {"status": "error", "reason": "property generation failed (model unreachable/empty)"}
    test_code = _extract_code(gen)
    # count @given properties generated (best-effort)
    props_generated = len(re.findall(r"@given", test_code)) or len(re.findall(r"def test_", test_code))

    tmp = tempfile.mkdtemp(prefix="proptest-")
    try:
        (Path(tmp) / f"{mod}.py").write_text(src)
        (Path(tmp) / "conftest.py").write_text(
            "from hypothesis import settings\nsettings.register_profile('ci', max_examples=%d)\n"
            "settings.load_profile('ci')\n" % int(max_examples))
        (Path(tmp) / "test_props.py").write_text(test_code)
        try:
            r = subprocess.run([_py(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_props.py"],
                               cwd=tmp, capture_output=True, text=True, timeout=PROPERTY_TEST_TIMEOUT_S)
            out = (r.stdout or "") + (r.returncode and ("\n" + (r.stderr or "")) or "")
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "properties_generated": props_generated,
                    "reason": f"property run exceeded {PROPERTY_TEST_TIMEOUT_S}s"}
        # hallucinated = collection/import errors (test references nonexistent API)
        filtered = len(re.findall(r"\bERROR\b", out)) + (1 if "errors during collection" in out else 0)
        counterexamples = re.findall(r"Falsifying example:\s*(.+)", out)
        m = re.search(r"(\d+) failed", out)
        failures = int(m.group(1)) if m else 0
        passed = int(re.search(r"(\d+) passed", out).group(1)) if re.search(r"(\d+) passed", out) else 0
        status = "fail" if failures else ("pass" if passed else "no_runnable_properties")
        return {"status": status, "properties_generated": props_generated,
                "properties_ran": passed + failures, "failures": failures,
                "counterexamples": [c.strip()[:200] for c in counterexamples][:10],
                "filtered_hallucinations": filtered,
                "summary": out[-1500:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _load_module(path: str):
    import importlib.util
    p = Path(path)
    spec = importlib.util.spec_from_file_location(p.stem, str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # may raise -> caller handles
    return mod


def _strategy(name: str):
    import hypothesis.strategies as st
    return {
        "int": st.integers(min_value=-10000, max_value=10000),
        "text": st.text(max_size=200),
        "list_int": st.lists(st.integers(min_value=-1000, max_value=1000), max_size=50),
        "list_text": st.lists(st.text(max_size=20), max_size=30),
    }.get(name, st.lists(st.integers(min_value=-1000, max_value=1000), max_size=50))


def _run_given(strategy, check) -> tuple[str, str | None]:
    """Run `check(x)` over Hypothesis-generated `x`; return (held|violated, counterexample)."""
    from hypothesis import given, settings
    max_examples = int(os.environ.get("METAMORPHIC_MAX_EXAMPLES", "200"))
    box: dict = {}

    @settings(max_examples=max_examples, deadline=None)
    @given(strategy)
    def _t(x):
        ok = check(x)
        if not ok:
            box["x"] = x
        assert ok, f"relation violated at x={x!r}"
    try:
        _t()
        return "held", None
    except AssertionError as e:
        m = re.search(r"Falsifying example:.*", str(e))
        return "violated", (m.group(0) if m else str(e))[:300]
    except Exception as e:  # noqa: BLE001
        return "error", f"{type(e).__name__}: {e}"[:300]


def metamorphic_test(path: str, function: str, relation: str, input_strategy: str = "auto",
                     inverse_function: str = "", max_examples: int = 200) -> dict[str, Any]:
    """Metamorphic testing for code WITHOUT a ground-truth oracle: assert an
    INVARIANT the function must satisfy over generated inputs (Phase 3.2). relation:
      idempotent           f(f(x)) == f(x)        (e.g. sort, normalize)
      involution           f(f(x)) == x           (e.g. reverse, negate)
      round_trip           inverse(f(x)) == x     (needs inverse_function; e.g. encode/decode)
      permutation_invariant f(perm(x)) == f(x)    (x a list; e.g. sum, sorted, max)
    Returns {status: pass|fail|error, relation, counterexample}. In-process, bounded;
    never raises."""
    try:
        mod = _load_module(path)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"import failed: {type(e).__name__}: {e}"}
    fn = getattr(mod, function, None)
    if not callable(fn):
        return {"status": "error", "reason": f"no callable '{function}' in {path}"}
    os.environ["METAMORPHIC_MAX_EXAMPLES"] = str(max_examples)
    strat = _strategy("list_int" if input_strategy == "auto" else input_strategy)

    if relation == "idempotent":
        check = lambda x: fn(fn(x)) == fn(x)  # noqa: E731
    elif relation == "involution":
        check = lambda x: fn(fn(x)) == x  # noqa: E731
    elif relation == "permutation_invariant":
        import random
        def check(x):  # noqa: E306
            y = list(x); random.Random(len(y)).shuffle(y)
            return fn(list(x)) == fn(y)
    elif relation == "round_trip":
        inv = getattr(mod, inverse_function, None)
        if not callable(inv):
            return {"status": "error", "reason": f"round_trip needs inverse_function (got '{inverse_function}')"}
        check = lambda x: inv(fn(x)) == x  # noqa: E731
    else:
        return {"status": "error", "reason": f"unknown relation '{relation}' "
                "(idempotent|involution|round_trip|permutation_invariant)"}

    verdict, cx = _run_given(strat, check)
    status = {"held": "pass", "violated": "fail", "error": "error"}[verdict]
    return {"status": status, "relation": relation, "function": function,
            "counterexample": cx,
            "note": ("invariant holds over %d examples" % max_examples if status == "pass"
                     else "invariant VIOLATED — the implementation (or the chosen relation) is wrong"
                     if status == "fail" else "could not evaluate")}


def differential_test(path_a: str, function_a: str, path_b: str, function_b: str,
                      input_strategy: str = "auto", max_examples: int = 200) -> dict[str, Any]:
    """Differential testing: run two implementations on shared generated inputs and
    surface the first input where they DISAGREE (a likely-bug signal — e.g. two
    best-of-N candidates). Returns {status: agree|diverge|error, counterexample}.
    In-process, bounded; never raises."""
    try:
        fa = getattr(_load_module(path_a), function_a, None)
        fb = getattr(_load_module(path_b), function_b, None)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"import failed: {type(e).__name__}: {e}"}
    if not callable(fa) or not callable(fb):
        return {"status": "error", "reason": "one or both functions not found/callable"}
    os.environ["METAMORPHIC_MAX_EXAMPLES"] = str(max_examples)
    strat = _strategy("list_int" if input_strategy == "auto" else input_strategy)

    def check(x):
        try:
            return fa(x) == fb(x)
        except Exception:  # noqa: BLE001 - an exception in one but not the other IS a divergence
            try:
                fa(x); fb(x); return True
            except Exception:
                return False
    verdict, cx = _run_given(strat, check)
    status = {"held": "agree", "violated": "diverge", "error": "error"}[verdict]
    return {"status": status, "function_a": function_a, "function_b": function_b,
            "counterexample": cx,
            "note": ("implementations agree over %d examples" % max_examples if status == "agree"
                     else "implementations DIVERGE — at least one is likely buggy at the counterexample"
                     if status == "diverge" else "could not evaluate")}


def _changed_py_files(repo: str) -> list[str]:
    try:
        r = subprocess.run(["git", "-C", repo, "diff", "--name-only", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        return [f for f in r.stdout.split("\n") if f.strip().endswith(".py")]
    except Exception:  # noqa: BLE001
        return []


# Mutation operators — small, high-signal set (mutmut 3.x's test-runner integration
# is brittle in a throwaway temp project and reports all-survived, which is worse
# than useless; this self-contained AST engine actually runs the test suite against
# each mutant and so reports a TRUE kill rate, version-independently).
import copy as _copy

_BINOP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult,
          ast.Mod: ast.Mult, ast.FloorDiv: ast.Mult, ast.Pow: ast.Mult}
_CMP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.Gt: ast.LtE,
        ast.LtE: ast.Gt, ast.GtE: ast.Lt}
_BOOL = {ast.And: ast.Or, ast.Or: ast.And}


class _Mutator(ast.NodeTransformer):
    """Applies exactly ONE mutation (at ordinal `target`) per instantiation; with
    target=-1 it applies nothing and just counts mutation points in `self.i`."""
    def __init__(self, target: int):
        self.target = target
        self.i = -1
        self.applied = None

    def _hit(self, label: str) -> bool:
        self.i += 1
        if self.i == self.target:
            self.applied = label
            return True
        return False

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if type(node.op) in _BINOP and self._hit(f"binop:{type(node.op).__name__}"):
            node.op = _BINOP[type(node.op)]()
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        node.ops = [(_CMP[type(op)]() if (type(op) in _CMP and self._hit(f"cmp:{type(op).__name__}")) else op)
                    for op in node.ops]
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        if type(node.op) in _BOOL and self._hit(f"bool:{type(node.op).__name__}"):
            node.op = _BOOL[type(node.op)]()
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            if self._hit(f"const:bool:{node.value}"):
                node.value = not node.value
        elif isinstance(node.value, int):
            if self._hit(f"const:int:{node.value}"):
                node.value = node.value + 1
        return node


def mutation_test(path: str, test_path: str) -> dict[str, Any]:
    """Mutate the module at `path` (one operator/constant flip per mutant), run
    `test_path` against each mutant, and report kill_rate + surviving_mutants — a
    survivor is a test that fails to catch a real bug (a test gap). The original
    test suite must be GREEN first (else kill detection is meaningless). Bounded by
    a mutant cap (MUTATION_MAX_MUTANTS=60) and the wall-clock budget; never raises."""
    mod, tst = Path(path), Path(test_path)
    if not mod.exists() or not tst.exists():
        return {"status": "skipped", "reason": f"module or test missing ({path} / {test_path})"}
    try:
        src = mod.read_text(errors="replace")
        ast.parse(src)
    except Exception as e:  # noqa: BLE001
        return {"status": "skipped", "reason": f"cannot parse module: {e}"}

    cap = int(os.environ.get("MUTATION_MAX_MUTANTS", "60"))
    deadline = __import__("time").monotonic() + MUTATION_TEST_TIMEOUT_S

    def _run_tests(workdir: str) -> int:
        try:
            r = subprocess.run([_py(), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tst.name],
                               cwd=workdir, capture_output=True, text=True,
                               timeout=max(5, deadline - __import__("time").monotonic()))
            return r.returncode
        except Exception:  # noqa: BLE001
            return 1  # treat a crash/timeout as a failed run (mutant killed)

    # 0) baseline: the test suite must be green on the ORIGINAL module
    base = tempfile.mkdtemp(prefix="mut-base-")
    try:
        (Path(base) / mod.name).write_text(src)
        shutil.copy2(tst, Path(base) / tst.name)
        if _run_tests(base) != 0:
            return {"status": "test_red",
                    "reason": "the test suite is not green on the unmutated module — fix tests first"}
    finally:
        shutil.rmtree(base, ignore_errors=True)

    total = _Mutator(-1)
    total.visit(ast.parse(src))
    n_points = total.i + 1
    if n_points <= 0:
        return {"status": "no_mutants", "mutants_generated": 0, "reason": "no mutable operators/constants"}

    targets = list(range(n_points))
    if n_points > cap:  # evenly sample to stay within budget
        step = n_points / cap
        targets = sorted({int(j * step) for j in range(cap)})

    killed = 0
    survivors: list[str] = []
    ran = 0
    for t in targets:
        if __import__("time").monotonic() >= deadline:
            break
        m = _Mutator(t)
        try:
            mutated = ast.fix_missing_locations(m.visit(ast.parse(src)))
            code = ast.unparse(mutated)
        except Exception:  # noqa: BLE001
            continue
        if m.applied is None or code == src:
            continue
        ran += 1
        wd = tempfile.mkdtemp(prefix="mut-")
        try:
            (Path(wd) / mod.name).write_text(code)
            shutil.copy2(tst, Path(wd) / tst.name)
            if _run_tests(wd) != 0:
                killed += 1            # test caught the mutation -> killed
            else:
                survivors.append(m.applied)  # test passed on a bug -> survived (gap)
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    kill_rate = round(killed / ran, 3) if ran else None
    n_surv = ran - killed
    return {"status": "ok", "engine": "ast", "mutation_points": n_points,
            "mutants_run": ran, "mutants_killed": killed, "surviving_mutants": n_surv,
            "kill_rate": kill_rate,
            "surviving_examples": survivors[:10],
            "note": ("all mutants killed — the tests genuinely catch bugs" if not n_surv
                     else f"{n_surv} mutant(s) survived = test gaps; add tests that kill them"),
            "timed_out": __import__("time").monotonic() >= deadline}
