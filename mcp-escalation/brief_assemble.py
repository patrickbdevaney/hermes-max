"""Deterministic brief-assembler (Stage 2) — the weak local model must NOT
hand-write the brief it sends to a stronger cloud model.

This module DETERMINISTICALLY pulls harness state into a structured brief:
  goal / done_so_far / constraints / success_criteria  <- PLAN.md (repo) sections
  done checkpoints                                      <- checkpoint list (MCP, optional)
  architecture_state                                    <- KG decisions (MCP, optional)
  failed_approaches  (so the cloud won't re-suggest)    <- KG + watchdog stuck-state
  code_excerpts (token-budgeted)                        <- codebase-rag (MCP, optional)
The LOCAL model writes ONLY two fields, passed in as params: `current_blocker`
and `decision_needed`. Everything else is assembled from ground truth.

Three profiles: compact (<=~8K tok, for steer), full (~15-30K tok, for synth),
draft (tight task-spec, for parallel_draft = the verifiable subtask + its
acceptance tests + minimal context). Progressive disclosure via brief_request_more.

GRACEFUL DEGRADATION is a hard requirement: every external pull is wrapped; if a
server is down or a file is missing, that section is simply empty/marked
unavailable and the brief still assembles. Never raises.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 4
HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
KG_PORT = os.environ.get("MCP_KG_PORT", "9103")
RAG_PORT = os.environ.get("MCP_RAG_PORT", "9102")
CHECKPOINT_PORT = os.environ.get("MCP_CHECKPOINT_PORT", "9106")
WATCHDOG_STATE_DIR = os.path.expanduser(
    os.environ.get("WATCHDOG_STATE_DIR", "~/.hermes-max/watchdog"))
MCP_TIMEOUT = float(os.environ.get("BRIEF_MCP_TIMEOUT", "20"))

# The STRUCTURED DIRECTIVE the cloud must return (shared with Stage-3 verify).
DIRECTIVE_SCHEMA: dict[str, Any] = {
    "ordered_steps": [{"step": "<concrete action>", "confidence": "high|medium|low"}],
    "files_to_touch": ["<path or component>"],
    "apis_to_use": ["<function/class/endpoint to call>"],
    "tests_to_write": ["<assertion to write FIRST>"],
    "pitfalls": ["<a concrete failure mode to avoid>"],
    "assumptions": ["<a checkable fact about the repo you assumed>"],
}

# Per-profile budgets (chars). RAG excerpts absorb the flexible remainder.
PROFILES: dict[str, dict[str, int]] = {
    "compact": {"max_chars": 32_000, "rag_k": 4, "code_chars": 9_000,
                "decisions": 6, "failed": 6, "plan_chars": 6_000},
    "full":    {"max_chars": 110_000, "rag_k": 10, "code_chars": 60_000,
                "decisions": 20, "failed": 20, "plan_chars": 24_000},
    "draft":   {"max_chars": 12_000, "rag_k": 3, "code_chars": 4_000,
                "decisions": 0, "failed": 6, "plan_chars": 1_500},
}


def _est_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


# ── MCP client (sync wrapper; returns None on ANY failure — never raises) ─────
async def _mcp_async(url: str, tool: str, args: dict) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            data = res.structuredContent or (
                json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and len(data) == 1:
                data = data["result"]
            return data


def _mcp(port: str, tool: str, args: dict) -> dict | None:
    try:
        return asyncio.run(asyncio.wait_for(
            _mcp_async(f"http://{HOST}:{port}/mcp", tool, args), timeout=MCP_TIMEOUT))
    except Exception:  # noqa: BLE001 - server down/absent -> section simply empty
        return None


# ── PLAN.md section parsing (file-based; no server needed) ────────────────────
_SECTION_KEYS = {
    "goal": ("goal", "objective", "mission"),
    "done_so_far": ("progress", "status", "completed", "so far", "done"),
    "original_directives": ("directive", "constraint", "requirement", "must", "rules"),
    "success_criteria": ("success", "acceptance", "definition of done", "dod",
                         "criteria", "verify"),
}
# Match in PRIORITY order, not dict order: 'success_criteria' must win over
# 'done_so_far' for a "Definition of Done" header (which contains "done").
_MATCH_ORDER = ("goal", "success_criteria", "original_directives", "done_so_far")


def _parse_plan(repo: str | None) -> dict[str, str]:
    repo = repo or os.getcwd()
    path = Path(repo) / "PLAN.md"
    out: dict[str, str] = {k: "" for k in _SECTION_KEYS}
    out["plan_other"] = ""
    try:
        text = path.read_text()
    except Exception:  # noqa: BLE001
        return out
    # split on markdown headers, keep header text to classify the block
    blocks = re.split(r"(?m)^(#{1,6})\s+(.*)$", text)
    # re.split yields [pre, hashes, title, body, hashes, title, body, ...]
    i = 1
    if blocks and blocks[0].strip():
        out["plan_other"] += blocks[0].strip() + "\n"
    while i + 2 < len(blocks) + 1 and i + 1 < len(blocks):
        title = (blocks[i + 1] or "").strip().lower()
        body = (blocks[i + 2] if i + 2 < len(blocks) else "").strip()
        matched = None
        for key in _MATCH_ORDER:
            if any(kw in title for kw in _SECTION_KEYS[key]):
                matched = key
                break
        if matched:
            out[matched] += (f"### {blocks[i + 1].strip()}\n{body}\n").strip() + "\n"
        else:
            out["plan_other"] += (f"### {blocks[i + 1].strip()}\n{body}\n").strip() + "\n"
        i += 3
    return out


# ── gatherers (each degrades to empty on failure) ─────────────────────────────
def _gather_checkpoints(repo: str | None, n: int = 6) -> list[dict]:
    r = _mcp(CHECKPOINT_PORT, "list_checkpoints", {"n": n, "repo_path": repo})
    if r and r.get("ok"):
        return r.get("checkpoints", [])
    return []


def _gather_decisions(limit: int) -> list[dict]:
    if limit <= 0:
        return []
    r = _mcp(KG_PORT, "query_graph", {"type": "decision", "limit": limit})
    ents = (r or {}).get("entities", []) if r else []
    return [{"name": e.get("name"), "props": e.get("props", {})} for e in ents]


def _gather_failed(limit: int) -> list[dict]:
    """Failed approaches from the KG (type=failed_approach, or rel=failed) so the
    cloud is told NOT to re-suggest them."""
    if limit <= 0:
        return []
    out: list[dict] = []
    r1 = _mcp(KG_PORT, "query_graph", {"type": "failed_approach", "limit": limit})
    for e in (r1 or {}).get("entities", []) if r1 else []:
        out.append({"approach": e.get("name"), "why": (e.get("props") or {}).get("reason", "")})
    r2 = _mcp(KG_PORT, "query_graph", {"rel": "failed", "limit": limit})
    for rel in (r2 or {}).get("relations", []) if r2 else []:
        out.append({"approach": rel.get("src"), "why": (rel.get("props") or {}).get("reason",
                    f"failed -> {rel.get('dst')}")})
    # de-dupe by approach name, cap
    seen, deduped = set(), []
    for f in out:
        k = f.get("approach")
        if k and k not in seen:
            seen.add(k)
            deduped.append(f)
    return deduped[:limit]


def _gather_watchdog(task_id: str | None) -> dict[str, Any]:
    """Read the watchdog stuck-state file directly (no server needed)."""
    if not task_id:
        return {}
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", task_id)
    path = Path(WATCHDOG_STATE_DIR) / f"{safe}.json"
    try:
        st = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    prog = st.get("progress", {})
    return {"no_progress_count": prog.get("no_progress_count", 0),
            "last_signals": prog.get("last", {}),
            "stuck": prog.get("no_progress_count", 0) >= 2}


def _gather_code(query: str, k: int, char_budget: int) -> tuple[list[dict], int]:
    """Token-budgeted code excerpts from codebase-rag. Returns (kept, total_found)."""
    r = _mcp(RAG_PORT, "search_code", {"query": query, "k": k})
    results = (r or {}).get("results", []) if r else []
    kept, used = [], 0
    for res in results:
        snip = res.get("snippet", "")
        entry = {"location": res.get("location"), "symbol": res.get("symbol"),
                 "lang": res.get("lang"), "snippet": snip}
        if used + len(snip) > char_budget and kept:  # keep at least one
            break
        used += len(snip)
        kept.append(entry)
    return kept, len(results)


# ── the assembler ─────────────────────────────────────────────────────────────
def brief_assemble(task_id: str, current_blocker: str, decision_needed: str,
                   *, profile: str = "full", repo: str | None = None,
                   query: str | None = None, directives: str | None = None,
                   acceptance_tests: list[str] | None = None) -> dict[str, Any]:
    """Assemble a structured brief. The LOCAL model supplies ONLY current_blocker
    and decision_needed (passed in); everything else is pulled deterministically.

    profile: 'compact' (steer), 'full' (synth), 'draft' (parallel_draft). For
    'draft', pass acceptance_tests — the objective oracle the verifier will use.
    Never raises; missing servers/files yield empty sections (graceful)."""
    prof = PROFILES.get(profile, PROFILES["full"])
    repo = repo or os.getcwd()
    query = query or f"{current_blocker} {decision_needed}".strip()

    plan = _parse_plan(repo)
    # trim plan sections to the profile's plan_chars (split across the 4 sections)
    per = max(400, prof["plan_chars"] // 4)
    for k in ("goal", "done_so_far", "original_directives", "success_criteria"):
        if len(plan[k]) > per:
            plan[k] = plan[k][:per] + "\n[...trimmed...]"

    decisions = _gather_decisions(prof["decisions"])
    failed = _gather_failed(prof["failed"])
    watchdog = _gather_watchdog(task_id)
    if watchdog.get("stuck") and watchdog.get("last_signals"):
        failed.append({"approach": "(watchdog) no measurable progress recently",
                       "why": f"signals stalled: {watchdog['last_signals']}"})
    code, code_total = _gather_code(query, prof["rag_k"], prof["code_chars"])
    checkpoints = _gather_checkpoints(repo) if profile != "draft" else []

    brief: dict[str, Any] = {
        "task_id": task_id,
        "profile": profile,
        # the ONLY two fields the local model wrote:
        "current_blocker": current_blocker,
        "decision_needed": decision_needed,
        # everything below is assembled from ground truth:
        "goal": plan["goal"] or "(no Goal section in PLAN.md)",
        "done_so_far": plan["done_so_far"],
        "recent_checkpoints": checkpoints,
        "original_directives": directives or plan["original_directives"],
        "constraints": plan["original_directives"],
        "success_criteria": plan["success_criteria"],
        "failed_approaches": failed,
        "code_excerpts": code,
    }
    if profile != "draft":
        brief["architecture_state"] = decisions
    if profile == "draft":
        brief["acceptance_tests"] = acceptance_tests or []
        brief["note"] = ("VERIFIABLE subtask: implement so the acceptance tests pass. "
                         "The deterministic verifier selects the winning draft.")

    # progressive disclosure: what was capped + how to ask for more
    expansions = {
        "code_excerpts": {"included": len(code), "found": code_total,
                          "more": code_total > len(code)},
        "failed_approaches": {"included": len(failed)},
        "architecture_state": {"included": len(decisions)},
    }

    payload = {"directive_schema": DIRECTIVE_SCHEMA, "brief": brief,
               "expansions": expansions}
    text = json.dumps(payload)
    truncated: list[str] = []
    # final hard budget: if over the profile ceiling, drop code excerpts tail first
    while len(text) > prof["max_chars"] and brief["code_excerpts"]:
        brief["code_excerpts"].pop()
        truncated.append("code_excerpt")
        text = json.dumps(payload)

    return {"ok": True, "profile": profile, "brief": brief,
            "directive_schema": DIRECTIVE_SCHEMA, "expansions": expansions,
            "request_more_with": "brief_request_more(task_id, section, query)",
            "size_chars": len(text), "est_tokens": _est_tokens(text),
            "truncated": truncated,
            "sources_live": {"plan_md": bool(plan["goal"] or plan["plan_other"]),
                             "kg": bool(decisions or failed),
                             "rag": bool(code), "checkpoints": bool(checkpoints),
                             "watchdog": bool(watchdog)}}


def brief_request_more(task_id: str, section: str, *, query: str = "",
                       k: int = 8, offset: int = 0, repo: str | None = None) -> dict[str, Any]:
    """Progressive disclosure: fetch MORE of one section the brief capped.
    section: 'code_excerpts' | 'failed_approaches' | 'architecture_state'."""
    section = (section or "").strip()
    if section == "code_excerpts":
        r = _mcp(RAG_PORT, "search_code", {"query": query or task_id, "k": k + offset})
        results = (r or {}).get("results", []) if r else []
        more = results[offset:offset + k]
        return {"ok": True, "section": section, "items": more,
                "has_more": len(results) > offset + k}
    if section == "failed_approaches":
        return {"ok": True, "section": section, "items": _gather_failed(k + offset)[offset:]}
    if section == "architecture_state":
        return {"ok": True, "section": section, "items": _gather_decisions(k + offset)[offset:]}
    return {"ok": False, "error": f"unknown section '{section}'",
            "sections": ["code_excerpts", "failed_approaches", "architecture_state"]}
