#!/usr/bin/env bash
# dream-cycle.sh — nightly corpus enrichment (Phase 5.2), off the hot path.
#   Nightly cron example (crontab -e):
#     0 3 * * *  /home/patrickd/hermes-max/scripts/dream-cycle.sh --apply >> ~/.hermes-max/logs/dream-cycle.log 2>&1
# Default (no --apply) is dry-run for the dedup moves; pass --apply to quarantine dups.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib.sh"; hmx_load_env 2>/dev/null || true
PY="${SCRIPT_DIR}/../mcp-research/.venv/bin/python"; [ -x "$PY" ] || PY=python3
exec "$PY" "${SCRIPT_DIR}/dream_cycle.py" "$@"
