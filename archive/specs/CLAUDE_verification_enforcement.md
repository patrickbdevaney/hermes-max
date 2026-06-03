# CLAUDE_verification_enforcement.md — Formal Verification Ladder + MCP Lifecycle Enforcement

**For:** Claude Code, in `~/hermes-max/` on branch `inference-fabric`.
**Two coupled goals, one directive:**
- **Part A —** build the full viable formal-verification ladder as `mcp-verify-formal`
  (an extension of the existing mcp-verify), enforced at deterministic checkpoints.
- **Part B —** audit and wire the conductor lifecycle hooks so the high-value MCPs are
  ENFORCED at the right point in the agent loop instead of left to the driver LLM's
  discretion (it skips them when it "feels smart enough," wasting the engineering).

**Source of truth:** the formal-verification report (compass_artifact_wf-4e1b6c93) and
the MCP-enforcement analysis. Build AGAINST the existing system; do NOT rewrite it, do
NOT import a framework. Preserve every invariant: sovereign / deterministic-first
(every LLM step degrades to a non-LLM check; the kernel/solver check is the trust
boundary), multi-language, public-repo-clean (spec/property/proof generation on the
cheap Groq/Cerebras/local pool, no keys committed), each capability one independent
process, smoke green after every phase, quality-conscious commits per phase.

Existing pieces to reuse (do not duplicate): mcp-verify (port 9101, the pytest
execution oracle in isolated dirs), mcp-search (9108, verifier-guided best-of-N,
select by EXECUTION never self-judgment), the conductor/mcp-escalation (9105) with
register_hook on pre_llm_call / post_llm_call / post_tool_call / step, the cheap
inference fabric (lib/inference role API), mcp-checkpoint (9106), mcp-watchdog,
mcp-knowledge-graph (9103), mcp-codebase-rag (9102).

================================================================================
# PART A — THE FORMAL VERIFICATION LADDER (mcp-verify-formal)
================================================================================

## A0. The governing principle (read first)
The verification TARGET is the agent's working-directory output (Python, Rust, TS, Go,
others) — NOT hermes-max's own source. The ladder, cheapest→heaviest, with ROI for
unattended LLM-driven multi-language use falling steeply down the list:
```
Rung 0  compiler + type check        (free, deterministic, broad)      ENFORCE ALWAYS
Rung 1  static analysis / linters    (free, deterministic)             ENFORCE ALWAYS
Rung 2  property-based + metamorphic  (cheap, multi-lang, actionable)   ENFORCE ALWAYS  ← workhorse
Rung 3  bounded model checking (Kani) (no proof burden, Rust)          CRITICAL-ONLY
Rung 4  SMT contracts (Dafny/CrossHair) (spec-gen is the risk)         CRITICAL-FEW
Rung 5  interactive proof (Lean/Rocq)  (research-grade for code)       DO NOT build now
```
Two hard truths from the research that constrain the build:
1. **Spec generation is the weak link** — an LLM writing a contract/property can produce
   a plausible-but-WRONG or VACUOUS spec → a proof of the wrong property → false
   confidence. The ONLY reliable guard is MUTATION CROSS-CHECK (break the code; if the
   spec/property still passes, the spec is too weak → reject) plus differential
   cross-check against the agent's own tests. `spec_rejected` is a first-class result.
2. **Unit soundness does NOT compose into program soundness** — LLM compositional
   verification collapses (~3.69% on DafnyComp vs ~58%+ single-function). Do NOT attempt
   whole-program proofs. Compose via assume-guarantee CONTRACTS at module edges +
   integration tests + protocol model-checking, never via bigger proofs.

The deterministic checker is the trust boundary: the cheap LLM only PROPOSES
specs/properties/harnesses; only the compiler/solver/runner ADJUDICATES (mirrors the
existing "select by EXECUTION, never self-judgment" rule).

## A1. PHASE 1 — the near-free, multi-language, enforced base (BUILD FIRST)
Rungs 0-2, on every generated module. This is the highest-ROI rung and the only one
with 2025-2026 evidence of catching real bugs unattended at scale.

Extend mcp-verify into **mcp-verify-formal** (same server or a sibling on a new port —
prefer extending mcp-verify's tool surface to avoid a new process unless port hygiene
demands one). New tool: `verify_formal(path, language, task_spec?, sibling_files?, agent_tests?)`
returning exactly one of: `verified{property,method}` | `counterexample{input,trace,mutant?}`
| `unknown{reason}` | `spec_rejected{reason}`.

Per-language Phase-1 ladder (route by extension):
- **Rung 0 (deterministic, hard-fail gate, no LLM):** Rust → `cargo build` + borrow-check;
  TS → `tsc --strict`; Go → `go build` + `go vet`; Python → `mypy`/`pyright`. A
  non-compiling / type-erroring candidate fails BEFORE anything else runs.
- **Rung 1 (deterministic, no LLM):** clippy (Rust), staticcheck (Go), ruff+pyright
  (Python), eslint (TS). Advisory warnings surfaced, hard errors gate.
- **Rung 2 (cheap LLM proposes, runner adjudicates):**
  - The cheap pool generates property-based tests from, in reliability order, (1) the
    agent's own passing tests, (2) the signature+types, (3) the docstring, (4) the NL
    task spec. Tools: Hypothesis (Python), proptest/quickcheck (Rust), fast-check (TS),
    gopter (Go). For no-oracle code, generate METAMORPHIC relations (idempotence,
    round-trip serialize∘deserialize=id, permutation-invariance).
  - Run them via the existing pytest-style oracle in isolated dirs.
  - **MUTATION CROSS-CHECK (the spec-strength guard, mandatory):** mutate the module
    (a small set of operators — flip comparisons, off-by-one, drop a statement, swap
    operands); re-run the generated properties. If the properties still pass on mutated
    (broken) code, they're too weak → return `spec_rejected`, DOWNGRADE to metamorphic→
    smoke, never report a pass. Bound to a handful of mutants per module (seconds-minutes).
  - **VACUITY check:** reject properties that don't constrain output (an impl returning
    anything would pass) and preconditions so strong they're never exercised.

Degradation per language: PBT → metamorphic → smoke (the pytest oracle). The MCP ALWAYS
returns some signal; it never hard-fails on tool incapacity (that's `unknown`).

**Phase-1 DoD:** verify_formal runs the compile/type/lint gate + cheap-pool-generated PBT
+ mutation cross-check across Python/Rust/TS/Go; returns the four-value result; mutation
guard demonstrably flips a too-weak property to `spec_rejected`; degrades to metamorphic/
smoke when no property fits. VALIDATE with a seeded-bug corpus (known-buggy generated
snippets in all four languages): catch-rate strictly higher than the pytest-only gate.
Committed.

## A2. PHASE 2 — Kani for Rust critical modules + the criticality classifier
Rung 3, critical-only. Add the criticality classifier (Part B reuses it too):
- **criticality_classify(module)** — deterministic rules first, cheap-LLM fallback:
  CRITICAL iff pure/deterministic AND high blast-radius — touches money/ledger,
  memory/`unsafe`, auth/credentials/permissions, data integrity/persistence, or has
  loop/recursion termination concerns. Cheap signals: imports (crypto, decimal, db,
  unsafe), names/docstrings (transfer, balance, auth, delete), loops/recursion, `unsafe`
  blocks, public-API surface. Returns `{critical: bool, dimensions: [...]}`. Deterministic
  rules win when they fire (sovereign-first).
- Route critical Rust modules to **Kani**: the cheap pool generates a `kani::any()`
  harness + assertions (proves panic/overflow/UB-absence and assertion-unreachability up
  to a bound — no manual proof, concrete counterexample playback). Surface the concrete
  counterexample input+trace back to the agent as a fix target or a new regression test.
- Degrade: Kani timeout/unbounded-loop → proptest. Kani has NO concurrency support — do
  not route concurrent code here (Part A Phase 4 covers concurrency).

**Phase-2 DoD:** criticality_classify works (rules + fallback); critical Rust modules go
to Kani with LLM-generated harnesses; concrete counterexamples surfaced actionably;
degrades to proptest on timeout. VALIDATE: seed Rust modules with overflow/panic/UB bugs;
Kani catches what proptest misses. Committed.

## A3. PHASE 3 — SMT contracts for the pure critical few
Rung 4, critical-few only, ALWAYS gated. Dafny (or CrossHair for Python) contracts on
pure, stable-spec, high-blast-radius modules:
- The cheap pool generates pre/post/invariant contracts; the solver (Z3 via Dafny /
  concolic via CrossHair) adjudicates.
- **Triple guard before any contract result is trusted as `verified`:** (1) mutation
  cross-check (MutDafny-style — break the code, the contract must catch it), (2)
  differential cross-check (every passing agent test must satisfy the contract), (3)
  Clover-style code↔doc↔spec consistency. Fail any guard → `spec_rejected`, downgrade to
  Phase-1 PBT, never report a pass.
- Make formal-pass an ADDITIONAL best-of-N selection criterion in mcp-search — but ONLY
  on critical modules and ONLY for the top-k surviving candidates (passes tests → passes
  type/static → survives PBT+mutation → verifies contract). Running rungs 3-5 on all N
  would let solver wall-clock dominate; cap it.

**Phase-3 DoD:** Dafny/CrossHair contracts on critical-few modules, triple-guarded;
`spec_rejected` downgrades cleanly; formal-pass is a best-of-N tiebreaker on critical
top-k only. VALIDATE: a contract that passes on mutated code is caught by the mutation
guard and rejected. Committed.

## A4. PHASE 4 — composition & protocol level (contracts + tests + model-checking, NO whole-program proofs)
Unit soundness does not compose; do NOT attempt whole-program proofs. Instead:
- **Assume-guarantee contracts at module edges:** a verified callee's postcondition
  becomes the caller's assumption; the caller must establish the callee's precondition.
  Emit/check contracts at edges (Dafny pre/post; Rust debug_assert/Verus specs; Python
  deal/icontract; TS runtime schema validation via zod). Where static proof is infeasible,
  enforce contracts as RUNTIME MONITORS.
- **Typed API boundaries / breaking-change detection:** schema validation (zod/OpenAPI)
  + oasdiff-style checks catch the cross-module API/type-mismatch and effect-ordering bugs.
- **Cross-module state spanning files:** stateful property testing (Hypothesis
  RuleBasedStateMachine, proptest state machines) — model states+transitions, find a
  violating sequence.
- **Concurrency (where unit verification is useless):** Rust → Loom (exhaustive
  interleaving, bound preemptions to 2-3) or Shuttle (randomized). Trigger only when the
  agent introduces shared atomics/locks/lock-free structures.
- **Protocol/distributed design:** TLA+/Apalache or Alloy on the DESIGN, when the agent
  designs a multi-node protocol.
- **The principled handoff:** prove the pure critical kernels; contract-check the edges;
  integration/E2E-test the wiring; model-check the protocols/concurrency. Formal methods
  own the small high-value core; integration tests own the combinatorial glue.

**Phase-4 DoD:** edge contracts (static where possible, runtime monitors otherwise);
stateful PBT for cross-module state; Loom/Shuttle wired for concurrent code; a documented
formal-vs-integration handoff. Whole-program proof stays OFF the table. Committed.

## A5. DEFER (do not build now)
Lean/Rocq proofs of general code (research-grade for code: ~7.8-27% vericoding);
whole-program compositional proofs (LLMs can't, ~3.69% DafnyComp); Verus beyond a small
pilot. Promote later only if: compositional-verification success crosses ~50%, or
Lean/Rocq code-vericoding crosses ~70% at acceptable latency.

================================================================================
# PART B — MCP LIFECYCLE ENFORCEMENT (so engineered tools are actually used)
================================================================================

## B0. The enforcement framework (apply to EVERY MCP; the general rule)
A value-adding MCP should be LIFECYCLE-ENFORCED (fired from a conductor hook regardless
of the model's judgment) — rather than DISCRETIONARY (prompted, model decides) — when
ALL THREE hold:
1. **Low LLM reliability at calling it at the right moment** (the model forgets/skips it).
2. **High consequence of skipping** — distinguish additive-quality (discretionary) from
   broken-guarantee (enforce) from degraded-future-runs (enforce).
3. **Structural bias against calling it** — using it feels like overhead exactly when the
   model is most confident (verification, checkpointing, self-checks).
Score 3/3 → enforce. Otherwise → discretionary. Document this rule in the conductor README.

Enforcement mechanism: the conductor (mcp-escalation) registers hooks
(pre_llm_call / post_llm_call / post_tool_call / step). An enforced capability fires from
the appropriate hook deterministically. The model's only influence is the content, never
whether the call happens. Every enforced fire emits an OTel span so the UI shows it.

## B1. AUDIT FIRST (before changing anything)
Read the conductor plugin and config; produce `MCP_ENFORCEMENT_AUDIT.md` recording, for
each of the 14 MCPs, whether it is currently discretionary (LLM-called) or already
hook-fired, and at which hook. The 14: verify, research, escalation(conductor),
codebase-rag, docs, checkpoint, knowledge-graph, observability, search, watchdog, lsp,
repomap, codegraph, scopemap. Do not change behavior in this step — inventory only.

## B2. HARD-ENFORCE these 4 (broken guarantee if skipped; model structurally skips them)

**1. mcp-verify / verify_formal — the ground-truth gate.**
- Fire automatically on every file write above a small threshold (post_tool_call hook
  detecting a write), AND as a HARD GATE before every checkpoint (a checkpoint cannot
  proceed without a green verify). The model cannot checkpoint on its own assessment.
- Handle results: `verified`→proceed; `counterexample`→block, return failing input to the
  agent (bounded retries 1-2, then surface); `unknown`→proceed-with-flag (never block on
  tool incapacity); `spec_rejected`→downgrade a rung + proceed-with-flag (NEVER report a
  pass). The gate must never wedge the loop.

**2. mcp-checkpoint — recovery points.**
- Fire automatically AFTER every green verify, before advancing to the next subtask
  (step hook). Not a model choice — otherwise the model checkpoints only "when done" and
  loses incremental recovery + clean backward-traceable state.

**3. mcp-research — entry gate (enforce USE, not just gate overuse).**
- The existing corpus-first + budget + cooldown + exhaustion gates already prevent
  OVERUSE. Add the complementary ENTRY enforcement: the conductor fires deep_research
  ONCE at task start for any task the criticality/novelty classifier marks as needing
  novel external knowledge (new protocol/domain/spec/current-state), BEFORE implementation
  begins — so the model can't skip it by feeling smart enough. Still subject to the
  corpus-first gate (instant if already covered). One entry fire per qualifying task.

**4. mcp-watchdog — should not be a tool call at all.**
- Convert to a background lifecycle process watching every run (spiral/stall/budget),
  not a discretionary tool. The model should never be calling this; it fires
  unconditionally on every run. If already a background thread, confirm it is and that no
  path depends on the model invoking it.

## B3. SOFT-ENFORCE these 3 (enforce at a lifecycle point, not as a hard gate)

**5. mcp-knowledge-graph — task-close memory write.**
- Require a KG write at task CLOSE (step/run-complete hook) summarizing what was decided
  and why, regardless of whether the model thought to record it mid-run. Compounds future
  runs; the model reliably records task-relevant facts but misses the ambient "we decided
  X about this codebase" facts that are the actual long-term value.

**6. mcp-escalation (conductor) — classification in the hook, not the model's hands.**
- The plan-need / difficulty / criticality classification runs in pre_llm_call BEFORE the
  model sees the task, so the model cannot dodge the conductor by self-classifying a task
  as "easy." (This is the existing plan/execute split — confirm it's hook-fired, not
  prompted.)

**7. mcp-codebase-rag — retrieval before multi-file edits.**
- Require a RAG retrieval pass at the START of any multi-file edit (step hook detecting a
  multi-file task) to surface relevant prior patterns before implementation, even when the
  model "knows the codebase." (The corpus-first gate inside deep_research already exists;
  this is the implementation-time complement.)

## B4. LEAVE DISCRETIONARY these 7 (additive quality; model's judgment is reliable enough)
docs, search, lsp, repomap, codegraph, scopemap, observability. Keep them prompt-driven
with strong skill descriptions. (codegraph is borderline — add a strong skill prompt to
call code_impact before modifying a function touched by many files, but do NOT hard-fire
it.) observability is infrastructure plumbing, not a tool call at all.

## B5. Cross-cutting for Part B
- All enforced fires emit OTel spans (verify_enforced, checkpoint_enforced,
  research_entry_gate, watchdog_background, kg_taskclose_write, classification_prefired,
  rag_pre_multifile) so the UI swimlane renders enforcement.
- Every enforced hook degrades gracefully: if the target MCP is down, the hook logs and
  proceeds (never crashes the loop) — sovereign discipline holds.
- Bounded retries everywhere; proceed-with-flag on `unknown`; never wedge.

================================================================================
# SEQUENCING & DISCIPLINE
================================================================================
Order: Part B audit (B1) → Part A Phase 1 (the verification base) → Part B B2 hard-enforce
(wire verify_formal + checkpoint as the gate) → Part A Phase 2 (Kani + classifier, which
B2 research-gate and B3 reuse) → Part B B3 soft-enforce → Part A Phase 3 → Part A Phase 4.
Rationale: build the verification capability before enforcing it; the criticality
classifier (A2) is shared by the verification router and the research entry gate, so build
it once and reuse. Commit per phase; smoke green between phases; failures reported honestly
(a phase that doesn't beat the pytest-only baseline on its seeded-bug corpus is signal, not
something to paper over).

## DEFINITION OF DONE
- mcp-verify-formal implements rungs 0-2 enforced on everything (compile/type/lint +
  mutation-guarded LLM-PBT/metamorphic via the pytest oracle, four-value result), Kani on
  critical Rust (rung 3), triple-guarded SMT contracts on the critical-few (rung 4), and
  composition via edge-contracts + stateful PBT + Loom/Shuttle + TLA+/Alloy (Phase 4) — with
  Lean/Rocq and whole-program proofs explicitly deferred.
- The spec-generation weak link is guarded everywhere by mutation cross-check + differential
  cross-check; `spec_rejected` never reported as a pass.
- The conductor hard-enforces verify_formal (post-write + pre-checkpoint gate), checkpoint
  (post-green), research entry (once per qualifying task), and runs watchdog as a background
  process; soft-enforces KG task-close write, classification-in-hook, and RAG-before-
  multi-file; leaves the 7 discretionary MCPs prompt-driven.
- Every enforced fire is deterministic (model can't skip it), emits a span, degrades
  gracefully, and handles unknown/spec_rejected/counterexample without wedging the loop.
- Seeded-bug corpora validate each verification phase beats the pytest-only baseline.
- Anti-Frankenstein upheld: extensions of mcp-verify/mcp-search/mcp-escalation + the
  conductor hooks, no framework, no core-loop modification, sovereign/deterministic-first,
  public-repo-clean. Committed per phase; smoke green throughout.