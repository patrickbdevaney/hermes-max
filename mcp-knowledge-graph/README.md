# mcp-knowledge-graph

A persistent, queryable model of **your** codebase's decisions, bugs, services
and structure that survives across all sessions — the compounding-knowledge
upgrade over a cold-start agent.

## Tools

- `record_entity(type, name, props={})` — upsert an entity (e.g.
  `type="decision"|"bug"|"file"|"service"`). Props merge into existing props.
- `record_relation(a, rel, b, props={})` — directed triple `(a)-[rel]->(b)`,
  e.g. `("bug-42", "fixed_in", "commit-abc")`. Missing endpoints are
  auto-created as stub entities so order never matters.
- `query_graph(subject, rel, obj, type, contains, limit)` — triple-pattern over
  relations (any subset) and/or entity filter by type / name substring.
- `recall_about(name)` — the entity plus all incoming and outgoing relations,
  each annotated with the neighbor's type. The task-start recall call.

## Store

ONE SQLite file (`KG_DB_PATH`, default `~/.hermes-max/kg/graph.db`), two tables
(`entities`, `relations`). No Neo4j, no Graphiti, no Cognee.

## Run / health / test

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MCP_KG_PORT=9103 .venv/bin/python server.py
./healthcheck.sh
.venv/bin/python smoke_test.py
```

## Isolation

Independent process; only shared state is the SQLite graph file. If killed,
Hermes reports the tools unavailable; the agent simply can't recall/record
structured knowledge that session.
