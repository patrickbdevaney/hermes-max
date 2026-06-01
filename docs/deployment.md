# Deployment profiles — one codebase, every environment

hermes-max does **not** assume a beefy machine. The stack degrades gracefully from
a DGX down to a CPU-only VPS, and `hm up` never hard-fails because a GPU service is
missing — it falls back and tells you what it did.

Deployment is selected by `DEPLOY_PROFILE` (in `.env`). There are two profiles,
each a one-line wrapper over the single `bootstrap.sh` engine — **no docker
required**, no code duplication:

```bash
bash bootstrap-gpu.sh     # DEFAULT, maximalist  → DEPLOY_PROFILE=gpu_local
bash bootstrap-lean.sh    # CPU / Mac-mini / VPS → DEPLOY_PROFILE=lean_cloud
bash bootstrap.sh --check # dry-run audit (what's missing), changes nothing
```

`bootstrap.sh` auto-detects and **suggests** a profile (CUDA + RAM + arch +
endpoint), never silently overriding an explicit choice. `install.sh` wraps this
for first-run setup.

## The environment matrix

Find your row and you know exactly what to do.

| Environment | Runs locally | Runs in cloud | Bootstrap profile | hm mode |
|---|---|---|---|---|
| **Mini PC / Mac mini / VPS** (cloud-everything) | MCP servers only (all pure-Python) | chat model, planning; embeddings optional | `lean_cloud` | `--full` (Profile B) |
| **Laptop** (cloud driver + local embed) | MCP servers + optional local embedding | chat model, planning | `lean_cloud` (or `gpu_local` if CUDA) | `--full` |
| **Desktop / single GPU** (RTX 3090–5090) | chat model + embed/rerank + all servers | optional planner uplift | `gpu_local` | `--free` / `--full-local` |
| **DGX / Thor / Spark** (big unified mem) | everything, large MoE driver | optional Opus for hard sessions | `gpu_local` | `--free` / `--frontier-local` |

## Graceful degradation (the lean guarantee)

**No MCP server's `requirements.txt` pulls torch/CUDA** — every server reaches
models over HTTP. The only torch/CUDA touchpoints are the optional,
`gpu_local`-only `serve-embed.sh` / `serve-rerank.sh`. `bootstrap.sh` asserts this
(greps the requirements), so a lean box never needs a GPU stack.

When a GPU-backed service is absent, the component continues with a warning rather
than failing:

| Capability | `gpu_local` (default) | `lean_cloud` (CPU/Mac/VPS) |
|---|---|---|
| Chat model | local vLLM **or** cloud via `$VLLM_BASE_URL` | cloud via `$VLLM_BASE_URL` |
| RAG embeddings | local Qwen3-Embed (CUDA) | optional cloud `EMBED_BASE_URL`, else **BM25 + AST-graph** (automatic) |
| Reranker | local Qwen3-Reranker (CUDA) | cloud if set, else fused order (no rerank) |
| RAG graph (tree-sitter + PageRank) | full | **full** (pure-Python, CPU-fine) |
| Doc extract | Crawl4AI (Docker) | Crawl4AI if Docker present, else **trafilatura** |
| Deep research | full | **full** (uses the cloud chat endpoint) |
| verify / checkpoint / watchdog / KG / repomap / lsp / codegraph | full | **full** (all pure-Python over HTTP) |

The manifest gates which servers run per profile, so a future `gpu_local`-only
capability is one `profiles:` line and lean is unaffected — **lean is a graceful
subset, never a ceiling on full.**

## What `hm up` does on a no-GPU box

`hm up --free` on a machine with no reachable local endpoint detects the absence,
**warns clearly**, and degrades — it points you at Profile B (`hm up --full`) rather
than silently pretending a local driver exists. RAG automatically uses BM25 + graph
retrieval when no embedding service is up. You always see, in the one-screen start
summary, which providers are present, which roles are satisfiable, and what fell
back.

## Optional supporting containers

These enrich the loop but are not required; each degrades cleanly if absent:

```bash
./phoenix.sh     # OpenTelemetry collector + UI (OTLP :4317, UI :6006)
./searXNG.sh     # self-hosted search for the docs/research loop (:8080)
./crawl4ai.sh    # high-fidelity HTML→markdown extraction (:11235)
./serve-embed.sh # local RAG embeddings  (gpu_local only, :8002)
./serve-rerank.sh# local cross-encoder rerank (gpu_local only, :8003)
```

See [troubleshooting.md](troubleshooting.md) for what each absence looks like at
runtime.
