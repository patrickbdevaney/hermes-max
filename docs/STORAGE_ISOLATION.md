# Storage isolation convention

Every harness that includes a RAG/KG layer runs its **own dedicated stateful storage**. No two
harnesses share a Qdrant instance, Neo4j database, Redis, or PostgreSQL. A harness can be reset or
destroyed (`docker compose -f infra/docker-compose.yml down -v`) without touching any other.

This convention is permanent and applies to every harness built from here forward. It applies
**regardless of the harness's name** — "optime" is just the name of the Claude Code MCP uplift
layer; each harness-specific MCP suite has its own name and simply takes the next free port block.

---

## Stateful (isolate) vs stateless (share)

Not everything is duplicated. The line is **data ownership**:

- **Isolate — one instance per harness, never shared.** Services that accumulate harness-specific
  data: **Qdrant, Neo4j, Redis, PostgreSQL**. Each harness gets its own on its own port block.
- **Share — one singleton, every harness points at it.** Stateless pass-through services that
  produce no harness-specific data: **SearXNG (8080), the embedding server, the reranker, the
  browser-automation tier (Lightpanda/Obscura), the local model server (Ollama/vLLM)**.

Rule of thumb: *stateful datastores are isolated per harness; stateless services are shared.*

---

## Port allocation (stateful stores only)

| Harness                | Postgres | Redis | Neo4j Bolt | Neo4j HTTP | Qdrant REST | Qdrant gRPC |
|------------------------|----------|-------|-----------|------------|-------------|-------------|
| hermes-max             | 5432     | 6379  | 7687       | 7474       | 6333        | 6334        |
| optime                 | 5533     | 6479  | 7787       | 7574       | 6433        | 6434        |
| _(next harness)_       | 5634     | 6579  | 7887       | 7674       | 6533        | 6534        |
| _(harness after that)_ | 5735     | 6679  | 7987       | 7774       | 6633        | 6634        |

Pattern: each new harness increments the blocks by **+101 / +100 / +100 / +100 / +100 / +100**.
A harness reserves its block whether or not it currently uses every service — hermes-max's slot is
the bare-default ports even though it defaults to embedded SQLite and only binds 7687 when the
optional Neo4j backend is enabled.

Shared stateless services keep their single well-known port (SearXNG 8080, etc.) and never appear
in this table.

---

## Rules

1. Each harness has `infra/docker-compose.yml` with its own **named volumes**
   (`<harness>_postgres_data`, …) and its own **network** (`<harness>_net`).
2. Storage URLs are env vars **prefixed with the harness name** (`OPTIME_NEO4J_URL`,
   `OPTIME_QDRANT_URL`, …) — never bare defaults. A bare `localhost:7687` in a non-hermes harness's
   source is a bug: that port is hermes-max's.
3. `docker compose -f infra/docker-compose.yml down -v` destroys only that harness's volumes. No
   other harness is affected.
4. **Dev-tier** harnesses may be reset or deleted at any time. A **prod-tier** harness is
   sacrosanct — its stack is never `down -v`'d without an explicit backup first.
5. This convention applies retroactively: hermes-max's bare-default ports are its slot in the
   table; new harnesses never reuse them.
6. Stateless services (above) are **shared singletons** — do not duplicate them per harness, and do
   not give them a per-harness port block.

---

## Current status

- **hermes-max** — defaults to **embedded SQLite** (`KG_DB_PATH`); Neo4j is an optional power-user
  backend (`KG_BACKEND=neo4j`, `NEO4J_URI=bolt://localhost:7687`). No always-on compose stack. Its
  port slot is reserved above regardless.
- **optime** — currently **embedded SQLite** under `~/.optime/` (file-level isolation is already
  absolute). Its dedicated stack (`optime/infra/docker-compose.yml`, ports 5533/6479/7787/6433) is
  provisioned ahead for any future service-backed component (e.g. a Qdrant-backed RAG or the
  deferred optime-research). Bring it up with one command when such a component lands.
