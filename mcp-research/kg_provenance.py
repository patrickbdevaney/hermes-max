"""Stage 5a — KG ingestion with provenance + temporal validity.

Research outputs become graph episodes/entities/edges, each FACT EDGE carrying its
source ID so any claim is traceable to the chunk it came from. Edge vocabulary maps
the research domain: cites / supersedes / implements / audits / contradicts /
authored_by (the citation graph from Stage 4 lands directly as `cites` edges).

Temporal validity matters for fast-moving fields: a 2024 claim may be superseded by
a 2026 one. Rather than silently keeping both, mark_superseded records the
`supersedes` edge AND stamps the old fact's `valid_until` — so the graph says which
is current.

NOTE (honest): the spec says Graphiti/Neo4j; the actual KG (mcp-knowledge-graph) is
a single-file SQLite store whose own header reads "Deliberately NOT built: Neo4j +
Graphiti + Cognee". Same contract — entities, directed relations, and a `props` bag
that carries source IDs + valid_from/valid_until — modeled on that store, USED not
modified. Never raises; degrades to a reported no-op if the KG is down.
"""
from __future__ import annotations

import datetime
from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import research_core as rc

KG_MCP_URL = rc.KG_MCP_URL
ALLOWED_RELS = {"cites", "supersedes", "implements", "audits", "contradicts", "authored_by"}


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ── injectable KG call (smoke tests stub this) ────────────────────────────────
def _kg_call(tool: str, args: dict) -> dict[str, Any]:
    return rc._mcp_call(KG_MCP_URL, tool, args)


def add_entity(entity_type: str, name: str, props: dict | None = None,
               source_id: str | None = None) -> dict[str, Any]:
    """Upsert a research entity (paper/repo/protocol/eip/person/technique). The
    backing source_id is stored in props for provenance."""
    props = dict(props or {})
    if source_id:
        props.setdefault("source_id", source_id)
    return _kg_call("record_entity", {"type": entity_type, "name": name, "props": props})


def add_fact_edge(a: str, rel: str, b: str, source_id: str,
                  valid_from: str | None = None, valid_until: str | None = None,
                  props: dict | None = None) -> dict[str, Any]:
    """Record a fact edge (a)-[rel]->(b) carrying its SOURCE ID + temporal validity.
    rel must be in ALLOWED_RELS (a wrong/invented relation is rejected, not stored)."""
    if rel not in ALLOWED_RELS:
        return {"ok": False, "error": f"relation '{rel}' not in {sorted(ALLOWED_RELS)}"}
    p = dict(props or {})
    p.update(source_id=source_id, valid_from=valid_from or _now_iso(), valid_until=valid_until)
    r = _kg_call("record_relation", {"a": a, "rel": rel, "b": b, "props": p})
    if r.get("ok"):
        otel_emit.record("kg_episode_added", {"rel": rel, "source_id": source_id,
                                              "a": a, "b": b})
    return r


def ingest_citation_edges(edges: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    """Bulk-record Stage-4 citation_edges() output as `cites` fact edges, each
    carrying source_id. Entities are auto-created by the KG; we also tag titles."""
    written = 0
    errors: list[str] = []
    for e in (edges or []):
        a, b = e.get("src"), e.get("dst")
        if not a or not b:
            continue
        # tag endpoint titles/urls when present (provenance)
        if e.get("src_title"):
            add_entity("paper", a, {"title": e["src_title"], "url": e.get("src_url", "")})
        if e.get("dst_title"):
            add_entity("paper", b, {"title": e["dst_title"], "url": e.get("dst_url", "")})
        r = add_fact_edge(a, e.get("rel", "cites"), b, source_id=source_id)
        if r.get("ok"):
            written += 1
        elif r.get("error"):
            errors.append(r["error"])
    return {"ok": True, "edges_written": written, "errors": errors}


def mark_superseded(old: str, new: str, source_id: str,
                    as_of: str | None = None) -> dict[str, Any]:
    """Mark `old` superseded by `new` (fast-moving fields): record new-[supersedes]->
    old, AND stamp the old entity's valid_until=as_of so the graph says which is
    current — rather than silently keeping both."""
    as_of = as_of or _now_iso()
    edge = add_fact_edge(new, "supersedes", old, source_id=source_id, valid_from=as_of)
    add_entity("entity", old, {"valid_until": as_of, "superseded_by": new})
    otel_emit.record("kg_superseded", {"old": old, "new": new, "as_of": as_of})
    return {"ok": edge.get("ok", False), "old": old, "new": new, "as_of": as_of}


def add_episode(namespace: str, summary: str, source_id: str,
                entities: list[dict] | None = None,
                edges: list[dict] | None = None) -> dict[str, Any]:
    """Record a research EPISODE (modeled as an entity of type 'episode') + its
    entities + fact edges, all carrying source_id + ingested_at. The single call
    that lands a finished research finding into the graph with full provenance."""
    ts = _now_iso()
    ep_name = f"episode:{namespace}:{source_id}"
    ep = add_entity("episode", ep_name,
                    {"namespace": namespace, "summary": summary[:1000], "ingested_at": ts},
                    source_id=source_id)
    ent_written = 0
    for e in (entities or []):
        if e.get("name"):
            add_entity(e.get("type", "entity"), e["name"], e.get("props", {}), source_id)
            ent_written += 1
    edge_written = 0
    for ed in (edges or []):
        if ed.get("a") and ed.get("b") and ed.get("rel"):
            if add_fact_edge(ed["a"], ed["rel"], ed["b"], source_id=source_id,
                             valid_from=ed.get("valid_from"),
                             valid_until=ed.get("valid_until")).get("ok"):
                edge_written += 1
    otel_emit.record("kg_episode_added", {"namespace": namespace, "source_id": source_id,
                                          "entities": ent_written, "edges": edge_written})
    return {"ok": ep.get("ok", False), "episode": ep_name, "entities": ent_written,
            "edges": edge_written, "ingested_at": ts}


def kg_provenance_stats() -> dict[str, Any]:
    return {"kg_mcp": KG_MCP_URL, "allowed_relations": sorted(ALLOWED_RELS)}
