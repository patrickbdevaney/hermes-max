#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stage-6 COMBINATORIAL emergent-behavior eval — hunts the interaction failures
# isolated tests miss. Produces EVIDENCE on the three suspicion risks (Banyan
# focus-thrash, research-noise contamination, ladder cascade) with each config
# remedy toggled A/B, plus the empty-base-case (zero data) and coherence checks.
# Writes emergent_eval_report.md.
#
# Usage:  bash scripts/emergent_eval.sh [--mode local|free|full]
# Local model ($VLLM_BASE_URL) recommended for the RISK-B directive generation;
# absent -> that one sub-check degrades, the rest still run.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

MODE="${CONDUCTOR_MODE:-full}"
if [ "${1:-}" = "--mode" ] && [ -n "${2:-}" ]; then MODE="$2"; fi
export HMX_REPO_ROOT="${REPO_ROOT}"
export HMX_EVAL_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo run)"

PY="${REPO_ROOT}/mcp-escalation/.venv/bin/python"; [ -x "${PY}" ] || PY="python3"
exec "${PY}" "${SCRIPT_DIR}/emergent_eval.py" --mode "${MODE}"
