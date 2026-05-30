#!/usr/bin/env bash
# Thin wrapper: bring hermes-max up in the DEFAULT, maximalist gpu_local profile
# (CUDA box; local OR cloud chat; optional local embed/rerank; full hybrid+graph+
# rerank RAG). Zero flags — pick the profile by filename. No code duplication:
# this is one line over the single engine, bootstrap.sh.
#
#   bash bootstrap-gpu.sh            # set everything up (idempotent)
#   bash bootstrap-gpu.sh --check    # dry-run audit
#
# (Identical to `bash bootstrap.sh` since gpu_local is the default; provided as a
# symmetric counterpart to bootstrap-lean.sh so intent is explicit.)
exec env DEPLOY_PROFILE=gpu_local bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bootstrap.sh" "$@"
