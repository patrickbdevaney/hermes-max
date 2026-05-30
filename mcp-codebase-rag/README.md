# mcp-codebase-rag

Hybrid **BM25 + dense + graph**, then optional **cross-encoder rerank**,
retrieval over *your own* repositories — the capability that most closes the gap
to a frontier model: grounding the agent in your code.

Every lane is independently optional and degrades to the next-best mode; the
`mode` field on every result (and `retrieval_mode` in `/health`) reports exactly
which lanes were active, so degradation is never silent.

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

## Reranker (Stage 1.2)

Set `RERANK_BASE_URL` (OpenAI/Cohere/Jina-shaped `/rerank`) to add a
cross-encoder pass: the fused top-pool (`RERANK_POOL`, default 24) is re-ordered
by the reranker and the top-k is returned (`mode` gains `+rerank`). Blank ⇒ the
fused order is returned unchanged. Independent of embeddings — it sharpens even a
BM25+graph result set. Endpoint down/misshaped ⇒ fused order kept, no hard fail.

```bash
./serve-rerank.sh        # Qwen3-Reranker-0.6B on :8003 (vLLM on your inference host, local CPU shim on a laptop)
# then in .env:  RERANK_BASE_URL=http://localhost:8003/v1
.venv/bin/python validate_stage1.py   # live MRR across bm25+graph / hybrid / hybrid+rerank
```

## Scope (intentionally not built)

ONE vector store + ONE embed endpoint + hybrid dense/BM25 + ONE reranker. **No**
8-stage HyDE → RAG-Fusion → ColBERT → Self-RAG → HippoRAG pipeline. The reranker
(Stage 1.2) is the last sanctioned precision lever; add more only if eval shows
retrieval precision is still the bottleneck.


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
