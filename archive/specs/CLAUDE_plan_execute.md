# CLAUDE_plan_execute.md — Plan/Execute Split: Expensive Planner, Cheap Executor

## The problem this solves

On simple tasks (a Bloom filter), the local 35B executor drifts: it loses track of its working
directory, isn't sure whether a verification step ran, and produces correct-but-shallow output that
misses the texture of senior engineering. This is not a task-difficulty failure — it's a coherence
failure. The 35B can WRITE any code a good plan specifies; it cannot reliably INVENT the rich
structure, edge cases, and done-conditions that Opus-level output requires.

## The fix

Split every non-trivial task into two phases with two different model tiers:

- **PLAN phase → V4-Pro (steer tier, expensive).** Produces a complete, unambiguous engineering
  plan so detailed that the executor never has to invent anything it cannot reliably write. The
  plan specifies file structure, exact function signatures, the algorithm/approach for each
  function in prose, edge cases, error handling, the done-condition, and the verification steps.
  This is compress-then-reason: pay for expensive reasoning once, upfront, to produce a brief the
  cheap model executes without drift.

- **EXECUTE phase → local 35B (cheap tier).** Implements the plan literally. Because the plan
  leaves nothing to invent, the executor's job is transcription + local correctness loops
  (verify/property/metamorphic), not design. The cheap model is good at this.

The principle: **the executor must never face a design decision the planner didn't already make.**
Every "how should I do X" question must be answered in the plan. If the executor has to invent an
approach, the plan failed and that's a planner-quality bug, not an executor bug.

Work in STAGES, each committed individually. Anti-Frankenstein discipline holds: this is an
extension of the existing conductor + skill surface, not a new orchestration framework. Read the
whole spec first. hm health must be green before starting; validate each stage with a real call.

=================================================================================================
## STAGE 1 — Conductor: tier policy for plan vs execute
=================================================================================================

Extend the conductor (mcp-escalation / conductor config) with an explicit two-tier task policy.

### 1.1 — Define the tiers
- steer tier = V4-Pro (the expensive planner). Confirm the conductor's steer-tier model string
  points at V4-Pro (DeepSeek V4-Pro via the funded API key). Document the exact model id in the
  conductor config.
- local tier = the 35B vLLM endpoint (the executor), unchanged.

### 1.2 — Task classification for plan routing
- Add a classifier (rule-based, no LLM call) in the conductor that fires at task start and
  classifies whether a task needs a PLAN phase:
  - NEEDS_PLAN: task verb is Implement / Build / Write / Create / Design / Refactor / Add, AND
    the task touches more than one file OR is more than a single-function change OR mentions tests.
  - NO_PLAN: single-file edits, lookups, one-line fixes, pure questions.
- Log a task_classification span {plan_required: bool, reason}.

### 1.3 — Plan routing
- When NEEDS_PLAN: the conductor routes the FIRST turn (plan generation) to the steer tier
  (V4-Pro). After the plan is produced and written to PLAN.md, control returns to the local tier
  for execution.
- When NO_PLAN: stay on local tier throughout (don't pay for trivial work).
- Log a tier_routing span {phase: "plan"|"execute", tier: "steer"|"local", model_id}.

**Stage-1 DoD:** conductor classifies plan-need, routes plan turns to V4-Pro and execution to
local, both spans logged. Validate: an "Implement X with tests" task shows phase=plan tier=steer
then phase=execute tier=local in the spans. A "what does this function do" task stays local.
Committed.

=================================================================================================
## STAGE 2 — The planning contract: what V4-Pro must produce
=================================================================================================

The plan is only useful if it leaves nothing to invent. Define and enforce the plan contract.

### 2.1 — PLAN.md schema (the incontrovertible brief)
- Add a skill workflow-plan-contract.md that the steer tier uses when generating a plan. The
  plan MUST contain, for the whole task:
  - TASK: one sentence restating what is being built.
  - WORKING_DIRECTORY: the absolute path the executor must operate in (resolved at plan time).
  - FILES: every file to create or modify, with its full relative path and one-line purpose.
  - DONE_CONDITION: concrete, checkable (e.g. "verify green, property_test passes, 12+ tests,
    FPR within 10% of theoretical at capacity").
  - RISKS: what could go wrong and how the executor detects it early.
- And for EACH file to be created, a FILE SPEC containing:
  - Every public class/function with its EXACT signature (name, typed params, return type).
  - For each function: a prose description of the algorithm/approach precise enough that writing
    the body requires no design decisions — the formula to use, the data structure, the library
    call, the control flow. Not pseudocode necessarily, but no ambiguity about HOW.
  - Edge cases each function must handle and the exact error type/message to raise.
  - For test files: the list of test cases by name with the property each one checks.
- The plan must NOT contain the actual implementation code — it specifies WHAT and HOW-in-prose so
  the executor writes the code. (If the planner writes the whole file, you're paying steer-tier
  prices for execution — that's the wrong split.)

### 2.2 — Plan completeness gate
- Add a plan_lint tool (in the conductor or a small checker) that validates a generated PLAN.md
  against the schema before execution begins: every file listed has a FILE SPEC; every function
  has a signature + approach; DONE_CONDITION is concrete; WORKING_DIRECTORY is absolute.
- If plan_lint fails, the plan goes BACK to the steer tier with the specific gaps, not forward to
  the executor. Max 2 plan-revision rounds, then proceed with a flagged-incomplete plan (don't
  loop forever).
- Log a plan_lint span {complete: bool, missing: [...], revision_round}.

**Stage-2 DoD:** workflow-plan-contract skill installed; plan_lint validates the schema and bounces
incomplete plans back to steer tier; spans logged. Validate: feed a deliberately-thin plan, confirm
plan_lint catches the missing FILE SPEC and routes it back. Committed.

=================================================================================================
## STAGE 3 — Execution contract: the executor implements, never invents
=================================================================================================

### 3.1 — workflow-execute-from-plan.md skill (always-on for execution phase)
- Description (the trigger): "When a PLAN.md exists, implement it literally. Do not make design
  decisions — every function signature, algorithm, and edge case is in the plan. If you encounter
  a decision the plan does not answer, STOP and request a plan revision rather than inventing."
- The skill enforces:
  1. First action: pwd; if not WORKING_DIRECTORY from the plan, cd there and confirm.
  2. Implement files in the plan's order, one at a time.
  3. After each file, run the verify gate on it.
  4. After implementation, run property_test and metamorphic_test as the plan's DONE_CONDITION
     specifies.
  5. Checkpoint after each green file.
  6. The task is done ONLY when the plan's DONE_CONDITION is literally satisfied — re-read it and
     check each clause.

### 3.2 — The "no invention" escalation
- If the executor hits a point where the plan is silent on a design decision (a missing signature,
  an unspecified algorithm, an ambiguous edge case), it must call a request_plan_revision tool
  that routes the specific question back to the steer tier (V4-Pro), gets the answer appended to
  PLAN.md, and resumes — rather than guessing. This is the mechanism that closes the gap: the cheap
  model never invents code it can't reliably write; it asks the expensive model.
- Log a plan_revision_requested span {question, resolved: bool}.
- Bound this: max 3 revision requests per task, then proceed with best-effort + a flagged note
  (avoid infinite planner/executor ping-pong).

**Stage-3 DoD:** workflow-execute-from-plan skill installed and always-on during execute phase;
request_plan_revision routes gaps to steer tier and appends answers to PLAN.md; spans logged;
revision count bounded. Validate: give the executor a plan with one deliberately-missing function
approach, confirm it requests a revision rather than inventing. Committed.

=================================================================================================
## STAGE 4 — Quality bar enforcement
=================================================================================================

### 4.1 — workflow-quality-bar.md skill
- Description: "Software implementations must meet senior-engineer review standards: complete type
  annotations, docstrings on all public classes/methods, explicit error handling with informative
  messages, no placeholder comments, no TODOs in committed code, tests covering edge cases not just
  the happy path. A finished implementation has no gap a senior reviewer would flag."
- This applies to both the planner (the plan must specify these) and the executor (the code must
  have them).

### 4.2 — quality_check tool in mcp-verify
- Add a quality_check tool that runs alongside the verify gate and flags: public functions missing
  type annotations, public functions missing docstrings, TODO/FIXME/placeholder comments, bare
  except clauses, and functions with no error handling that the plan said needed it.
- This is advisory (a warning surfaced to the agent), NOT a hard gate — keep the deterministic
  test/lint/typecheck gate as the hard pass/fail. quality_check raises the texture, it doesn't
  block on style.
- Log a quality_check span {annotations_missing, docstrings_missing, placeholders, bare_excepts}.

**Stage-4 DoD:** workflow-quality-bar skill installed; quality_check tool in mcp-verify surfaces
texture gaps as advisory warnings; span logged. Validate: run quality_check on a file with a
missing docstring and a TODO, confirm both are flagged. Committed.

=================================================================================================
## STAGE 5 — Wire it together and prove the split
=================================================================================================

### 5.1 — End-to-end flow
- Confirm the full path for a NEEDS_PLAN task: task in → conductor classifies plan_required →
  steer tier (V4-Pro) generates PLAN.md → plan_lint validates (revise if needed) → local tier
  executes from plan (requesting revisions for any gap) → verify + property + metamorphic per the
  DONE_CONDITION → quality_check advisory → checkpoint → done only when DONE_CONDITION literally met.

### 5.2 — The proof task
- Run the Bloom filter task again, fresh directory, through the new flow. The proof is NOT that it
  produces a correct Bloom filter (it did before) — it's that:
  - the plan phase ran on V4-Pro (tier_routing span phase=plan tier=steer),
  - PLAN.md is complete enough that plan_lint passed,
  - the executor implemented WITHOUT any plan_revision_requested span (a clean plan needs no
    revisions on a simple task — if it asks for a revision on a Bloom filter, the plan was thin),
  - working directory was correct from the first action (no directory drift),
  - quality_check came back clean (docstrings, annotations, no placeholders),
  - DONE_CONDITION was literally checked, not assumed.
- If the executor drifts, asks for revisions, or skips the done-check on a task this simple, the
  PLANNER quality is the bug — tighten the plan contract, not the executor.

**Stage-5 DoD:** full plan/execute flow wired; Bloom filter task runs through it with plan on
steer tier, clean execution with zero revisions, correct directory, clean quality_check, and an
explicit DONE_CONDITION check. Committed.

=================================================================================================
## DEFINITION OF DONE
=================================================================================================
- Conductor routes plan turns to V4-Pro and execution to local, classified by task (Stage 1).
- The plan contract forces V4-Pro to produce an incontrovertible brief — exact signatures,
  prose algorithms, edge cases, concrete done-condition — validated by plan_lint (Stage 2).
- The executor implements literally and escalates any gap back to V4-Pro via request_plan_revision
  rather than inventing (Stage 3).
- Quality-bar skill + advisory quality_check raise output texture toward senior-review standard
  (Stage 4).
- The Bloom filter proof shows plan-on-steer, zero-revision clean execution, correct directory,
  clean quality_check (Stage 5).

The cost model: V4-Pro is paid only for the plan (one rich turn + any revisions), not for
execution. The 35B does all the token-heavy implementation. This is compress-then-reason: buy the
expensive model's judgment once, upfront, as a brief the cheap model can execute without drift.

What this explicitly does NOT do (anti-Frankenstein): it does not make every turn expensive, does
not add a multi-agent framework, does not route execution to V4-Pro, and does not hard-gate on
style. The split is one classifier + two skills + one lint tool + one advisory check, all on the
existing conductor and verify surfaces.

REFERENCE: the planner/executor split and compress-then-reason are documented in the agent-system
survey (RouteLLM-style conductor routing; the cheap-model-brief-then-expensive-reason pattern,
inverted here to expensive-plan-then-cheap-execute because planning is the quality-limiting phase
for a strong-executor/weak-planner local model).