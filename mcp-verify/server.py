"""mcp-verify — deterministic verification gate as an independent MCP server.

Transport: streamable-http on $MCP_VERIFY_PORT (default 9101), path /mcp.
Health:    GET /health (independent of the MCP protocol, for healthcheck.sh).

This process shares no mutable state with any other component. If it dies,
Hermes's MCP client simply reports the `verify` tool unavailable and the agent
degrades gracefully — it never takes the harness down.
"""

from __future__ import annotations

import asyncio
import functools
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import verify_core

try:
    import enhanced_verify  # M-Stage 3: model-generated property tests + mutation testing
except Exception:  # noqa: BLE001
    enhanced_verify = None  # type: ignore

try:
    import quality_core  # plan/execute Stage 4: advisory senior-review texture checks
except Exception:  # noqa: BLE001
    quality_core = None  # type: ignore

try:
    import formal_core  # Part A: the formal-verification ladder (verify_formal)
except Exception:  # noqa: BLE001
    formal_core = None  # type: ignore

# Opt-in: property generation adds wall time and the 35B can hallucinate properties,
# so the primary verify() gate runs it only when explicitly enabled.
ENABLE_PROPERTY_TEST = os.environ.get("ENABLE_PROPERTY_TEST", "false").strip().lower() in ("1", "true", "yes")

PORT = int(os.environ.get("MCP_VERIFY_PORT", "9101"))
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")

mcp = FastMCP(
    "mcp-verify",
    instructions=(
        "Deterministic code verification gate. Call verify(path) before "
        "declaring any coding task done; iterate until passed is true."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _threaded(fn):
    """Run a sync @mcp.tool() body on a worker thread so it never blocks the event
    loop. FastMCP (1.27) calls sync tool handlers directly in the single event-loop
    thread, so any long tool (running tests, indexing a repo, an LLM/cloud call,
    fetching+distilling a page) stalls EVERY other request — including GET /health,
    which is what made a live server show DOWN while it was actively serving the
    agent. asyncio.to_thread offloads the body so /health and concurrent calls stay
    responsive; functools.wraps preserves the typed signature for the schema, and
    the body runs in a thread with no running loop (so MCP-to-MCP asyncio.run works).
    """
    @functools.wraps(fn)
    async def _aw(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _aw

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "mcp-verify", "port": PORT})


@mcp.tool()
@_threaded
def verify(path: str, language: str = "auto") -> dict:
    """Run lint -> typecheck -> unit tests on a file or directory.

    Args:
        path: File or directory to verify.
        language: "auto" (default), "python", "ts", or "rust".

    Returns a structured result with a top-level `passed` boolean, a per-stage
    breakdown (lint/typecheck/tests, each passed|failed|skipped|error with
    diagnostics), and a human-readable `summary`. The gate is green only when at
    least one stage ran and none failed; missing tools are reported as skipped.

    When ENABLE_PROPERTY_TEST=true and the base gate passes on a single .py file, an
    advisory model-generated property_test pass runs and is attached under
    `property_test` (it never flips the base gate, but surfaces edge-case counterexamples).
    """
    result = verify_core.verify(path, language)
    if (ENABLE_PROPERTY_TEST and enhanced_verify is not None
            and isinstance(result, dict) and result.get("passed") and path.endswith(".py")):
        try:
            result["property_test"] = enhanced_verify.property_test(path)
        except Exception as e:  # noqa: BLE001 - advisory; never break the gate
            result["property_test"] = {"status": "error", "reason": str(e)[:200]}
    return result


@mcp.tool()
@_threaded
def property_test(path: str, max_examples: int = 100) -> dict:
    """Generate Hypothesis @given property tests for the module at `path` with the
    local model, run them, report failures + minimal counterexamples (M-Stage 3,
    arXiv:2510.09907). Filters hallucinated properties (fail to import/collect).
    Falsifiable invariants only — round-trips, bounds, ordering, agreement with a
    reference — never a restatement of the body. Time-bounded
    (PROPERTY_TEST_TIMEOUT_S=120); never raises. Use on core-logic functions after
    unit tests pass to find edge cases."""
    if enhanced_verify is None:
        return {"status": "skipped", "reason": "enhanced_verify unavailable"}
    return enhanced_verify.property_test(path, max_examples)


@mcp.tool()
@_threaded
def metamorphic_test(path: str, function: str, relation: str, input_strategy: str = "auto",
                     inverse_function: str = "", max_examples: int = 200) -> dict:
    """Metamorphic testing for code with NO ground-truth oracle (Phase 3.2): assert an
    invariant the function must satisfy over generated inputs. relation ∈
    idempotent | involution | round_trip (needs inverse_function) | permutation_invariant.
    input_strategy ∈ auto | int | text | list_int | list_text. Returns
    {status: pass|fail|error, counterexample}. Use when you can't write exact expected
    outputs but you know a property the code must obey."""
    if enhanced_verify is None:
        return {"status": "skipped", "reason": "enhanced_verify unavailable"}
    return enhanced_verify.metamorphic_test(path, function, relation, input_strategy,
                                            inverse_function, max_examples)


@mcp.tool()
@_threaded
def differential_test(path_a: str, function_a: str, path_b: str, function_b: str,
                      input_strategy: str = "auto", max_examples: int = 200) -> dict:
    """Differential testing (Phase 3.2): run two implementations on shared generated
    inputs and surface the first input where they DISAGREE — a likely-bug signal when
    you have two candidates (e.g. from best-of-N) but no oracle. Returns
    {status: agree|diverge|error, counterexample}."""
    if enhanced_verify is None:
        return {"status": "skipped", "reason": "enhanced_verify unavailable"}
    return enhanced_verify.differential_test(path_a, function_a, path_b, function_b,
                                             input_strategy, max_examples)


@mcp.tool()
@_threaded
def mutation_test(path: str, test_path: str) -> dict:
    """Mutate the module at `path` with mutmut, run `test_path` against each mutant,
    report kill_rate + surviving_mutants (M-Stage 3; Meta ACH arXiv:2501.12862 —
    mutation beats coverage). A SURVIVING mutant is a test gap: add a test that kills
    it before declaring done. Scoped to changed files in a git repo; time-bounded
    (MUTATION_TEST_TIMEOUT_S=300); skips gracefully if mutmut is absent; never raises."""
    if enhanced_verify is None:
        return {"status": "skipped", "reason": "enhanced_verify unavailable"}
    return enhanced_verify.mutation_test(path, test_path)


@mcp.tool()
@_threaded
def quick_check(path: str, language: str = "auto") -> dict:
    """Fast incremental check — lint + typecheck only, NO tests.

    Run this after EACH diff/search-replace edit for cheap well-formed-edit
    feedback (the edit-format discipline), then run the full verify() at subtask
    end. Same structured shape as verify(), with the test stage omitted.
    """
    return verify_core.quick_check(path, language)


@mcp.tool()
@_threaded
def deep_verify(path: str, language: str = "auto", difficulty: str = "medium",
                layers: list | None = None) -> dict:
    """Full gate PLUS difficulty-gated deeper layers — closes silent-wrong-answer.

    difficulty: easy -> base only; medium -> +property(hypothesis);
    hard -> +property, mutation(mutmut, reports surviving mutants), fuzz(atheris).
    Each extra layer is independently skippable (missing tool -> skipped+warning)
    and advisory (won't flip a green base gate red, except property test failures).
    Use on subtasks the difficulty classifier flags non-trivial; don't run
    mutation testing on trivial changes.
    """
    return verify_core.deep_verify(path, language, difficulty, layers)


@mcp.tool()
@_threaded
def quality_check(path: str) -> dict:
    """ADVISORY senior-review texture pass over a Python file — NOT a hard gate.

    Flags what the deterministic verify() gate does not: public functions/methods
    missing type annotations or docstrings, leftover TODO/FIXME/placeholder/stub
    markers, and bare `except:` clauses. Returns {ok, status:"advisory",
    annotations_missing, docstrings_missing, placeholders, bare_excepts, clean,
    summary}. It NEVER fails a build — keep verify()/deep_verify() as the hard
    pass/fail; quality_check raises output toward senior-review standard. Use on a
    file you just implemented (and the planner should specify these in the plan
    contract). Never raises; emits a quality_check span. Pairs with the
    workflow-quality-bar skill."""
    if quality_core is None:
        return {"ok": False, "reason": "quality_core unavailable"}
    return quality_core.quality_check(path)


@mcp.tool()
@_threaded
def verify_formal(path: str, language: str = "auto", task_spec: str = "",
                  sibling_files: list | None = None, agent_tests: str = "") -> dict:
    """The formal-verification LADDER (Part A) — returns ONE of four values:
    `verified{property,method}`, `counterexample{input,trace,mutant?}`, `unknown{reason}`,
    or `spec_rejected{reason}`.

    Runs cheapest→heaviest: Rung 0 compile/type (HARD gate — py_compile/mypy · cargo build
    · tsc --strict · go build+vet), Rung 1 lint (advisory), Rung 2 cheap-LLM-proposed
    property tests adjudicated by the pytest oracle and GUARDED by a mutation cross-check
    (break the module; if the properties still pass they're too weak → spec_rejected) plus
    a vacuity check. Python is complete at rung 2; Rust/TS/Go enforce rungs 0-1 and return
    `unknown` for rung 2 (honest — never a false `verified`).

    `agent_tests` (the agent's own passing tests, as source text) is the highest-trust
    oracle for property generation; `task_spec` (NL) is the lowest. Sovereign/deterministic-
    first: no model → rung 2 degrades to the smoke gate and returns `unknown`, never a
    fabricated pass. Never raises. Use as the ground-truth gate before checkpointing."""
    if formal_core is None:
        return {"result": "unknown", "reason": "formal_core unavailable"}
    return formal_core.verify_formal(path, language, task_spec or None, sibling_files,
                                     agent_tests or None)


@mcp.tool()
@_threaded
def criticality_classify(path: str, language: str = "auto") -> dict:
    """Classify a module's VERIFICATION criticality (Part A Phase 2). CRITICAL iff
    pure/deterministic AND high blast-radius (money/ledger, memory/unsafe, auth/credentials,
    data-integrity/persistence, or termination). Deterministic keyword/AST rules decide when
    they fire; a cheap-LLM fallback runs only when rules are silent and degrades to
    non-critical without a model. Returns {critical, dimensions, pure, concurrent, method}.
    The router uses this to send only critical, non-concurrent code to the heavy rungs
    (Kani/SMT) — never the whole codebase."""
    try:
        import criticality
    except Exception as e:  # noqa: BLE001
        return {"critical": False, "reason": f"criticality unavailable: {e}"}
    return criticality.criticality_classify(path, language)


@mcp.tool()
@_threaded
def formal_stats() -> dict:
    """Report the formal-verification ladder's configuration: whether a spec-generation
    model/pool is reachable, mutation budget, min kill-rate, and which languages have
    rungs 0-1 vs rung 2 wired."""
    if formal_core is None:
        return {"ok": False, "reason": "formal_core unavailable"}
    return formal_core.verify_formal_stats()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
