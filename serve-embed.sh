#!/usr/bin/env bash
# Serve Qwen3-Embedding-0.6B (MTEB-Code top tier; ~1.2GB; Matryoshka 32–1024 dims)
# as an OpenAI-compatible /embeddings endpoint for mcp-codebase-rag's dense lane.
#
#   bash serve-embed.sh           # auto: vLLM if present (your inference host), else local (laptop)
#   SERVE_BACKEND=vllm  bash serve-embed.sh
#   SERVE_DEVICE=cuda   bash serve-embed.sh   # local backend on the GPU
#
# Then set in .env:   EMBED_BASE_URL=http://localhost:8002/v1
# Tiny enough to run alongside the chat model on your inference host's 128GB (bound
# concurrent calls — they share the memory bus). Kill it → RAG falls back to
# BM25+graph with a clear stats() banner.
set -uo pipefail
ROLE=embed
PORT="${EMBED_PORT:-8002}"
MODEL="${EMBED_SERVE_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
VLLM_TASK=embed
# shellcheck source=serving/_serve_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/serving/_serve_common.sh"
serve_role
