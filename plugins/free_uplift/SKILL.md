---
name: checkpoint_review
description: >-
  OPTIONAL proactive coherence checkpoint (free_uplift plugin). When free_uplift is
  ON (hm mode shows [free-uplift: ON]) and Kimi-K2.6:free is live, after a file
  completes and verify passes — BEFORE checkpoint — run one Kimi-K2.6:free review
  asking whether the implementation matches its FILE SPEC and the already-completed
  interfaces. CLEAN → checkpoint and continue. FLAG → fix the one named issue first.
  Hard-capped (≤2/file, ≤10/task). Skips silently if the rate bucket is tight. Never
  blocks the loop on error. Off by default.
version: 1.0.0
author: Hermes Max
license: MIT
metadata:
  hermes:
    tags: [plugin, free-uplift, coherence, checkpoint, optional, hermes-max]
    category: hermes-max-plugin
    related_skills: [workflow-execute-from-plan, workflow-verify-enhanced, workflow-task-finish]
---

<!-- TRIGGERS WHEN: free_uplift is ON, a file just passed verify, and you are about to checkpoint -->

# checkpoint_review — a free proactive coherence check (optional, off by default)

This is a **plugin** capability, not core. It runs only when the operator has turned
it on (`hm up --free-uplift` or `INFERENCE_MODE_FREE_UPLIFT=true`) AND
`OPENROUTER_API_KEY` is present AND Kimi-K2.6:free is live with daily RPD headroom.
When any of those is false, this skill does nothing — proceed exactly as normal.

## When it fires

After a file completes and **verify passes**, but **before** the checkpoint
([[workflow-task-finish]]). One call, then continue.

## What it does

Spend ONE `free_uplift` role call (Kimi-K2.6:free via OpenRouter) with this exact
review prompt:

> Read the FILE SPEC in PLAN.md for this file and the implementation. Does it match
> exactly? Do its interfaces match already-completed files? Respond CLEAN or
> FLAG: <one specific issue>. Nothing else.

- **CLEAN** → checkpoint and move on. No drift.
- **FLAG: \<issue\>** → fix that one named issue, re-verify, then checkpoint. Do not
  re-run the review more than twice on the same file.

## The guardrails (do not override)

- **≤ 2 calls per file, ≤ 10 per task** — hard caps. Never spend more.
- **Skip silently if the rate bucket is tight** — never block on a rate limit.
- **Never block the loop on error** — if the call fails, proceed as if CLEAN.
- It is a *checkpoint*, not a *gate*: a FLAG is advice to fix one concrete thing,
  not a reason to stall. If you can't resolve the flag in one pass, checkpoint with a
  note and continue ([[workflow-stuck]] if truly blocked).

When Kimi-K2.6:free is deprecated the plugin stops registering and this skill goes
dormant automatically — the core loop is unaffected.
