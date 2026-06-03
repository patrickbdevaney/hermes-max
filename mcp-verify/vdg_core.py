"""vdg_core.py — Phase 4: verification-driven generation + bounded reflection.

State the property/contract FIRST, then drive generation to satisfy it; on failure, run a
bounded self-critique loop that consumes the verify-gate COUNTEREXAMPLE as the critique
signal. This is the in-context analogue of verifiable-reward selection — NO weight updates,
NO new verifier: it reuses the formal-verification ladder + the existing verify gate as the
oracle, and the cheap pool only to AUTHOR the contract (degrading to the agent's own tests
as the oracle when no model is available).

Bounded to k=2-3 retries; stop on pass or k exhausted (never an unbounded loop). Preserves
the cheap/utility ratio: cheap authoring + reuse. Deterministic-first; never raises.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

import enhanced_verify as _ev
import verify_core

try:
    import pool as _pool
except Exception:  # noqa: BLE001
    _pool = None  # type: ignore

VDG_MAX_RETRIES = int(os.environ.get("VDG_MAX_RETRIES", "3"))

_CONTRACT_SYS = (
    "State a CONTRACT for the described function as ONE Hypothesis @given property that "
    "ASSERTS the postcondition on the output (a real invariant — bounds/ordering/round-trip/"
    "relationship to inputs). `from solution import *`. Output ONLY a ```python block.")


def author_contract(task_spec: str, signature: str = "") -> dict[str, Any]:
    """Author the property/contract BEFORE generation. Cheap pool / _llm; degrades to None
    (→ the loop uses the agent's own tests as the oracle — still verification-driven)."""
    user = f"Task: {task_spec[:1500]}"
    if signature:
        user += f"\nSignature: {signature}"
    gen = None
    if _pool and _pool.available():
        r = _pool.map_cheap([user], system=_CONTRACT_SYS, temperature=0.1, max_tokens=1200)
        gen = r[0] if r else None
    elif _ev.VLLM_BASE_URL or os.environ.get("ESCALATION_MCP_URL"):
        gen = _ev._llm(_CONTRACT_SYS + "\n\n" + user, 1200)
    if not gen:
        return {"contract": None, "method": "tests-as-oracle (no model to author a contract)"}
    return {"contract": _ev._extract_code(gen), "method": "cheap-pool-authored property"}


def _oracle(target_path: str, tests: dict[str, str], contract: Optional[str],
            task_spec: str) -> dict[str, Any]:
    """Run the verify oracle on a candidate: the formal ladder (with the authored contract as
    selection pressure) plus the agent's tests. Returns {ok, critique} — critique is the
    counterexample/diagnostic to feed the next reflection iteration."""
    tmp = tempfile.mkdtemp(prefix="vdg-")
    try:
        for rel, content in (tests or {}).items():
            fp = Path(tmp) / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
        main = Path(tmp) / "solution.py"
        main.write_text(Path(target_path).read_text(errors="replace"))
        if contract:
            (Path(tmp) / "test_contract.py").write_text(contract)
        # the deterministic gate (pytest etc.) is the trust boundary
        prev = os.environ.get("VERIFY_REQUIRE_PLAN")
        os.environ["VERIFY_REQUIRE_PLAN"] = "false"
        try:
            res = verify_core.verify(tmp, "python")
        finally:
            if prev is None:
                os.environ.pop("VERIFY_REQUIRE_PLAN", None)
            else:
                os.environ["VERIFY_REQUIRE_PLAN"] = prev
        ok = bool(res.get("passed"))
        crit = ""
        if not ok:
            for st in res.get("stages", []):
                if st.get("status") in ("failed", "error") and st.get("output"):
                    crit = st["output"][:800]
                    break
            crit = crit or res.get("summary", "")
        return {"ok": ok, "critique": crit, "summary": res.get("summary", "")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def reflect_loop(task_spec: str, generate: Callable[[str, str], str],
                 tests: dict[str, str], signature: str = "", k: int = 0) -> dict[str, Any]:
    """Property-first, bounded reflection. Authors a contract, then for up to k iterations:
    generate a candidate (the `generate(task_spec, critique)` callback supplies it — the
    agent/pool/local executor), run the verify oracle, and on failure feed the
    COUNTEREXAMPLE back as the critique for the next iteration. Stop on pass or k exhausted.
    Returns {ok, attempts, code, contract_method, critiques}."""
    k = k or VDG_MAX_RETRIES
    k = max(1, min(k, 5))
    if not tests:
        return {"ok": False, "attempts": 0, "reason": "no execution oracle (tests) — "
                "verification-driven generation needs a verifiable target"}
    authored = author_contract(task_spec, signature)
    contract = authored["contract"]
    critique = ""
    critiques: list[str] = []
    code = ""
    for i in range(k):
        try:
            code = generate(task_spec, critique) or ""
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "attempts": i, "reason": f"generator error: {type(e).__name__}"}
        tmp = tempfile.mkdtemp(prefix="vdg-cand-")
        try:
            cand = Path(tmp) / "solution.py"
            cand.write_text(code)
            res = _oracle(str(cand), tests, contract, task_spec)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if res["ok"]:
            return {"ok": True, "attempts": i + 1, "code": code,
                    "contract_method": authored["method"], "critiques": critiques}
        critique = res["critique"]                  # consume the counterexample as critique
        critiques.append(critique[:200])
    return {"ok": False, "attempts": k, "code": code, "contract_method": authored["method"],
            "critiques": critiques, "reason": f"k={k} reflection retries exhausted"}


def verify_driven_stats() -> dict[str, Any]:
    return {"max_retries": VDG_MAX_RETRIES,
            "model_for_contract": bool((_pool and _pool.available()) or _ev.VLLM_BASE_URL
                                       or os.environ.get("ESCALATION_MCP_URL")),
            "oracle": "formal ladder + verify gate (reused; no new verifier)"}
