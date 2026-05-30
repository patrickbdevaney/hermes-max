#!/usr/bin/env python3
"""Standalone smoke test for the brief-assembler (Stage 2). Runs with NO sibling
servers up — proving graceful degradation (KG/RAG/checkpoint absent -> empty
sections, brief still assembles). Asserts the Stage-2 DoD:

  • builds compact/full/draft briefs from a real PLAN.md, with the LOCAL model
    supplying ONLY current_blocker + decision_needed
  • valid structure + directive schema attached
  • profile sizes within budget (compact < full ceilings)
  • draft profile carries acceptance_tests and omits architecture_state
  • request_more works (and rejects unknown sections)
  • graceful degradation: with all sibling servers down, sources_live shows
    plan_md True but kg/rag False, and it still returns ok
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="brief-smoke-")
# Point watchdog state at an empty temp dir; sibling MCP ports at dead ports so
# every MCP pull fails fast and degrades (proves no hard dependency).
os.environ["WATCHDOG_STATE_DIR"] = os.path.join(_TMP, "wd")
os.environ["MCP_KG_PORT"] = "59103"
os.environ["MCP_RAG_PORT"] = "59102"
os.environ["MCP_CHECKPOINT_PORT"] = "59106"
os.environ["BRIEF_MCP_TIMEOUT"] = "2"

import brief_assemble as ba  # noqa: E402


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _fail(m: str) -> None:
    print(f"  FAIL: {m}")
    sys.exit(1)


PLAN = """# Project: hermes-max conductor

## Goal
Add an optional presence-gated conductor that routes hard subtasks to cloud help.

## Progress / Done so far
Stage 0 (eval) and Stage 1 (router) landed and committed.

## Directives / Constraints
Never swap the Hermes backend model. Cloud help is stateless tools only.
No secrets in any brief.

## Success Criteria / Definition of Done
brief_assemble produces compact/full/draft within budget; graceful degradation
holds with all sibling servers down.

## Misc notes
Some other content that should land in plan_other.
"""


def main() -> None:
    repo = os.path.join(_TMP, "repo")
    os.makedirs(repo, exist_ok=True)
    Path(repo, "PLAN.md").write_text(PLAN)

    blocker = "The role executor must never raise into the core loop."
    decision = "How should silent fall-through compose with the USD cap?"

    # 1. PLAN.md parsing maps sections correctly
    plan = ba._parse_plan(repo)
    if "presence-gated conductor" not in plan["goal"]:
        _fail(f"Goal not parsed: {plan['goal']!r}")
    if "Stage 0" not in plan["done_so_far"]:
        _fail(f"Done-so-far not parsed: {plan['done_so_far']!r}")
    if "Never swap" not in plan["original_directives"]:
        _fail(f"Directives not parsed: {plan['original_directives']!r}")
    if "within budget" not in plan["success_criteria"]:
        _fail(f"Success criteria not parsed: {plan['success_criteria']!r}")
    if "Misc notes" not in plan["plan_other"]:
        _fail(f"Unmatched section should land in plan_other: {plan['plan_other']!r}")
    _ok("PLAN.md sections parsed (goal/done/directives/success/plan_other)")

    # 2. full profile assembles with local model writing only blocker+decision
    full = ba.brief_assemble("task-1", blocker, decision, profile="full", repo=repo)
    b = full["brief"]
    if not full["ok"] or b["current_blocker"] != blocker or b["decision_needed"] != decision:
        _fail(f"local-only fields not carried verbatim: {b.get('current_blocker')!r}")
    if "presence-gated conductor" not in b["goal"]:
        _fail("assembled brief missing goal from PLAN.md")
    if full["directive_schema"].get("ordered_steps") is None:
        _fail("directive schema not attached")
    if "architecture_state" not in b:
        _fail("full profile should include architecture_state")
    _ok(f"full brief: local-only blocker/decision verbatim; goal+schema present; "
        f"{full['est_tokens']} tok")

    # 3. graceful degradation: sibling servers down -> kg/rag False, still ok
    sl = full["sources_live"]
    if not sl["plan_md"]:
        _fail(f"plan_md should be live (file present): {sl}")
    if sl["kg"] or sl["rag"] or sl["checkpoints"]:
        _fail(f"with sibling servers down, kg/rag/checkpoints must be False: {sl}")
    _ok(f"graceful degradation: servers down -> sources_live={sl}, brief still ok")

    # 4. compact < full in size, both within their ceilings
    compact = ba.brief_assemble("task-1", blocker, decision, profile="compact", repo=repo)
    if compact["size_chars"] > ba.PROFILES["compact"]["max_chars"]:
        _fail(f"compact exceeds its budget: {compact['size_chars']}")
    if full["size_chars"] > ba.PROFILES["full"]["max_chars"]:
        _fail(f"full exceeds its budget: {full['size_chars']}")
    _ok(f"profile budgets: compact {compact['size_chars']}c <= "
        f"{ba.PROFILES['compact']['max_chars']}, full {full['size_chars']}c within ceiling")

    # 5. draft profile carries acceptance_tests, omits architecture_state
    tests = ["assert add(2,3) == 5", "assert add(-1,1) == 0"]
    draft = ba.brief_assemble("task-1", "implement add(a,b)", "n/a", profile="draft",
                              repo=repo, acceptance_tests=tests)
    db = draft["brief"]
    if db.get("acceptance_tests") != tests:
        _fail(f"draft must carry acceptance_tests (the oracle): {db.get('acceptance_tests')}")
    if "architecture_state" in db:
        _fail("draft profile should omit architecture_state (verifiable subtask, no decomposition)")
    if draft["size_chars"] > ba.PROFILES["draft"]["max_chars"]:
        _fail(f"draft exceeds its tight budget: {draft['size_chars']}")
    _ok(f"draft brief: acceptance_tests carried, architecture_state omitted, "
        f"{draft['size_chars']}c (tight)")

    # 6. request_more: unknown section rejected; known section returns shape
    bad = ba.brief_request_more("task-1", "nonsense")
    if bad.get("ok"):
        _fail(f"unknown section should be rejected: {bad}")
    good = ba.brief_request_more("task-1", "code_excerpts", query="role executor")
    if not good.get("ok") or "items" not in good:
        _fail(f"request_more(code_excerpts) shape wrong: {good}")
    _ok("request_more: unknown section rejected; code_excerpts returns items (empty, servers down)")

    # 7. no PLAN.md -> still assembles (everything empty but ok)
    empty_repo = os.path.join(_TMP, "empty")
    os.makedirs(empty_repo, exist_ok=True)
    none = ba.brief_assemble("task-2", blocker, decision, profile="compact", repo=empty_repo)
    if not none["ok"] or none["sources_live"]["plan_md"]:
        _fail(f"no PLAN.md should still assemble with plan_md False: {none['sources_live']}")
    _ok("no PLAN.md present -> brief still assembles (all sources empty, ok)")

    # sanity: brief is JSON-serializable (it crosses the MCP boundary)
    json.dumps(full)
    _ok("brief is JSON-serializable")


if __name__ == "__main__":
    main()
    print("brief-assembler smoke test PASSED")
