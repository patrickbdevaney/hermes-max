# The formal-vs-integration handoff (Part A Phase 4)

**Unit soundness does NOT compose into program soundness.** LLM compositional verification
collapses (~3.69% on DafnyComp vs ~58%+ single-function), so hermes-max does **not** attempt
whole-program proofs. Instead the verification ladder owns the small high-value core and
hands the combinatorial glue to integration tests + model checking. This is the principled
division of labour the `mcp-verify-formal` rungs implement.

## Who proves what

| Layer | What it covers | Tool / rung | Why here |
|-------|----------------|-------------|----------|
| **Pure critical KERNELS** | money/ledger, auth, memory-safety, termination — pure + high-blast | Kani (rung 3, Rust) · SMT contracts (rung 4, Python/Dafny), triple-guarded | Single-function proof is where LLM+solver verification actually works; the payoff is highest on these. |
| **Module EDGES** | the assume-guarantee boundary between modules | `edge_contract_monitor` — static proof preferred; **runtime monitors** (pre/post asserts) where static proof is infeasible | A verified callee's postcondition becomes the caller's assumption; the caller must establish the callee's precondition. A violation surfaces AT the edge, not three modules away. |
| **Cross-module STATE** | state spanning files (a sequence of calls drives a bad state) | `stateful_test` — Hypothesis `RuleBasedStateMachine` (proptest state machines for Rust) | A single-call property can't reach a state reached only by a *sequence*; the state machine searches transition sequences. |
| **The WIRING** | how the proven pieces are glued together | the pytest/cargo integration + E2E tests (the existing oracle) | Integration tests own the combinatorial glue — cheaper and more reliable than a whole-program proof that won't converge. |
| **CONCURRENCY** | shared atomics / locks / lock-free | `concurrency_check` → Loom (exhaustive, bound preemptions to 2-3) / Shuttle (randomized) | Unit verification is useless for races; Kani has **no** concurrency support. Trigger only when the agent introduces shared-memory concurrency. |
| **PROTOCOLS / distributed design** | multi-node consensus/replication/ordering | `protocol_check` → TLA+/Apalache or Alloy on the **design** | Design-level bugs are cheapest to catch in a model checker before any code exists. |

## The rule of thumb
> Prove the pure critical kernels. Contract-check the edges (static where you can, runtime
> monitors where you can't). Integration/E2E-test the wiring. Model-check the protocols and
> concurrency. **Never** try to compose the kernel proofs into a whole-program proof.

## Deferred (A5 — do NOT build now)
Lean/Rocq proofs of general code (~7.8-27% vericoding), whole-program compositional proofs
(LLMs can't, ~3.69%), Verus beyond a small pilot. Promote only if compositional-verification
success crosses ~50% or Lean/Rocq code-vericoding crosses ~70% at acceptable latency.

## Sovereign reality
Loom/Shuttle (Rust crates), TLA+/Apalache/Alloy, CrossHair/Dafny, and the Python type
checkers are optional: when a tool is absent the corresponding rung **degrades to a
directive** (`unknown` + what to install/write) — never a false `verified`. The runtime-
monitor edge contracts and the stateful PBT are dependency-free (stdlib + Hypothesis) and run
everywhere.
