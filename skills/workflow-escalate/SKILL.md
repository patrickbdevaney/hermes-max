---
name: workflow-escalate
description: "When (and when not) to escalate a hard, well-scoped subproblem to a cloud tier."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, escalation, routing, cost, hermes-max]
    category: hermes-max
    related_skills: [workflow-stuck]
---

# Escalate — rare, scoped, cost-capped

The default is **$0 local grinding**. Escalation to a cheap cloud frontier model
is the exception, not the habit. mcp-escalation is **OFF by default** and has a
hard daily USD cap enforced in the server.

## Escalate ONLY when ALL hold

- The subproblem is **genuinely hard** for the local model (subtle algorithm,
  dense cross-file reasoning, a tricky proof-like constraint) — not routine work.
- It is **well-scoped**: you can state the problem and the success criterion in
  a self-contained prompt, with the relevant code/context attached.
- You have already tried locally and have a clear record of what failed
  (you've effectively passed the `workflow-stuck` bar on *reasoning*, not on a
  *missing decision*).

## Do NOT escalate

- Routine edits, boilerplate, formatting, test scaffolding.
- Anything blocked on a **human decision** (use `workflow-stuck` → ping instead).
- Repeatedly, to brute-force a vague problem — that just burns the cap.

## How

1. Assemble a self-contained prompt: problem statement, the minimal relevant
   code, constraints, and the exact success criterion.
2. Call `escalate(task, tier)` (mcp-escalation). If it returns disabled or
   cap-reached, respect that — fall back to `workflow-stuck` and ping the
   operator. Never try to route around the cap.
3. Treat the cloud result as a **proposal**: integrate it, then run the
   `verify` gate (`workflow-task-finish`) before accepting it.

## Never

Tier-3 (Opus / Claude Code) is **not** wired here and must not be — it lives on
the laptop's separate Claude Code to avoid auth collisions.
