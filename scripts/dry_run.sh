#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stage-5 RAPID real-inference DRY RUN — one fast command that exercises EVERY
# component once against REAL inference and dumps a readable dry_run_trace.md.
#
# A smoke proof the whole system boots & coheres end-to-end (NOT a benchmark).
# Mode-aware: local | free | full (default: $CONDUCTOR_MODE in .env, else full).
#   local  — base case, zero cloud; every cloud step cleanly skip-logged.
#   free   — local + free cloud tiers (Cerebras/Groq).
#   full   — adds paid synth/steer.
#
# Usage:  bash scripts/dry_run.sh [--mode local|free|full]
# Requires only the local model ($VLLM_BASE_URL). Everything else degrades.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

MODE="${CONDUCTOR_MODE:-full}"
if [ "${1:-}" = "--mode" ] && [ -n "${2:-}" ]; then MODE="$2"; fi

# --reliability : the Stage-4 reliability + observability sequence (empty+real
# index, RAG, KG, watchdog look-ahead/heartbeat/kill, checkpoint revert) with the
# live log streaming + the per-task summary + dry_run_trace.md. Model-INDEPENDENT
# (it exercises the parts the reliability pass changed), so it runs with NO cloud
# keys and NO local model — open scripts/watch.sh in a side terminal to see it live.
if [ "${1:-}" = "--reliability" ] || [ "${MODE}" = "reliability" ]; then
  PY="${REPO_ROOT}/mcp-watchdog/.venv/bin/python"; [ -x "${PY}" ] || PY="python3"
  exec "${PY}" "${SCRIPT_DIR}/dry_run_reliability.py"
fi

export HMX_REPO_ROOT="${REPO_ROOT}"
export HMX_DRYRUN_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo run)"

# preflight: the one hard dependency
if [ -z "${VLLM_BASE_URL:-}" ] || ! curl -fsS -m6 "${VLLM_BASE_URL%/}/models" >/dev/null 2>&1; then
  echo "✗ local model ($VLLM_BASE_URL) unreachable — the one required endpoint. Aborting."
  exit 1
fi

PY="${REPO_ROOT}/mcp-escalation/.venv/bin/python"; [ -x "${PY}" ] || PY="python3"
exec "${PY}" "${SCRIPT_DIR}/dry_run.py" --mode "${MODE}"
