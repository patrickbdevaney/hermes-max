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
        return {"entities": ne, "relations": nr, "db_path": DB_PATH}
    finally:
        con.close()
