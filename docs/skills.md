# Skills — the catalogue

Skills are markdown files (`skills/<name>/SKILL.md`) loaded into the system prompt
as **triggered guidance**: they tell the model *when* to reach for which MCP tool
and *what discipline* to follow. They are policy, not code — advice the model reads,
not functions it calls. `register-mcp.sh` installs them into
`~/.hermes/skills/hermes-max/`.

There are ~34 skills. They group by the part of the loop they govern.

## Task framing

- **`workflow-task-start`** — ground a task in RAG + KG before acting.
- **`workflow-task-finish`** — the verify gate + record results to the KG.
- **`workflow-retrieve-before-act`** — check the corpus/KG before doing fresh work.
- **`workflow-tool-selection`** — pick the right tool for the job.

## Planning

- **`workflow-plan`** — decompose a large task.
- **`workflow-plan-first`** — write a plan (+ pre-mortem) to PLAN.md before any code.
- **`workflow-plan-contract`** — the PLAN.md schema (TASK, WORKING_DIRECTORY,
  FILES, a FILE SPEC per file, DONE_CONDITION, RISKS).
- **`workflow-execute-from-plan`** — implement files in order, verify each, never
  invent past the plan.
- **`workflow-spec-driven`** — spec-first development.

## Long-horizon discipline

- **`workflow-subtask-loop`** — one bounded subtask → verify → record → checkpoint.
- **`workflow-long-running-processes`** — a running server is success, not a hang;
  start backgrounded, test once with a timeout, never poll.
- **`skill-process-gotchas`** — externalized world-knowledge a fast small model
  misses.
- **`workflow-stuck`** / **`workflow-stuck-detect-reset`** — the loop-then-ping
  circuit breaker; on STUCK, summarize → `revert_to_last_green` → reset context →
  try a different approach → ping.
- **`workflow-done-definition`** — "done" = verify green, never the model's opinion.
- **`workflow-deadline-discipline`** — short turns; background + `check_stall` once;
  on any watchdog flag, revert + replan.

See [long-horizon.md](long-horizon.md) for the full design and the kickoff-prompt
template.

## Quality & verification

- **`workflow-verify-enhanced`** — drive the deep-verify layers.
- **`workflow-quality-bar`** — the bar a change must clear.
- **`workflow-edit-format`** — small diff edits + `quick_check` after each.
- **`workflow-critic`** — after a hard subtask goes green, one bounded reviewer
  red-teams the diff.
- **`workflow-effort-routing`** — HIGH effort on planning/hard work, LOW on
  reads/mechanical — caps spirals.

## Retrieval & code intelligence

- **`workflow-lsp`** — compiler-grade navigation via mcp-lsp.
- **`workflow-repomap`** — the PageRank repo map.
- **`workflow-codegraph`** — blast-radius / callers / dead-code analysis.

## Research

- **`workflow-deep-research`** — drive mcp-research's `deep_research`; gate depth on
  scope, verify before asserting, cite every claim.
- **`workflow-learn-framework`** — learn a novel framework on demand via the docs
  loop.

## Context & memory

- **`workflow-context-hygiene`** — PLAN.md is the source of truth; keep the relevant
  set small and pinned.
- **`workflow-context-condenser`** — condense long context.
- **`workflow-filesystem-offload`** — offload state to the filesystem.
- **`workflow-cache-discipline`** — prompt-cache-friendly habits.
- **`workflow-memory-curation`** — deliberately curate the always-in-context memory.

## Conductor & isolation

- **`workflow-conductor`** — the cloud-help invocation ladder (when to steer /
  synthesize / draft / escalate).
- **`workflow-escalate`** — when, and when *not*, to escalate.
- **`workflow-subagent-isolation`** — fan out read-only localization; keep the edit
  thread single and linear.

The MCP tools these skills drive are documented in [mcp-servers.md](mcp-servers.md).
