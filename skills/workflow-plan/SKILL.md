---
name: workflow-plan
description: "For tasks over ~5 files, plan and decompose before coding using native plan + kanban."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, planning, kanban, decomposition, hermes-max]
    category: hermes-max
    related_skills: [workflow-task-start, workflow-task-finish]
---

# Plan — decompose before large changes

Run this for any task spanning **more than ~5 files** or with non-trivial
architecture. Don't free-solo large changes.

## Steps

1. **Ground first.** Make sure you've run `workflow-task-start` so the plan is
   based on the actual codebase (real `search_code` hits and `recall_about`
   history), not assumptions.
2. **Write the plan.** Use Hermes's native `writing-plans` / `plan` skill to
   produce a concrete, ordered plan: the files to touch, the order, the risky
   steps, and how each step will be verified.
3. **Decompose into tasks.** Lean on Hermes's native kanban decomposition
   (`auto_decompose` is on) to break the plan into trackable units; use
   subagent delegation (planner / coder / reviewer roles) for independent units
   where it helps.
4. **Define the done-gate up front.** State that each unit must pass `verify`
   (`workflow-task-finish`) before it's considered complete.

## Why

Decomposition + native delegation gives planner/coder/reviewer separation
without building a custom multi-agent framework. Planning against retrieved
reality (not guesses) is what keeps large autonomous changes coherent.

## Don't over-build

These workflow skills are intentionally light. Hermes's self-improvement loop
and the weekly DSPy evolution cron refine them from real session history over
time — resist hand-tuning them into brittle scripts.
