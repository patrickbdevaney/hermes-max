---
name: workflow-context-hygiene
description: Keep the working set small and the plan always in focus.
trigger: continuously, especially as context fills
---

<!-- TRIGGERS WHEN: Keep the working set small and the plan always in focus. -->
# The model attends poorly to long context. Keep the relevant set small and pinned.

- PLAN.md is the source of truth, not memory. Re-read it at each subtask start. If your memory
  of the plan and PLAN.md disagree, PLAN.md wins.
- Keep in active focus only: the current subtask, the code being changed now, relevant retrieved
  snippets (from codebase-rag — `mcp_hermes_max_codebase_rag_search_code` /
  `mcp_hermes_max_codebase_rag_get_symbol_context`), and the rules. Everything else can be
  compressed/forgotten.
- Do NOT paste large file contents repeatedly. Retrieve the specific function with codebase-rag
  when needed instead of holding whole files in context.
- After finishing a subtask, you do not need its working details anymore — let them compress.
  The durable record is the git checkpoint (`mcp_hermes_max_checkpoint_checkpoint`) + the
  knowledge-graph entry, not the chat history.
