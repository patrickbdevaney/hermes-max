#!/usr/bin/env bash
# Thin wrapper: bring hermes-max up in the lean_cloud profile (CPU / Mac-mini /
# VPS; NO torch/CUDA anywhere; cloud chat via $VLLM_BASE_URL; RAG = BM25 + AST-
# graph; doc extract via Crawl4AI-if-present else trafilatura). A graceful SUBSET
# of gpu_local — it never caps any gpu_local capability. Zero flags — pick the
# profile by filename. No code duplication: one line over the engine bootstrap.sh.
#
#   bash bootstrap-lean.sh           # set everything up (idempotent)
#   bash bootstrap-lean.sh --check   # dry-run audit
#
# Reminder: set VLLM_BASE_URL in .env to your CLOUD chat endpoint (lean assumes
# no local GPU to serve the chat model).
exec env DEPLOY_PROFILE=lean_cloud bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bootstrap.sh" "$@"
