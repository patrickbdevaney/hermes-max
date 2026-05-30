# mcp-docs

The **sovereign documentation pipeline**: search → extract → distil → store,
entirely self-hosted. Lets the agent **learn a novel framework on demand** with
**no external API** (no Firecrawl/Tavily/Exa key), then retrieve the real
signatures via `search_code`.

```
SearXNG (search)  →  Crawl4AI (extract → clean markdown)  →  local chat model
(distil to a high-signal note)  →  mcp-codebase-rag (docs/<topic> namespace)
                                 + mcp-knowledge-graph (framework → api edges)
```

## Tools

- `search_docs(query, category?, limit=8)` — self-hosted SearXNG JSON → candidate
  URLs (title/url/snippet). Optional SearXNG `category`.
- `fetch_clean(url)` — Crawl4AI → clean, RAG-optimised markdown. **trafilatura**
  local fallback if Crawl4AI is down. The sovereign replacement for a Firecrawl
  extract call.
- `ingest_doc(url_or_markdown, topic)` — fetch (if URL) → distil with the local
  model → store the note in RAG under `docs/<topic>` (co-retrievable with code)
  AND record `framework→api` edges in the KG. Idempotent per (topic, url).
- `research_topic(topic, n=3, category?)` — the "learn a framework" entry point:
  search → ingest top N → distilled brief. Use it BEFORE coding against an
  unfamiliar framework (see the `workflow-learn-framework` skill).

## Backends (all local; each degrades gracefully)

| Env var | Default | Down ⇒ |
|---|---|---|
| `SEARXNG_URL` | `http://localhost:8080` | search_docs reports unavailable |
| `CRAWL4AI_URL` | `http://localhost:11235` | fetch_clean falls back to trafilatura |
| `VLLM_BASE_URL` | (chat model) | distil stores **raw** markdown (no crash) |
| `RAG_MCP_URL` | `http://127.0.0.1:9102/mcp` | note not stored (reported) |
| `KG_MCP_URL` | `http://127.0.0.1:9103/mcp` | entities not stored (reported) |

Start the containers: `./searXNG.sh` (enables the JSON API) and `./crawl4ai.sh`
(arch-aware: `:basic`/arm64 on your inference host, `:latest`/amd64 on x86_64; binds
`localhost:11235`).

## Native wiring (discovery-first)

Hermes exposes `web.backend: searxng`, `web.search_backend`, and an **empty**
`web.extract_backend`. That extract hook expects a **Firecrawl-protocol** URL;
Crawl4AI's REST API (`POST /md`) is not Firecrawl-compatible, so the native
`web_extract` tool is **not** auto-pointed at it. mcp-docs instead provides the
sovereign extract via its own `fetch_clean`/`ingest_doc` tools. (If a
Firecrawl-compatible shim is added later, set `web.extract_backend` to it.)

## Run / health / test

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MCP_DOCS_PORT=9109 .venv/bin/python server.py
.venv/bin/python smoke_test.py     # A/B deterministic, C live-if-up, D server boot
```

## Isolation

Independent process. If killed, Hermes reports the tools unavailable and the
agent degrades (it just can't learn new frameworks that session). It never
hard-fails the agent — every backend has a local default or graceful fallback.
