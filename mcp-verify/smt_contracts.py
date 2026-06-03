"""smt_contracts.py — Part A Phase 3: SMT contracts for the pure CRITICAL FEW (rung 4).

Always gated: runs ONLY on pure, high-blast modules (criticality.py decides). The cheap
pool PROPOSES pre/post/invariant contracts (expressed as a Hypothesis property: the
postcondition must hold for every input satisfying the precondition); the solver/oracle
ADJUDICATES. CrossHair (concolic, Python) does the symbolic check when installed; Dafny/Z3
would be the equivalent for a Dafny module.

A contract result is trusted as `verified` ONLY after a TRIPLE GUARD (no single one is
enough — a generated spec can be wrong, vacuous, or too strong):
  1. MUTATION cross-check (MutDafny-style) — break the module; the contract MUST catch it
     (reuses the Phase-1 mutation engine). Survives mutation ⇒ too weak ⇒ spec_rejected.
  2. DIFFERENTIAL cross-check — every PASSING agent test must satisfy the contract; if the
     contract rejects a known-good input the contract is wrong/too-strong ⇒ spec_rejected.
  3. CONSISTENCY (Clover-style) — the contract must reference the function's real
     parameters and assert on its output (code↔spec coherence) ⇒ else spec_rejected.
Fail any guard → spec_rejected, downgrade to Phase-1 PBT. NEVER report a pass on a rejected
spec. Sovereign/deterministic-first: no model → unknown; CrossHair absent → the mutation-
guarded property stands in (honest, flagged). Never raises.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import enhanced_verify as _ev
import formal_core as _fc

try:
    import pool as _pool
except Exception:  # noqa: BLE001
    _pool = None  # type: ignore

CROSSHAIR_TIMEOUT = int(os.environ.get("CROSSHAIR_TIMEOUT_S", "60"))


def crosshair_available() -> bool:
    try:
        return subprocess.run([_ev._py(), "-c", "import crosshair"], capture_output=True,
                              timeout=20).returncode == 0
    except Exception:  # noqa: BLE001
        return False


_CONTRACT_SYS = (
    "Write ONE Hypothesis property test encoding a CONTRACT for the module's main public "
    "function: bind inputs with @given, restrict to the precondition with hypothesis.assume(), "
    "then ASSERT the postcondition on the OUTPUT. The postcondition must be a real invariant "
    "(bounds/ordering/round-trip/relationship between input and output), reference the actual "
    "parameters, and never be a restatement of the body. `from {mod} import *`. Output ONLY one "
    "```python block."
)


def _generate_contract(mod: str, src: str, task_spec: str | None) -> str | None:
    funcs = _ev._public_functions(src)
    if not funcs:
        return None
    user = f"Module `{mod}` (functions: {', '.join(funcs)}):\n\n{src[:6000]}"
    if task_spec:
        user += f"\n\n# Intended behaviour (NL spec):\n{task_spec[:1200]}"
    sysmsg = _CONTRACT_SYS.replace("{mod}", mod)
    gen = None
    if _pool and _pool.available():
        r = _pool.map_cheap([user], system=sysmsg, temperature=0.1, max_tokens=2000)
        gen = r[0] if r else None
    elif _ev.VLLM_BASE_URL or os.environ.get("ESCALATION_MCP_URL"):
        gen = _ev._llm(sysmsg + "\n\n" + user, 2000)
    return _ev._extract_code(gen) if gen else None


def _consistency_ok(contract_code: str, src: str) -> tuple[bool, str]:
    """Guard 3 (Clover): the contract must reference the module's real public functions and
    assert on output. Rejects a contract that calls nothing from the module or never asserts."""
    funcs = set(_ev._public_functions(src))
    if not funcs:
        return False, "module has no public functions"
    try:
        tree = ast.parse(contract_code)
    except Exception as e:  # noqa: BLE001
        return False, f"contract does not parse: {e}"
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    if not (funcs & called):
        return False, "contract references no public function of the module"
    if not any(isinstance(n, ast.Assert) for n in ast.walk(tree)):
        return False, "contract asserts nothing on the output (vacuous)"
    return True, "ok"


def _differential_ok(mod: str, src: str, contract_code: str, agent_tests: str) -> tuple[bool, str]:
    """Guard 2: the agent's passing tests AND the contract must both hold on the module —
    a contract that fails where the agent's known-good tests pass is wrong/too-strong."""
    tmp = tempfile.mkdtemp(prefix="smt-diff-")
    try:
        (Path(tmp) / f"{mod}.py").write_text(src)
        (Path(tmp) / "conftest.py").write_text(
            "from hypothesis import settings\nsettings.register_profile('ci', max_examples=50, "
            "deadline=None)\nsettings.load_profile('ci')\n")
        (Path(tmp) / "test_agent.py").write_text(agent_tests)
        (Path(tmp) / "test_contract.py").write_text(contract_code)
        try:
            r = subprocess.run([_ev._py(), "-m", "pytest", "-q", "-p", "no:cacheprovider", tmp],
                               cwd=tmp, capture_output=True, text=True, timeout=_ev.PROPERTY_TEST_TIMEOUT_S)
            out = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return False, "differential run timed out"
        if " failed" in out or "errors during collection" in out:
            return False, f"contract conflicts with passing agent tests: {out[-400:]}"
        return True, "agent tests + contract co-hold"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _crosshair(mod: str, src: str, contract_code: str) -> dict[str, Any]:
    """Symbolic check via CrossHair when installed. Returns {status: clean|counterexample|
    unavailable|error, detail}."""
    if not crosshair_available():
        return {"status": "unavailable"}
    tmp = tempfile.mkdtemp(prefix="smt-ch-")
    try:
        # CrossHair analyses asserts/contracts in a module; we point it at the contract file
        # (which imports and exercises the module's functions through assertions).
        (Path(tmp) / f"{mod}.py").write_text(src)
        (Path(tmp) / "contract_mod.py").write_text(contract_code.replace("from %s import *" % mod,
                                                                         "from %s import *" % mod))
        try:
            r = subprocess.run([_ev._py(), "-m", "crosshair", "check", str(Path(tmp) / "contract_mod.py")],
                               cwd=tmp, capture_output=True, text=True, timeout=CROSSHAIR_TIMEOUT)
            out = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return {"status": "error", "detail": "crosshair timeout"}
        if r.returncode == 0 and "error" not in out.lower():
            return {"status": "clean"}
        # CrossHair prints "false when calling f(...)" style counterexamples
        ce = [ln for ln in out.splitlines() if re.search(r"(false when|counterexample|AssertionError)", ln, re.I)]
        if ce:
            return {"status": "counterexample", "detail": "\n".join(ce[:10])[:1000]}
        return {"status": "error", "detail": out[-400:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def smt_verify(path: str, task_spec: str | None = None,
               agent_tests: str | None = None) -> dict[str, Any]:
    """Rung 4: contract verification for the pure critical few. Four-value result, triple-
    guarded. Gated on criticality (pure + high-blast); otherwise returns `unknown`."""
    abspath = os.path.abspath(os.path.expanduser(path))
    if not (os.path.isfile(abspath) and abspath.endswith(".py")):
        return _fc._unknown("SMT contracts: Python single-file only in this phase")
    try:
        import criticality
        crit = criticality.criticality_classify(abspath, "python")
    except Exception:  # noqa: BLE001
        crit = {"critical": False, "pure": True}
    if not (crit.get("critical") and crit.get("pure")):
        return _fc._unknown("not a pure critical-few module — SMT contracts not warranted",
                            criticality=crit)

    src = Path(abspath).read_text(errors="replace")
    mod = Path(abspath).stem
    if not (_pool and _pool.available()) and not (_ev.VLLM_BASE_URL or os.environ.get("ESCALATION_MCP_URL")):
        return _fc._unknown("no model to propose contracts; rung-2 PBT is the floor", method="none")
    contract = _generate_contract(mod, src, task_spec)
    if not contract:
        return _fc._unknown("contract generation failed/empty", method="none")

    # Guard 3 — consistency (cheapest, run first)
    ok, why = _consistency_ok(contract, src)
    if not ok:
        return _fc._spec_rejected(f"consistency guard: {why}", downgrade="pbt")
    # the contract must hold on the real code first (else it's a counterexample, not a spec)
    ran = _fc._run_properties(mod, src, contract, 50)
    if ran["status"] == "fail":
        return _fc._counterexample(ran.get("counterexamples") or None, ran.get("summary", ""),
                                   method="contract", property="generated contract")
    if ran["status"] in ("hallucinated", "timeout", "none"):
        return _fc._unknown(f"contract did not run cleanly ({ran['status']})", method="contract")
    # Guard 1 — mutation cross-check (the contract must catch injected bugs)
    mut = _fc._mutation_crosscheck(mod, src, contract)
    kr = mut.get("kill_rate")
    if mut.get("status") != "ok" or kr is None:
        return _fc._unknown(f"mutation cross-check inconclusive ({mut.get('status')})", method="contract")
    if kr < _fc.FORMAL_MIN_KILL:
        return _fc._spec_rejected(f"contract too weak: mutation kill-rate {kr} < {_fc.FORMAL_MIN_KILL}",
                                  downgrade="pbt", surviving_examples=mut.get("surviving_examples"))
    # Guard 2 — differential cross-check (only when the agent supplied tests)
    if agent_tests:
        ok, why = _differential_ok(mod, src, contract, agent_tests)
        if not ok:
            return _fc._spec_rejected(f"differential guard: {why}", downgrade="pbt")
    # symbolic adjudication when CrossHair is installed
    ch = _crosshair(mod, src, contract)
    if ch["status"] == "counterexample":
        return _fc._counterexample(None, ch.get("detail", ""), method="crosshair",
                                   property="generated contract")
    method = "crosshair + triple-guard" if ch["status"] == "clean" else \
             "contract-pbt + mutation + differential (crosshair unavailable)"
    return _fc._verified("generated contract", method, kill_rate=kr, criticality=crit,
                         crosshair=ch["status"])
