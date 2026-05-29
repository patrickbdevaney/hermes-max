---
name: workflow-plan-first
description: ALWAYS run first on any task touching more than one file or more than ~3 steps.
trigger: any multi-step or multi-file task
---
# Plan first — never start coding a multi-step task without a written plan.

1. Restate the goal in one sentence and the DEFINITION OF DONE as a concrete, testable
   checklist (e.g. "endpoint returns 200 with JSON {ok:true}", "pytest passes", "file X exists").
2. Query codebase-rag (`mcp_hermes_max_codebase_rag_search_code`) and knowledge-graph
   (`mcp_hermes_max_knowledge_graph_recall_about`) for relevant existing code, patterns, and
   prior decisions BEFORE planning. Plan around what exists; don't invent.
3. Decompose into the SMALLEST subtasks that are each independently verifiable. Each subtask =
   one bounded change you could verify with a single test or check. Aim for steps a junior dev
   could do in 10 minutes. Write them as a todo/kanban list.
4. PRE-MORTEM the plan (lookahead): for each subtask, ask "what is the most likely way this
   step hangs, errors, or produces something untestable?" Common traps: long-running processes
   (see process-gotchas), missing deps, wrong working dir, a step with no way to verify it.
   Adjust the plan to make every step verifiable and non-hanging.
5. Write the plan to a file (PLAN.md in the project) so it survives context compression.
   Re-read PLAN.md at the start of each subtask. The plan is the source of truth, not memory.
6. Only then begin subtask 1.
