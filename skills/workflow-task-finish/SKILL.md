---
name: workflow-task-finish
description: "Never declare done on red: verify, then record what you learned to the knowledge graph."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, verification, knowledge-graph, gate, hermes-max]
    category: hermes-max
    related_skills: [workflow-task-start, workflow-stuck]
---

<!-- TRIGGERS WHEN: "Never declare done on red: verify, then record what you learned to the knowledge graph." version: 1.0.0 author: Hermes Max license: MIT platforms: [linux, maco -->

# Task Finish — the verification gate + knowledge capture

Run this **before reporting any coding task complete**. You may not declare
"done" while verification is red.

## Steps

1. **Verify (hard gate).** Call `verify(path)` (mcp-verify) on the code you
   changed. It runs lint → typecheck → unit tests.
   - If `passed` is **false**, read the per-stage diagnostics, fix the cause,
     and re-run `verify`. Repeat until green. Do **not** report done on red.
   - If the same failure persists after a few honest attempts, switch to the
     `workflow-stuck` skill instead of thrashing.
   - If `verify` is unavailable (server down), say so explicitly and fall back
     to running the project's lint/type/test commands yourself — but still do
     not claim success unattended without some green signal.
2. **Record what you learned** to mcp-knowledge-graph so the next session starts
   ahead:
   - `record_entity` for any new decision (`type="decision"`), bug
     (`type="bug"`), or component (`type="file"|"service"`) — include a short
     `why` in props.
   - `record_relation` to link them, e.g. `(decision)-[applies_to]->(file)`,
     `(bug)-[fixed_in]->(commit)`, `(service)-[depends_on]->(service)`.
3. **Let a skill distill.** If the task involved a novel, reusable technique,
   allow Hermes's skill-creation loop to capture it (don't force it; the nudge
   will prompt you when warranted).

## Why

A gate that *cannot* be bypassed on broken code makes unattended operation
trustworthy — reliability you can leave alone overnight. Recording decisions is
what makes the *second* related task faster than the first.
