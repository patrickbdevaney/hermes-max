"""OPTIONAL Neo4j backend for the KG — power-user swap, NOT the default.

The prime directive ships ONE embedded SQLite store (kg_core) as the default and
the only thing bootstrap needs. This module is the sanctioned OPTIONAL backend:
selected with KG_BACKEND=neo4j, it persists the SAME logical schema (entities,
(src,rel,dst) edges, props carrying provenance / temporal validity / citation
edges) to a Neo4j server instead of the local file.

It is LAZILY imported by kg_core ONLY when KG_BACKEND=neo4j, and the `neo4j`
driver is deliberately NOT in requirements.txt — so the base install stays lean
(no graph DB, no heavy dep) and the lean/torch-free guarantee is untouched. A
power user opts in with `pip install neo4j` + a running server; if the driver or
server is absent, kg_core.connect() raises and the store falls back to embedded.

Public surface mirrors kg_core EXACTLY (same signatures + return shapes) so the
backend is a config change, never a schema or caller change:
    connect(), record_entity(), record_relation(), query_graph(),
    recall_about(), stats()
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

_driver = None  # cached neo4j.Driver


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect():
    """Open (and cache) the Neo4j driver, verifying connectivity + ensuring the
    schema constraints/indexes. Raises if the `neo4j` driver is not installed or
    the server is unreachable — the caller (kg_core) catches this and falls back
    to the embedded store. Idempotent."""
    global _driver
    if _driver is not None:
        return _driver
    import neo4j  # lazy: only imported on the opt-in path; not a base dependency

    drv = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    drv.verify_connectivity()  # raises if the server is down / auth wrong
    with drv.session(database=NEO4J_DATABASE) as s:
        s.run("CREATE CONSTRAINT entity_name IF NOT EXISTS "
              "FOR (e:Entity) REQUIRE e.name IS UNIQUE")
        s.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")
    _driver = drv
    return _driver


def _session():
    return connect().session(database=NEO4J_DATABASE)


def _ent_dict(node: Any) -> dict[str, Any]:
    return {
        "name": node.get("name"),
        "type": node.get("type"),
        "props": json.loads(node.get("props") or "{}"),
        "created_at": node.get("created_at"),
        "updated_at": node.get("updated_at"),
    }


def _rel_dict(rec: Any) -> dict[str, Any]:
    return {
        "src": rec["src"],
        "rel": rec["rel"],
        "dst": rec["dst"],
        "props": json.loads(rec.get("props") or "{}"),
        "created_at": rec.get("created_at"),
    }


def record_entity(type: str, name: str, props: dict | None = None) -> dict[str, Any]:
    """Upsert an entity; props shallow-merged into existing (matches kg_core).

    Cypher can't shallow-merge a JSON-string prop bag portably without APOC, so we
    ensure the node exists (create-only sets), then read-merge-write the props in
    one helper — exact parity with kg_core's UPDATE-merge semantics, APOC-free."""
    props = props or {}
    now = _now()
    with _session() as s:
        s.run(
            """MERGE (e:Entity {name: $name})
               ON CREATE SET e.type=$type, e.props='{}', e.created_at=$now, e.updated_at=$now""",
            name=name, type=type, now=now,
        )
    return _merge_props(name, props, now, type)


def _merge_props(name: str, props: dict, now: str, type: str) -> dict[str, Any]:
    with _session() as s:
        cur = s.run("MATCH (e:Entity {name:$name}) RETURN e", name=name).single()
        existing = json.loads(cur["e"].get("props") or "{}") if cur else {}
        existing.update(props)
        rec = s.run(
            """MATCH (e:Entity {name:$name})
               SET e.props=$props, e.updated_at=$now, e.type=coalesce(e.type,$type)
               RETURN e""",
            name=name, props=json.dumps(existing), now=now, type=type,
        ).single()
        return {"ok": True, "entity": _ent_dict(rec["e"])}


def record_relation(a: str, rel: str, b: str, props: dict | None = None) -> dict[str, Any]:
    """Record (a)-[:REL {rel}]->(b). rel kept as an edge PROPERTY (not the Neo4j
    type) so arbitrary relation strings work; endpoints auto-created as stubs."""
    props = props or {}
    now = _now()
    with _session() as s:
        rec = s.run(
            """MERGE (x:Entity {name:$a})
                 ON CREATE SET x.type='entity', x.props='{}', x.created_at=$now, x.updated_at=$now
               MERGE (y:Entity {name:$b})
                 ON CREATE SET y.type='entity', y.props='{}', y.created_at=$now, y.updated_at=$now
               MERGE (x)-[r:REL {rel:$rel}]->(y)
                 ON CREATE SET r.created_at=$now
               SET r.props=$props
               RETURN x.name AS src, r.rel AS rel, y.name AS dst, r.props AS props,
                      r.created_at AS created_at""",
            a=a, b=b, rel=rel, props=json.dumps(props), now=now,
        ).single()
        return {"ok": True, "relation": _rel_dict(rec)}


def query_graph(subject: str | None = None, rel: str | None = None,
                obj: str | None = None, type: str | None = None,
                contains: str | None = None, limit: int = 50) -> dict[str, Any]:
    relations: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    with _session() as s:
        if subject or rel or obj:
            clauses = []
            if subject:
                clauses.append("x.name=$subject")
            if rel:
                clauses.append("r.rel=$rel")
            if obj:
                clauses.append("y.name=$obj")
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = s.run(
                f"""MATCH (x:Entity)-[r:REL]->(y:Entity){where}
                    RETURN x.name AS src, r.rel AS rel, y.name AS dst, r.props AS props,
                           r.created_at AS created_at
                    ORDER BY r.created_at DESC LIMIT $limit""",
                subject=subject, rel=rel, obj=obj, limit=limit,
            )
            relations = [_rel_dict(r) for r in rows]
        if type or contains:
            clauses = []
            if type:
                clauses.append("e.type=$type")
            if contains:
                clauses.append("e.name CONTAINS $contains")
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = s.run(
                f"MATCH (e:Entity){where} RETURN e ORDER BY e.updated_at DESC LIMIT $limit",
                type=type, contains=contains, limit=limit,
            )
            entities = [_ent_dict(r["e"]) for r in rows]
    return {"ok": True, "entities": entities, "relations": relations}


def recall_about(name: str) -> dict[str, Any]:
    with _session() as s:
        ent = s.run("MATCH (e:Entity {name:$name}) RETURN e", name=name).single()
        out = s.run(
            """MATCH (x:Entity {name:$name})-[r:REL]->(y:Entity)
               RETURN x.name AS src, r.rel AS rel, y.name AS dst, r.props AS props,
                      r.created_at AS created_at, y.type AS neighbor_type
               ORDER BY r.created_at DESC""", name=name).data()
        inc = s.run(
            """MATCH (x:Entity)-[r:REL]->(y:Entity {name:$name})
               RETURN x.name AS src, r.rel AS rel, y.name AS dst, r.props AS props,
                      r.created_at AS created_at, x.type AS neighbor_type
               ORDER BY r.created_at DESC""", name=name).data()

    def shape(rows: list[dict]) -> list[dict[str, Any]]:
        result = []
        for d in rows:
            rd = _rel_dict(d)
            rd["neighbor_type"] = d.get("neighbor_type")
            result.append(rd)
        return result

    return {
        "ok": True,
        "found": ent is not None,
        "entity": _ent_dict(ent["e"]) if ent else {"name": name, "type": None, "props": {}},
        "outgoing": shape(out),
        "incoming": shape(inc),
    }


def stats() -> dict[str, Any]:
    with _session() as s:
        ne = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]
        nr = s.run("MATCH ()-[r:REL]->() RETURN count(r) AS n").single()["n"]
    return {"entities": ne, "relations": nr, "backend": "neo4j",
            "uri": NEO4J_URI, "database": NEO4J_DATABASE}
