---
name: workflow-subagent-isolation
description: Fan OUT read-only research/localization to isolated sub-agents that return summaries; keep the EDIT thread single and linear.
trigger: a task needs broad research/localization across many files before editing
---
# Parallelize reading, never editing. Isolated reads in; one linear edit thread out.

Cognition's lesson: fanning out the EDIT path corrupts shared state and produces incoherent diffs.
But research and localization — "where is X, who calls Y, how is Z configured" — fan out cleanly.
So split the two:

## Isolated read-only sub-agents (fan-out OK)
- For research/localization, spawn isolated sub-agents scoped to READ-ONLY tools (Glob / Grep / Read,
  plus `search_code` / `retrieve_related` / `repo_map`). They cannot edit.
- Each returns only a SUMMARY to the parent — NOT raw file dumps. This keeps the parent's context
  small and on-plan (`workflow-context-hygiene`).
- The summary must be GROUNDED, not lossy: exact `file:line` anchors, the specific symbols/signatures
  found, and the concrete facts the parent needs to act — never vague prose like "the auth logic is
  in a few places." A lossy summary that drops the detail is the anti-pattern; carry the anchors.

## The edit thread stays single and linear
- ONE thread does the editing, in sequence, against the parent's plan. Do NOT spawn parallel editors.
- After the isolated reads return their summaries, the parent integrates them and edits linearly with
  disciplined compaction (drop the research chatter; keep the anchors + plan).
- Wire this via delegation config + tool-scoping: research delegates get read-only toolsets; the
  parent keeps the write tools.

## Bounds
Use isolation for genuinely broad localization (many files / unclear layout). For a small, known
change, just read the files directly — don't spawn an agent to find what you can `grep` in one step.

## Graceful degradation
If delegation is unavailable, do the localization inline with `search_code` / `retrieve_related` /
`grep` and keep your own working set small — the single-linear-edit discipline still holds.
