---
name: workflow-done-definition
description: What "done" means. Applied before reporting any task complete.
trigger: about to report a task or subtask complete
---
# "Done" is defined by verification, never by the model's opinion.

Before reporting done:
1. Every item in the task's definition-of-done checklist (from workflow-plan-first) is met.
2. mcp-verify is GREEN (`mcp_hermes_max_verify_verify` — lint + types + tests). If the project
   has no tests, that itself is a gap — write at least one test that exercises the main path,
   then verify.
3. For anything with a runtime (server, CLI, script): it was actually RUN once and produced the
   expected output (see workflow-long-running-processes for how to test a server).
4. The knowledge-graph has the key decisions recorded
   (`mcp_hermes_max_knowledge_graph_record_entity`); a skill was distilled if the task was novel.
5. Only then report done, with: what was built, the verify result, and how to run it.
If any of the above is not true, the task is NOT done — keep going or invoke stuck-detect-reset.
