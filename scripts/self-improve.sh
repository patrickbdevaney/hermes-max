#!/usr/bin/env bash
# self-improve.sh — run the human-gated self-improvement jobs (Phase 4.2 + 4.3),
# OFF the hot path. Intended for a weekly cron; writes proposals to the review queue
# (~/.hermes-max/review-queue/) for manual review — never auto-applies.
#   Weekly cron example (crontab -e):
#     0 4 * * 0  /home/patrickd/hermes-max/scripts/self-improve.sh >> ~/.hermes-max/logs/self-improve.log 2>&1
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib.sh"; hmx_load_env 2>/dev/null || true
# use a venv that has `mcp` so reflection can route to the steer tier (else local vLLM)
PY="${SCRIPT_DIR}/../mcp-research/.venv/bin/python"; [ -x "$PY" ] || PY=python3
echo "── self-improve $(date) ──"
"$PY" "${SCRIPT_DIR}/self_improve.py" optimize || true
"$PY" "${SCRIPT_DIR}/self_improve.py" distill  || true
echo "review queue: ${HOME}/.hermes-max/review-queue/ (human-gated — review and apply manually)"
