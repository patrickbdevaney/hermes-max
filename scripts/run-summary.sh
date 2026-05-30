#!/usr/bin/env bash
# run-summary.sh — print the per-task tool-call summary table (Stage 3).
#
# A table of every tool called this task: count, total time, failures, fallbacks,
# est-vs-actual duration, plus the routing/fallback/kill decisions. So after a run
# the operator sees exactly where time went and what fell back. Reads the live
# tool-call log (live.jsonl); pass an explicit path to summarise a saved log.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env 2>/dev/null || true

exec python3 "${SCRIPT_DIR}/run_summary.py" "$@"
