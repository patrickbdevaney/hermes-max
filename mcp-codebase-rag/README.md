# mcp-codebase-rag

Hybrid **BM25 + dense** retrieval over *your own* repositories — the capability
that most closes the gap to a frontier model: grounding the agent in your code.

## Tools (dual-mode retrieval)

- `index_repo(path)` — code-aware index of a repo (tree-sitter chunking by
  function/class, heuristic fallback). Re-indexing replaces the repo's entries.
- `search_code(query, k=8)` — hybrid search (RRF over BM25 + dense). Used both
  for **per-task injection** (the `workflow-task-start` skill calls it at job
  start) and **agent-initiated** mid-task retrieval.
- `get_symbol_context(symbol, k=5)` — full chunk(s) defining a named symbol.
- `find_similar(snippet, k=8)` — nearest code to a snippet.

## One store, one embed endpoint

- **Store:** a single SQLite file (`RAG_INDEX_PATH`, default
  `~/.hermes-max/rag/index.db`) holding chunk rows + an FTS5 lexical index +
  (optionally) a `sqlite-vec` vector index. No Qdrant, no external services.
- **Embeddings:** `EMBED_BASE_URL` (OpenAI-compatible `/embeddings`),
  `EMBED_MODEL`. **Optional** — if unset/unreachable the server runs **BM25-only**
  and says so in every result (`"mode": "bm25-only"`). The chat vLLM does *not*
  serve embeddings; point `EMBED_BASE_URL` at a dedicated embed model (e.g. a
  second vLLM serving an embedding model) to enable hybrid mode.
- **The index starts EMPTY.** No seed corpus — it indexes the repos you give it.

## Run / health / test

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MCP_RAG_PORT=9102 .venv/bin/python server.py
./healthcheck.sh
.venv/bin/python smoke_test.py     # deterministic (forces BM25, throwaway DB)
```

## Isolation

Independent process. Only shared state is the SQLite index file. If killed,
Hermes reports the tools unavailable; the agent still works, just un-grounded.

## Scope (intentionally not built)

ONE vector store + ONE embed endpoint + hybrid dense/BM25. **No** 8-stage
HyDE → RAG-Fusion → ColBERT → Self-RAG → HippoRAG pipeline. A reranker is the
only sanctioned future addition, and only if eval shows retrieval precision is
the bottleneck (deferred to Lane 3).


## Enabling semantic (hybrid) RAG later

This server runs **BM25-only** whenever `EMBED_BASE_URL` is empty (the honest
default — the chat vLLM does not serve `/embeddings`). Retrieval still works; it
is lexical rather than semantic. `healthcheck.sh` prints a clear
`RAG: BM25-only (...)` banner in this mode so the degradation is never silent.

To enable hybrid (BM25 + dense) retrieval:

1. Serve an OpenAI-compatible embedding model (e.g. a second vLLM, or a small
   local embed server) reachable over the network.
2. Set in `~/hermes-max/.env` (and `.env.example`):
   ```
   EMBED_BASE_URL=http://<host>:<port>/v1
   EMBED_MODEL=<model-id-or-/model>
   ```
3. Restart `mcp-codebase-rag` and re-index. The healthcheck banner disappears
   and queries become hybrid. No code change is required — the switch is the
   single `EMBED_BASE_URL` variable.
