---
name: workflow-stuck
description: "Stop thrashing after N failed attempts: write a STUCK report and ping the operator."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, escalation, human-in-loop, telegram, hermes-max]
    category: hermes-max
    related_skills: [workflow-task-finish, workflow-escalate]
---

# Stuck — loop, then ping me

The overnight failure mode is silent thrashing: trying the same fix over and
over. This skill is the circuit breaker.

## Trigger

You hit the same error, or make no real progress, after **3 genuine attempts**
on one problem. (Genuine = a different hypothesis each time, not the same edit
retried.)

## Steps

1. **Stop.** Do not attempt a 4th variation of the same fix.
2. **Write a STUCK report** — concise and specific:
   - The exact error / failing check (paste the key lines).
   - The 2-3 approaches you already tried and *why each failed*.
   - Your current best hypothesis and what you'd need to verify it.
   - The specific decision or input you need from the operator.
3. **Ping the operator** via the messaging tool (Telegram is the overnight
   channel). Send the STUCK report as a single clear message ending in a
   concrete question.
4. **Park the task** at a clean checkpoint and, if other independent tasks
   exist, move on rather than burning cycles.

## Before pinging, consider escalation

If the blocker is a genuinely-hard but *well-scoped* subproblem (not a missing
decision), the `workflow-escalate` skill may resolve it without human input —
but only if escalation is enabled. A missing *decision* always goes to the
human; only hard *reasoning* is a candidate for escalation.

## Why

This is the operator's explicit "loop then ping me" contract. A specific blocker
with attempted approaches is actionable; silent grinding wastes the unattended
hours that are this system's main advantage.
