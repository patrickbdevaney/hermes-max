#!/usr/bin/env python3
"""self_improve.py — the self-improvement loop (Phase 4.2 + 4.3), run as scheduled
BACKGROUND jobs, NEVER in the hot path, and NEVER auto-applied. Both read the
trajectory store (Phase 4.1) and write proposals to a HUMAN-GATED review queue
(~/.hermes-max/review-queue/) — like opening a PR, not committing.

  optimize  (4.2, GEPA-style): reflect over failed vs successful trajectories per
            skill and propose a sharper skill description/instruction that would
            prevent the failures. Reflective prompt evolution is the GEPA mechanism
            (ref: NousResearch/hermes-agent-self-evolution, DSPy+GEPA); implemented
            directly here against the steer tier for reliable headless operation.
  distill   (4.3, Trace2Skill): cluster recurring SUCCESSFUL trajectories; when a
            class of task was solved the same way 3+ times, propose a reusable skill
            (SOP) capturing that workflow.

Reflection routes to the conductor STEER tier (fast cloud), falling back to the local
model. Never raises; emits nothing to the hot path. Run with a venv that has `mcp`
(e.g. mcp-research/.venv) so the steer call works; degrades to local vLLM otherwise.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

TRAJ = Path(os.path.expanduser(os.environ.get("TRAJECTORY_DIR", "~/.hermes-max/trajectories"))) / "trajectories.jsonl"
QUEUE = Path(os.path.expanduser(os.environ.get("REVIEW_QUEUE_DIR", "~/.hermes-max/review-queue")))
SKILLS_DIR = Path(os.path.expanduser("~/.hermes/skills/hermes-max"))
VLLM = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
ESCALATION_URL = os.environ.get("ESCALATION_MCP_URL",
                                f"http://127.0.0.1:{os.environ.get('MCP_ESCALATION_PORT','9105')}/mcp")
MIN_CLUSTER = int(os.environ.get("TRACE2SKILL_MIN", "3"))


# ── reflection backend (steer tier → local fallback) ─────────────────────────
def _steer(prompt: str, max_tokens: int = 1500) -> str | None:
    async def _go():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        box = {}
        try:
            async with streamablehttp_client(ESCALATION_URL) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("conductor_steer", {"prompt": prompt, "max_tokens": max_tokens})
                    txt = getattr(res.content[0], "text", "") if res.content else ""
                    d = res.structuredContent or (json.loads(txt) if txt else {})
                    box["v"] = d.get("result", d) if isinstance(d, dict) else {}
        except BaseException:  # noqa: BLE001
            if "v" in box:
                return box["v"]
            raise
        return box["v"]
    try:
        import asyncio
        d = asyncio.run(asyncio.wait_for(_go(), timeout=90))
        if isinstance(d, dict) and not d.get("proceed_local") and d.get("content"):
            return str(d["content"]).strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _local(prompt: str, max_tokens: int = 4000) -> str | None:
    if not VLLM:
        return None
    body = json.dumps({"model": os.environ.get("VLLM_MODEL", "/model"),
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.2, "max_tokens": max_tokens}).encode()
    try:
        req = urllib.request.Request(f"{VLLM}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            c = json.loads(r.read())["choices"][0]["message"].get("content")
        return c.strip() if c else None
    except Exception:  # noqa: BLE001
        return None


def reflect(prompt: str) -> str | None:
    return _steer(prompt) or _local(prompt)


def _load() -> list[dict]:
    out = []
    try:
        for ln in open(TRAJ):
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:  # noqa: BLE001
                    pass
    except FileNotFoundError:
        pass
    return out


def _queue(name: str, content: str) -> Path:
    QUEUE.mkdir(parents=True, exist_ok=True)
    p = QUEUE / f"{int(time.time())}-{re.sub(r'[^a-z0-9-]+','-',name.lower())}.md"
    p.write_text(content)
    return p


def _skill_desc(skill: str) -> str:
    f = SKILLS_DIR / skill / "SKILL.md"
    try:
        m = re.search(r"description:\s*(>-|\|)?\s*\n?(.*?)\n---", f.read_text(), re.DOTALL)
        return " ".join(l.strip() for l in (m.group(2).splitlines() if m else []) if l.strip())[:500]
    except Exception:  # noqa: BLE001
        return ""


# ── 4.2 GEPA-style skill optimizer ───────────────────────────────────────────
def cmd_optimize() -> int:
    rows = _load()
    if not rows:
        print("no trajectories yet — nothing to optimize")
        return 0
    by_skill_fail = defaultdict(list)
    by_skill_ok = defaultdict(list)
    for r in rows:
        for sk in (r.get("skills_used") or []):
            (by_skill_fail if not r.get("success") else by_skill_ok)[sk].append(r)
    targets = [sk for sk, fails in by_skill_fail.items() if fails]
    if not targets:
        print("no skills associated with failures — nothing to propose")
        return 0
    proposed = 0
    for sk in targets:
        fails = by_skill_fail[sk][:8]
        oks = by_skill_ok.get(sk, [])[:5]
        prompt = (
            "You are GEPA, a reflective skill-prompt optimizer. A workflow skill's "
            "DESCRIPTION is its trigger signal; its body is its guidance. Below are agent "
            f"task trajectories where the skill '{sk}' was active.\n\n"
            f"CURRENT DESCRIPTION:\n{_skill_desc(sk)}\n\n"
            f"FAILED trajectories ({len(fails)}):\n"
            + "\n".join(f"- task: {f.get('task','')[:160]} | failure_mode: {f.get('failure_mode','')} "
                        f"| outcome: {f.get('outcome','')[:120]}" for f in fails)
            + (f"\n\nSUCCESSFUL trajectories ({len(oks)}):\n"
               + "\n".join(f"- task: {o.get('task','')[:140]}" for o in oks) if oks else "")
            + "\n\nReflect on WHY the failures happened and propose a SHARPER skill description "
              "(the trigger) and/or one concrete guidance edit that would have prevented them. "
              "Output exactly:\nPROPOSED DESCRIPTION: <one sharp sentence>\nRATIONALE: <2-3 sentences "
              "tying it to the failures>\nGUIDANCE EDIT: <one concrete added/changed instruction>")
        out = reflect(prompt)
        if not out:
            continue
        content = (f"# Skill-optimizer proposal — `{sk}`\n\n"
                   f"_GEPA-style reflection over {len(fails)} failure(s) / {len(oks)} success(es). "
                   f"HUMAN-GATED: review and apply manually (do not auto-merge)._\n\n"
                   f"## Current description\n{_skill_desc(sk)}\n\n## Proposal\n{out}\n")
        p = _queue(f"optimize-{sk}", content)
        print(f"  proposed: {p}")
        proposed += 1
    print(f"optimize: {proposed} proposal(s) written to {QUEUE}")
    return 0


# ── 4.3 Trace2Skill distillation ─────────────────────────────────────────────
def _sig(r: dict) -> tuple:
    """A coarse signature: the ordered set of tool names used (the 'how')."""
    tools = []
    for tc in (r.get("tool_calls") or []):
        t = tc.get("tool") if isinstance(tc, dict) else str(tc)
        if t:
            tools.append(t)
    return tuple(tools)


def cmd_distill() -> int:
    succ = [r for r in _load() if r.get("success")]
    clusters = defaultdict(list)
    for r in succ:
        clusters[_sig(r)].append(r)
    proposed = 0
    for sig, group in clusters.items():
        if len(group) < MIN_CLUSTER or not sig:
            continue
        prompt = (
            "You distill recurring successful agent workflows into reusable SKILLS (SOPs). "
            f"The following {len(group)} tasks were ALL solved with the same tool sequence "
            f"{list(sig)}:\n"
            + "\n".join(f"- {g.get('task','')[:160]}" for g in group[:8])
            + "\n\nPropose a reusable skill that captures this workflow. Output exactly:\n"
              "SKILL NAME: workflow-<kebab>\nDESCRIPTION: <one sentence trigger — WHEN to use it>\n"
              "STEPS: <numbered SOP of the tool sequence and decision points>")
        out = reflect(prompt)
        if not out:
            continue
        content = (f"# Trace2Skill candidate (from {len(group)} similar successes)\n\n"
                   f"_Tool signature: {list(sig)}. HUMAN-GATED: review, refine, and install "
                   f"manually._\n\n## Example tasks\n"
                   + "\n".join(f"- {g.get('task','')[:160]}" for g in group[:8])
                   + f"\n\n## Proposed skill\n{out}\n")
        p = _queue("distill-candidate", content)
        print(f"  candidate: {p}")
        proposed += 1
    if not proposed:
        print(f"distill: no task class solved the same way >= {MIN_CLUSTER} times yet")
    else:
        print(f"distill: {proposed} candidate skill(s) written to {QUEUE}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "optimize"
    sys.exit(cmd_optimize() if cmd == "optimize" else cmd_distill() if cmd == "distill"
             else (print(f"usage: self_improve.py [optimize|distill]"), 2)[1])
