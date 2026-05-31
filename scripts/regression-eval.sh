#!/usr/bin/env bash
# regression-eval.sh — regression-eval-as-code (Phase 6.3). Deterministic capability
# probes gated against a baseline. `--update` writes the baseline; default compares
# and exits 1 on regression. Wire into CI / pre-merge.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib.sh"; hmx_load_env 2>/dev/null || true
PY="${SCRIPT_DIR}/../mcp-research/.venv/bin/python"; [ -x "$PY" ] || PY=python3
exec "$PY" "${SCRIPT_DIR}/regression_eval.py" "$@"
