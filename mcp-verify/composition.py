"""composition.py — Part A Phase 4: composition & protocol level.

Unit soundness does NOT compose into program soundness (LLM compositional verification
collapses, ~3.69% on DafnyComp). So we do NOT attempt whole-program proofs. Instead we
compose via the principled handoff (see docs/FORMAL_VS_INTEGRATION.md):

  • prove the pure critical KERNELS            (rungs 3-4: Kani / SMT contracts)
  • contract-check the module EDGES            (assume-guarantee; runtime monitors here)
  • integration/E2E-test the WIRING            (the pytest oracle)
  • model-check the PROTOCOLS / CONCURRENCY    (TLA+/Alloy on design; Loom/Shuttle on code)

This module provides the edge + composition rungs:
  1. edge_contract_monitor — assume-guarantee contracts at module edges. Where static proof
     is infeasible (the common case for generated code) they are installed as RUNTIME
     MONITORS: pre/post assertions wrapping the public functions, dependency-free (no
     deal/icontract needed). A verified callee's postcondition becomes the caller's
     assumption; the caller must establish the callee's precondition.
  2. stateful_test — cross-module state spanning files → a Hypothesis RuleBasedStateMachine
     (model states + transitions, find a violating SEQUENCE). Runs on the pytest oracle.
  3. concurrency_check — shared atomics/locks/lock-free → route Rust to Loom (exhaustive,
     bounded preemptions) / Shuttle (randomized). Degrades when the crates are absent.
  4. protocol_check — a multi-node protocol DESIGN → route to TLA+/Apalache or Alloy.
     Degrades when the tools are absent.

Sovereign/deterministic-first; never raises. Whole-program proof stays OFF the table.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import enhanced_verify as _ev
import formal_core as _fc

# concurrency / protocol design signals
_CONCURRENCY = ("atomic", "mutex", "rwlock", "arc<", "spawn", "thread", "channel", "lock(",
                "tokio", "rayon", "unsafe impl send", "unsafe impl sync", "compare_and_swap",
                "compare_exchange", "ordering::")
_PROTOCOL = ("consensus", "raft", "paxos", "quorum", "leader election", "replica", "two-phase",
             "2pc", "distributed", "gossip", "vector clock", "lamport", "byzantine", "commit log",
             "state machine replication", "protocol")


# 1 ── edge contracts as runtime monitors ─────────────────────────────────────
def make_monitor(pre, post):
    """A dependency-free runtime monitor: wrap a function so its precondition is checked on
    entry and its postcondition on exit. `pre(*a, **k) -> bool`, `post(result, *a, **k) -> bool`.
    A violation raises AssertionError at the edge — the assume-guarantee boundary in code."""
    def deco(fn):
        def wrapped(*a, **k):
            if pre is not None:
                assert pre(*a, **k), f"precondition violated calling {fn.__name__}{a}"
            r = fn(*a, **k)
            if post is not None:
                assert post(r, *a, **k), f"postcondition violated in {fn.__name__}{a} -> {r!r}"
            return r
        wrapped.__name__ = getattr(fn, "__name__", "wrapped")
        wrapped.__wrapped__ = fn
        return wrapped
    return deco


_MONITOR_SYS = (
    "For each public function, write a RUNTIME MONITOR as Python: a `@make_monitor(pre, post)` "
    "decorator where `pre(*a, **k)` returns the precondition and `post(result, *a, **k)` returns "
    "the postcondition (a real invariant relating output to inputs). Output ONLY a ```python "
    "block defining `MONITORS = {\"fn_name\": make_monitor(pre_lambda, post_lambda), ...}`."
)


def edge_contract_monitor(path: str, contracts: str | None = None) -> dict[str, Any]:
    """Build assume-guarantee runtime monitors for a module's public (edge) functions.
    `contracts` may be supplied (a MONITORS dict definition); otherwise the cheap pool
    proposes them. Returns {ok, monitored, functions, monitor_code, note}. The monitors are
    installable in production so a contract violation surfaces at the edge, not three modules
    away. Static proof (Kani/SMT) is preferred for pure kernels; this is the edge fallback."""
    p = Path(path)
    if not (p.is_file() and p.suffix == ".py"):
        return {"ok": False, "reason": "python single-file only"}
    src = p.read_text(errors="replace")
    funcs = _ev._public_functions(src)
    if not funcs:
        return {"ok": False, "reason": "no public (edge) functions"}
    code = contracts
    if code is None:
        try:
            import pool as _pool
        except Exception:  # noqa: BLE001
            _pool = None
        user = f"Module `{p.stem}` (edge fns: {', '.join(funcs)}):\n\n{src[:6000]}"
        gen = None
        if _pool and _pool.available():
            r = _pool.map_cheap([user], system=_MONITOR_SYS, temperature=0.1, max_tokens=1500)
            gen = r[0] if r else None
        elif _ev.VLLM_BASE_URL or os.environ.get("ESCALATION_MCP_URL"):
            gen = _ev._llm(_MONITOR_SYS + "\n\n" + user, 1500)
        code = _ev._extract_code(gen) if gen else None
    if not code or "make_monitor" not in code:
        return {"ok": True, "monitored": False, "functions": funcs,
                "note": "no model to propose edge contracts; install monitors manually or "
                        "rely on the integration tests for the wiring"}
    return {"ok": True, "monitored": True, "functions": funcs, "monitor_code": code,
            "note": "runtime monitors proposed; install at module edges (assume-guarantee). "
                    "Violations raise AssertionError at the boundary."}


# 2 ── stateful property testing (cross-module state) ─────────────────────────
def stateful_test(path: str, machine_code: str, max_examples: int = 100) -> dict[str, Any]:
    """Run a Hypothesis RuleBasedStateMachine (`machine_code`, which imports the module and
    defines a `TestMachine` state machine) to find a violating SEQUENCE of transitions —
    the right tool for cross-module state spanning files (single-call PBT can't reach it).
    Adjudicated by the pytest oracle. Four-value-ish: {status: pass|fail|error, counterexample}."""
    p = Path(path)
    if not (p.is_file() and p.suffix == ".py"):
        return {"status": "error", "reason": "python single-file only"}
    if "RuleBasedStateMachine" not in (machine_code or ""):
        return {"status": "error", "reason": "machine_code must define a RuleBasedStateMachine"}
    tmp = tempfile.mkdtemp(prefix="stateful-")
    try:
        (Path(tmp) / p.name).write_text(p.read_text(errors="replace"))
        (Path(tmp) / "conftest.py").write_text(
            "from hypothesis import settings\nsettings.register_profile('ci', max_examples=%d, "
            "deadline=None, stateful_step_count=50)\nsettings.load_profile('ci')\n" % int(max_examples))
        # the machine file must expose `TestMachine = MyMachine.TestCase` for pytest pickup
        (Path(tmp) / "test_stateful.py").write_text(machine_code)
        try:
            r = subprocess.run([_ev._py(), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                "test_stateful.py"], cwd=tmp, capture_output=True, text=True,
                               timeout=_ev.PROPERTY_TEST_TIMEOUT_S)
            out = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return {"status": "error", "reason": "stateful run timed out"}
        if "errors during collection" in out or " ERROR " in out:
            return {"status": "error", "summary": out[-800:]}
        if " failed" in out or "AssertionError" in out:
            seq = re.findall(r"state\.\w+\(.*\)", out)
            return {"status": "fail", "counterexample": seq[:20], "summary": out[-1000:]}
        if " passed" in out:
            return {"status": "pass", "summary": out[-400:]}
        return {"status": "error", "summary": out[-400:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# 3 ── concurrency: route to Loom/Shuttle (Rust) ──────────────────────────────
def _has_cargo_dep(root: Path, dep: str) -> bool:
    ct = root / "Cargo.toml"
    try:
        return ct.exists() and dep in ct.read_text()
    except OSError:
        return False


def concurrency_check(path: str) -> dict[str, Any]:
    """Detect shared-memory concurrency (atomics/locks/lock-free) and route Rust to Loom
    (exhaustive interleavings, preemptions bounded to 2-3) or Shuttle (randomized). Only
    triggers when concurrency primitives are present. Degrades (unknown + directive) when the
    crate isn't wired — Loom requires a `#[cfg(loom)]` test build, which we cannot synthesize
    blindly without breaking the build."""
    p = Path(path)
    root = p if p.is_dir() else p.parent
    blob = ""
    try:
        for f in ([p] if p.is_file() else list(root.rglob("*.rs"))[:50]):
            blob += f.read_text(errors="replace").lower()
    except OSError:
        pass
    concurrent = any(s in blob for s in _CONCURRENCY)
    if not concurrent:
        return {"result": "unknown", "reason": "no shared-memory concurrency primitives — "
                "concurrency model-checking not warranted", "concurrent": False}
    loom = _has_cargo_dep(root, "loom")
    shuttle = _has_cargo_dep(root, "shuttle")
    if not (loom or shuttle):
        return {"result": "unknown", "concurrent": True, "method": "loom/shuttle",
                "reason": "concurrent Rust detected but neither loom nor shuttle is a dev-"
                "dependency. Add loom (exhaustive, bound preemptions to 2-3) or shuttle "
                "(randomized) and a #[cfg(loom)] interleaving test; this router will then "
                "exercise it. (Kani cannot check concurrency.)",
                "directive": "add loom/shuttle dev-dependency + a #[cfg(loom)] model test"}
    tool = "loom" if loom else "shuttle"
    return {"result": "unknown", "concurrent": True, "method": tool,
            "reason": f"{tool} is wired; run `cargo test --cfg {tool}` interleaving tests — "
            "live execution of the model test is left to the project's test harness",
            "directive": f"cargo test (with {tool} cfg) the interleaving model"}


# 4 ── protocol/distributed DESIGN: route to TLA+/Alloy ───────────────────────
def protocol_check(path: str) -> dict[str, Any]:
    """Detect a multi-node protocol DESIGN (spec/design doc or code) and route to TLA+/
    Apalache or Alloy on the DESIGN (not the code — design bugs are cheapest there).
    Degrades (unknown + directive) when no model checker is installed."""
    p = Path(path)
    try:
        text = p.read_text(errors="replace").lower() if p.is_file() else ""
    except OSError:
        text = ""
    is_protocol = any(s in text for s in _PROTOCOL)
    has_tla = bool(shutil.which("tlc") or shutil.which("apalache-mc") or shutil.which("alloy"))
    if not is_protocol:
        return {"result": "unknown", "reason": "no multi-node/distributed-protocol signal — "
                "design model-checking not warranted", "protocol": False}
    if not has_tla:
        return {"result": "unknown", "protocol": True, "method": "tla+/alloy",
                "reason": "protocol design detected but no model checker (tlc/apalache/alloy) "
                "is installed. Model-check the DESIGN (safety + liveness) before implementing; "
                "specify it in TLA+/PlusCal or Alloy.",
                "directive": "write a TLA+/Alloy spec of the protocol and check safety+liveness"}
    return {"result": "unknown", "protocol": True, "method": "tla+/alloy",
            "reason": "model checker available — supply a .tla/.als spec to check",
            "directive": "provide the TLA+/Alloy spec path to model-check"}


def composition_stats() -> dict[str, Any]:
    return {"edge_monitors": "runtime (dependency-free) + static-preferred",
            "stateful_pbt": "hypothesis RuleBasedStateMachine",
            "concurrency": {"loom": False, "shuttle": False, "note": "route Rust only; Kani can't"},
            "protocol": {"tlc": bool(shutil.which("tlc")),
                         "apalache": bool(shutil.which("apalache-mc")),
                         "alloy": bool(shutil.which("alloy"))},
            "whole_program_proof": "OFF (does not compose; see docs/FORMAL_VS_INTEGRATION.md)"}
