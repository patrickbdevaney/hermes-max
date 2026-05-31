#!/usr/bin/env bash
# observe.sh — live observability terminal (R-Stage 5). A per-turn waterfall +
# backend throughput + time-breakdown showing exactly where the wall time goes
# (research vs implementation vs idle), fed by the existing live.jsonl and vLLM
# /metrics. Read-only; Ctrl-C (or q) to quit.
#
# Complements watch.sh (the raw colourised stream): observe.sh AGGREGATES — it
# answers "where did the last 25 minutes go", watch.sh shows the event-by-event log.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env 2>/dev/null || true

LOG_DIR="${HERMES_MAX_LOG_DIR:-${HMX_LOG_DIR:-${HOME}/.hermes-max/logs}}"
export HERMES_MAX_LIVE_JSONL="${LOG_DIR/#\~/$HOME}/live.jsonl"
export VLLM_BASE_URL="${VLLM_BASE_URL:-}"
mkdir -p "${LOG_DIR/#\~/$HOME}"
[ -f "${HERMES_MAX_LIVE_JSONL}" ] || : >"${HERMES_MAX_LIVE_JSONL}"

exec python3 "${SCRIPT_DIR}/observe.py"
