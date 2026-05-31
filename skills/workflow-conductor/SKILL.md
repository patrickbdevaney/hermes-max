---
name: workflow-conductor
description: Stingy, classifier-gated ladder for reaching OPTIONAL cloud help (steer/parallel_draft/synthesize/escalate). The local driver does all routine work at $0; reach up only on genuinely-hard subtasks, behind presence-gated stateless tools, and gate every directive before commit.
trigger: a subtask the difficulty signal flags HARD/novel, watchdog genuine-stuck after local recovery failed, or a major architectural fork — never routine work
---

<!-- TRIGGERS WHEN: Stingy, classifier-gated ladder for reaching OPTIONAL cloud help (steer/parallel_draft/synthesize/escalate). -->
# The local Qwen driver does everything. Cloud help is rare, optional, and stateless.

The conductor adds cloud help as TOOLS, never a backend swap — the Hermes model stays the orchestrator
and integrator the whole time. It is fully presence-gated: with zero cloud keys set, every rung is OFF
and you proceed local-only. Reach up ONLY when the difficulty classifier says HARD/novel, the watchdog
reports genuine-stuck after local recovery already failed, or there's a major architectural fork. Routine
work stays local at $0 — do NOT reach up for it.

## Step 1 — ask the policy which rung (don't guess)
Call `conductor_plan(signals, verifiable)` with the cheap signals you already have
(`file_count`, `novelty`, `prior_failures`, `lines_changed`, `cross_module`) and whether the subtask has
an objective TEST ORACLE (`verifiable=true` iff you can write tests that decide success). It returns the
ladder rung, presence-gated — it never fires a call. The ladder by subtask type:

- **routine (easy/medium)** → `tier=local`. Stay on the local model. Stop here.
- **verifiable + hard** → `tier=parallel_draft` → if none pass, `synthesize`.
- **ambiguous + hard** → `tier=steer` (cheap nudge) → if it doesn't unblock, `synthesize`.
- **frontier-novel / synth-failed** → `tier=escalate` (Opus) ONLY when synth failed verify TWICE or two
  synth opinions DISAGREE on a high-blast-radius change. Otherwise the gate stays shut.

If a role is OFF (no key) the plan degrades automatically (pool off → synthesize → local; steer off →
synthesize; synth off → local; Opus off → surface to human/local). Honor what `tier` it returns.

## Step 2 — assemble the brief deterministically (never hand-write it)
You (the weak local model) write ONLY two fields: `current_blocker` and `decision_needed`. Then call
`brief_assemble(task_id, current_blocker, decision_needed, profile)` — it pulls goal/done/constraints/
success from PLAN.md, architecture + failed_approaches from the KG + watchdog, and token-budgeted code
from codebase-rag. Profiles: `compact` for steer, `full` for synthesize, `draft` for parallel_draft
(pass `acceptance_tests` — the oracle). Use `brief_request_more` if the cloud asks for more.

## Step 3 — fire the chosen rung (stateless; never raises)
- **parallel_draft**: `parallel_draft(task_spec, tests=<oracle>, …)` on mcp-search — fans best-of-N across
  the free pool and the VERIFIER (not a model) selects the green winner. None pass → it returns
  `route_to=synthesize`. Integrate the winning `selected_files` yourself; the slop models never touch the
  repo.
- **steer / synthesize**: `conductor_steer(brief)` / `conductor_synthesize(brief)` — first present rung
  wins, silent fall-with-log on failure, `proceed_local` if the role is OFF. The cloud returns a
  STRUCTURED DIRECTIVE; it is ADVISORY.
- **escalate**: only when the Opus gate is met — the existing `escalate` tool (capped).

## Step 4 — GATE the directive before you execute it (the cloud is smart but BLIND)
Call `directive_verify(directive, repo)` before touching the tree. It checks every `assumptions` entry
against ACTUAL repo state (a false one is rejected + recorded as a failed_approach — re-brief), confirms
`apis_to_use` exist, requires concrete `tests_to_write`, and on low-confidence + high-blast-radius asks
for a second synth opinion (`compare_directives` decides agree→execute vs disagree→escalate/human).
Execute + `checkpoint` ONLY when `execute` is true. Write the prescribed tests FIRST.

## Step 5 — record the outcome (the compounding flywheel)
Call `conductor_record_outcome(subtask, tier, outcome)` at subtask end so the difficulty classifier
learns which subtasks needed which tier and repeated blockers reuse prior directives. Periodically check
`conductor_frequency_report` — synth ≤ ~15/project and Opus ≤ ~3 are the honest targets. If Opus calls
exceed 3, the brief-assembler quality is the bottleneck — fix the assembler, don't spend more on Opus.

## The discipline (non-negotiable)
Slop-draft the verifiable, synthesize the ambiguous, escalate only the frontier-novel. Never run
parallel_draft on an ambiguous subtask (no oracle → the verifier can't select → wasted slop). Never swap
the Hermes backend model. No secrets in any brief. With all keys unset this whole skill is a no-op and
the bare local harness runs unchanged.
