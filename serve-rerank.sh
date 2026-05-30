#!/usr/bin/env bash
# Serve Qwen3-Reranker-0.6B (cross-encoder; ~1.2GB) as an OpenAI/Cohere-shaped
# /rerank endpoint — the single highest-precision-per-token RAG lever.
#
#   bash serve-rerank.sh          # auto: vLLM if present (your inference host), else local (laptop)
#   SERVE_BACKEND=vllm  bash serve-rerank.sh
#   SERVE_DEVICE=cuda   bash serve-rerank.sh   # local backend on the GPU
#
# Then set in .env:   RERANK_BASE_URL=http://localhost:8003/v1
# Tiny; runs alongside the chat + embedding models on your inference host. Kill it → RAG
# returns the fused order unchanged (graceful; rerank only ever sharpens).
set -uo pipefail
ROLE=rerank
PORT="${RERANK_PORT:-8003}"
MODEL="${RERANK_SERVE_MODEL:-Qwen/Qwen3-Reranker-0.6B}"
VLLM_TASK=score
# shellcheck source=serving/_serve_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/serving/_serve_common.sh"
serve_role
