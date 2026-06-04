"""conventions.py — categorized project-convention memory for mcp-knowledge-graph.

Typed convention facts {category, data, tags, scope} in a dedicated table, queryable with a
'*' wildcard and deduped by content hash — durable, queryable, and compounding, vs. re-reading
raw SKILLS.md as flat text each run. Lives alongside the entity/relation store; `propose_skill`
saves reusable CODE, this saves architectural facts and workflow RULES. Degrades silently when
the KG db is unavailable. Never raises.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import kg_core  # reuse DB_PATH + _connect()

CONVENTION_CATEGORIES = {
    "decision":       "Architecture decisions that must not be revisited",
    "protected_file": "Files the agent must not modify",
    "test_command":   "Commands to run for verification",
    "entry_point":    "Key module/function entry points",
    "workflow":       "Recurring workflow patterns or preferences",
    "custom":         "Operator-defined free-form conventions",
}

# SKILLS.md section header → convention category (unknown sections → 'custom').
_SECTION_TO_CATEGORY: list[tuple[Any, str]] = [
    (re.compile(r"architecture decision", re.I), "decision"),
    (re.compile(r"(do not modify|protected file|don'?t touch|must not modify)", re.I), "protected_file"),
    (re.compile(r"test command", re.I), "test_command"),
    (re.compile(r"(entry point|key entr)", re.I), "entry_point"),
    (re.compile(r"workflow", re.I), "workflow"),
]


def _ensure_table(con) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS agent_conventions ("
        "hash TEXT PRIMARY KEY, category TEXT, data TEXT, tags TEXT, scope TEXT, ts REAL)")


def _hash(category: str, data: str, scope: str) -> str:
    return hashlib.sha1(f"{category}|{scope}|{data}".encode()).hexdigest()[:16]


def parse_skills_md(path) -> list[dict]:
    """Parse a SKILLS.md into [{category, data}] — every bullet item under a known section
    header, mapped to a convention category. Pure/offline; returns [] on a missing file."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    facts: list[dict] = []
    category: Optional[str] = None
    for line in text.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)", line)
        if h:
            title = h.group(1).strip()
            category = "custom"
            for rx, cat in _SECTION_TO_CATEGORY:
                if rx.search(title):
                    category = cat
                    break
            continue
        b = re.match(r"^\s*[-*]\s+(.+)", line)
        if b and category:
            data = b.group(1).strip()
            if data:
                facts.append({"category": category, "data": data})
    return facts


def save_convention(category: str, data: str, tags: Optional[list] = None,
                    scope: str = "global") -> dict:
    """Save a convention fact. Idempotent on (category, scope, data) content hash."""
    category = (category or "custom").strip().lower()
    data = (data or "").strip()
    if not data:
        return {"saved": False, "reason": "empty data"}
    if category not in CONVENTION_CATEGORIES:
        category = "custom"
    h = _hash(category, data, scope)
    try:
        con = kg_core._connect()
        _ensure_table(con)
        if con.execute("SELECT 1 FROM agent_conventions WHERE hash=?", (h,)).fetchone():
            con.close()
            return {"saved": False, "hash": h, "reason": "already present"}
        con.execute("INSERT INTO agent_conventions VALUES (?,?,?,?,?,?)",
                    (h, category, data, json.dumps(tags or []), scope, time.time()))
        con.commit()
        con.close()
        return {"saved": True, "hash": h, "reason": "saved"}
    except Exception as e:  # noqa: BLE001 - degrade silently
        return {"saved": False, "reason": f"kg unavailable: {str(e)[:120]}"}


def get_conventions(category: str = "*", scope: str = "*",
                    tags: Optional[list] = None) -> list[dict]:
    """Retrieve convention facts (most-recent first). category='*' returns all; scope!='*'
    returns that scope PLUS global. Returns [] when the KG is unavailable."""
    try:
        con = kg_core._connect()
        _ensure_table(con)
        q = "SELECT category, data, tags, scope, ts FROM agent_conventions"
        conds, args = [], []
        if category != "*":
            conds.append("category=?")
            args.append(category.strip().lower())
        if scope != "*":
            conds.append("(scope=? OR scope='global')")
            args.append(scope)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY ts DESC"
        rows = con.execute(q, args).fetchall()
        con.close()
        out = [{"category": c, "data": d, "tags": json.loads(t or "[]"), "scope": s}
               for c, d, t, s, _ in rows]
        if tags:
            want = set(tags)
            out = [o for o in out if want & set(o["tags"])]
        return out
    except Exception:  # noqa: BLE001
        return []


def sync_skills_md(cwd, scope: str = "global") -> dict:
    """Parse <cwd>/SKILLS.md and upsert its items as convention facts (idempotent by hash).
    Returns {written, total, path}. Degrades silently if the KG is down."""
    p = os.path.join(str(cwd), "SKILLS.md")
    facts = parse_skills_md(p)
    written = sum(1 for f in facts if save_convention(f["category"], f["data"], scope=scope).get("saved"))
    return {"written": written, "total": len(facts), "path": p}
