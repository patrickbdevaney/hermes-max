"""One embedded SQLite triples store: entities + (src, rel, dst) relations.

This is the persistence layer that beats a cold-start agent: a queryable model
of YOUR codebase's decisions, bugs, services and structure that survives across
all sessions. The agent writes facts at task end and reads them at task start.

Deliberately NOT built: Neo4j + Graphiti + Cognee (three services). One file,
two tables, deterministic queries.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.path.expanduser(os.environ.get("KG_DB_PATH", "~/.hermes-max/kg/graph.db"))

# ── self-editing core memory (MemGPT pattern, WIRED TO Hermes's native memory) ─
# Discovery-first: Hermes already keeps an always-in-context, char-limited
# MEMORY.md (built-in memory, memory_char_limit ~2200). Rather than duplicate
# that always-in-context block, these tools let the agent DELIBERATELY curate the
# SAME native file — the explicit get/append/replace control the passive native
# monitor doesn't expose (MemGPT's "let the model own its working memory"). Single
# source of truth: Hermes auto-loads MEMORY.md into context, so edits show up
# there with no parallel store. Size-bounded to protect the window.
HERMES_MEMORY_PATH = os.path.expanduser(
    os.environ.get("HERMES_MEMORY_PATH", "~/.hermes/MEMORY.md"))
CORE_MEMORY_CHAR_LIMIT = int(os.environ.get("CORE_MEMORY_CHAR_LIMIT", "2200"))

try:
    import otel_emit  # best-effort core_memory_edited span
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*a, **k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    from pathlib import Path

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS entities(
            name TEXT PRIMARY KEY,
            type TEXT,
            props TEXT,
            created_at TEXT,
            updated_at TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS relations(
            id INTEGER PRIMARY KEY,
            src TEXT NOT NULL,
            rel TEXT NOT NULL,
            dst TEXT NOT NULL,
            props TEXT,
            created_at TEXT,
            UNIQUE(src, rel, dst))"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_rel_src ON relations(src)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_rel_dst ON relations(dst)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_rel_rel ON relations(rel)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ent_type ON entities(type)")
    con.commit()
    return con


def _ent_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "type": row["type"],
        "props": json.loads(row["props"]) if row["props"] else {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _rel_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "src": row["src"],
        "rel": row["rel"],
        "dst": row["dst"],
        "props": json.loads(row["props"]) if row["props"] else {},
        "created_at": row["created_at"],
    }


def record_entity(type: str, name: str, props: dict | None = None) -> dict[str, Any]:
    """Upsert an entity. Props are merged into any existing props (shallow)."""
    props = props or {}
    con = _connect()
    try:
        existing = con.execute("SELECT * FROM entities WHERE name=?", (name,)).fetchone()
        now = _now()
        if existing:
            merged = json.loads(existing["props"]) if existing["props"] else {}
            merged.update(props)
            con.execute(
                "UPDATE entities SET type=?, props=?, updated_at=? WHERE name=?",
                (type or existing["type"], json.dumps(merged), now, name),
            )
        else:
            con.execute(
                "INSERT INTO entities(name, type, props, created_at, updated_at) VALUES(?,?,?,?,?)",
                (name, type, json.dumps(props), now, now),
            )
        con.commit()
        row = con.execute("SELECT * FROM entities WHERE name=?", (name,)).fetchone()
        return {"ok": True, "entity": _ent_dict(row)}
    finally:
        con.close()


def _ensure_entity(con: sqlite3.Connection, name: str) -> None:
    row = con.execute("SELECT name FROM entities WHERE name=?", (name,)).fetchone()
    if not row:
        now = _now()
        con.execute(
            "INSERT INTO entities(name, type, props, created_at, updated_at) VALUES(?,?,?,?,?)",
            (name, "entity", "{}", now, now),
        )


def record_relation(a: str, rel: str, b: str, props: dict | None = None) -> dict[str, Any]:
    """Record a directed relation (a)-[rel]->(b). Missing endpoints are created
    as stub entities of type 'entity' so recording never fails on order."""
    props = props or {}
    con = _connect()
    try:
        _ensure_entity(con, a)
        _ensure_entity(con, b)
        now = _now()
        con.execute(
            "INSERT INTO relations(src, rel, dst, props, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(src, rel, dst) DO UPDATE SET props=excluded.props",
            (a, rel, b, json.dumps(props), now),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM relations WHERE src=? AND rel=? AND dst=?", (a, rel, b)
        ).fetchone()
        return {"ok": True, "relation": _rel_dict(row)}
    finally:
        con.close()


def query_graph(
    subject: str | None = None,
    rel: str | None = None,
    obj: str | None = None,
    type: str | None = None,
    contains: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Flexible query.

    - subject / rel / obj: triple-pattern over relations (any subset; omitted =
      wildcard). Values are matched exactly.
    - type: filter entities by type.
    - contains: substring match on entity name.
    Returns matching entities and relations.
    """
    con = _connect()
    try:
        relations: list[dict[str, Any]] = []
        if subject or rel or obj:
            clauses, params = [], []
            if subject:
                clauses.append("src=?")
                params.append(subject)
            if rel:
                clauses.append("rel=?")
                params.append(rel)
            if obj:
                clauses.append("dst=?")
                params.append(obj)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            rows = con.execute(
                f"SELECT * FROM relations{where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
            relations = [_rel_dict(r) for r in rows]

        entities: list[dict[str, Any]] = []
        if type or contains:
            clauses, params = [], []
            if type:
                clauses.append("type=?")
                params.append(type)
            if contains:
                clauses.append("name LIKE ?")
                params.append(f"%{contains}%")
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            rows = con.execute(
                f"SELECT * FROM entities{where} ORDER BY updated_at DESC LIMIT ?", params
            ).fetchall()
            entities = [_ent_dict(r) for r in rows]

        return {"ok": True, "entities": entities, "relations": relations}
    finally:
        con.close()


def recall_about(name: str) -> dict[str, Any]:
    """Everything known about an entity: its record + outgoing + incoming
    relations, each annotated with the connected entity's type. This is the
    task-start recall call."""
    con = _connect()
    try:
        ent = con.execute("SELECT * FROM entities WHERE name=?", (name,)).fetchone()
        out_rows = con.execute(
            "SELECT * FROM relations WHERE src=? ORDER BY id DESC", (name,)
        ).fetchall()
        in_rows = con.execute(
            "SELECT * FROM relations WHERE dst=? ORDER BY id DESC", (name,)
        ).fetchall()

        def annotate(rows: list[sqlite3.Row], other_key: str) -> list[dict[str, Any]]:
            result = []
            for r in rows:
                d = _rel_dict(r)
                other = con.execute(
                    "SELECT type FROM entities WHERE name=?", (d[other_key],)
                ).fetchone()
                d["neighbor_type"] = other["type"] if other else None
                result.append(d)
            return result

        return {
            "ok": True,
            "found": ent is not None,
            "entity": _ent_dict(ent) if ent else {"name": name, "type": None, "props": {}},
            "outgoing": annotate(out_rows, "dst"),
            "incoming": annotate(in_rows, "src"),
        }
    finally:
        con.close()


def stats() -> dict[str, Any]:
    con = _connect()
    try:
        ne = con.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
        nr = con.execute("SELECT COUNT(*) AS n FROM relations").fetchone()["n"]
        core_chars = 0
        if os.path.isfile(HERMES_MEMORY_PATH):
            try:
                core_chars = len(open(HERMES_MEMORY_PATH).read())
            except Exception:  # noqa: BLE001
                core_chars = -1
        return {"entities": ne, "relations": nr, "db_path": DB_PATH,
                "core_memory_path": HERMES_MEMORY_PATH, "core_memory_chars": core_chars,
                "core_memory_limit": CORE_MEMORY_CHAR_LIMIT}
    finally:
        con.close()


# ── self-editing core memory ─────────────────────────────────────────────────
def _read_core() -> str:
    if os.path.isfile(HERMES_MEMORY_PATH):
        try:
            with open(HERMES_MEMORY_PATH) as f:
                return f.read()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _write_core(text: str) -> None:
    from pathlib import Path

    Path(HERMES_MEMORY_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = HERMES_MEMORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, HERMES_MEMORY_PATH)


def core_memory_get() -> dict[str, Any]:
    """Return the agent-curated core-memory block (Hermes's native MEMORY.md) and
    its char usage vs the bound — the highest-signal facts kept always-in-context."""
    text = _read_core()
    return {"ok": True, "path": HERMES_MEMORY_PATH, "content": text, "chars": len(text),
            "limit": CORE_MEMORY_CHAR_LIMIT, "over_limit": len(text) > CORE_MEMORY_CHAR_LIMIT}


def core_memory_append(fact: str) -> dict[str, Any]:
    """Append ONE high-signal fact (a convention, gotcha, the architecture
    one-liner) to core memory. Enforces the char limit: if the append would
    overflow the always-in-context block, it's REJECTED with a nudge to
    core_memory_replace (prune stale facts first) — protecting the window."""
    fact = (fact or "").strip()
    if not fact:
        return {"ok": False, "error": "empty fact"}
    cur = _read_core()
    bullet = fact if fact.startswith(("- ", "* ", "#")) else f"- {fact}"
    new = (cur.rstrip() + "\n" + bullet + "\n") if cur.strip() else (bullet + "\n")
    if len(new) > CORE_MEMORY_CHAR_LIMIT:
        return {"ok": False, "error": "would exceed core-memory char limit",
                "chars": len(cur), "would_be": len(new), "limit": CORE_MEMORY_CHAR_LIMIT,
                "hint": "prune with core_memory_replace (evict stale facts) first"}
    _write_core(new)
    otel_emit.record("core_memory_edited", {"op": "append", "chars": len(new),
                                            "limit": CORE_MEMORY_CHAR_LIMIT})
    return {"ok": True, "op": "append", "chars": len(new), "limit": CORE_MEMORY_CHAR_LIMIT,
            "appended": bullet}


def core_memory_replace(old: str | None = None, new: str | None = None,
                        block: str | None = None) -> dict[str, Any]:
    """Deliberately edit core memory — MemGPT's "agent owns its working memory".
    Either substring-replace (old → new) for a targeted prune/update, OR pass
    `block` to replace the ENTIRE core-memory block (the task-boundary curation
    pass). Enforces the char limit; rejects an over-limit result."""
    if block is not None:
        text = block.strip() + "\n"
        op = "replace_block"
    else:
        if old is None:
            return {"ok": False, "error": "provide old (+new), or block"}
        cur = _read_core()
        if old not in cur:
            return {"ok": False, "error": "old text not found in core memory"}
        text = cur.replace(old, new or "")
        text = "\n".join(ln for ln in text.splitlines() if ln.strip() != "")
        text = (text + "\n") if text else ""
        op = "replace"
    if len(text) > CORE_MEMORY_CHAR_LIMIT:
        return {"ok": False, "error": "result exceeds core-memory char limit",
                "chars": len(text), "limit": CORE_MEMORY_CHAR_LIMIT}
    _write_core(text)
    otel_emit.record("core_memory_edited", {"op": op, "chars": len(text),
                                            "limit": CORE_MEMORY_CHAR_LIMIT})
    return {"ok": True, "op": op, "chars": len(text), "limit": CORE_MEMORY_CHAR_LIMIT}
