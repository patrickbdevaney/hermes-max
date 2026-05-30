#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stage-3 rate-limit PRODUCTION validation (REAL free-tier calls, $0).
#
# Proves the conductor's live per-provider RPM/RPD/TPM budget tracker is
# production-viable for `free` mode: it PRE-FLIGHT-skips an over-budget rung
# before firing (never fire-and-absorb a 429/413). Drives the best-of-N draft
# fan-out a few rounds in a tight window so Groq's tiny per-model TPM exhausts and
# the tracker skips it while Cerebras keeps producing — the run completes degraded.
#
# Requires free keys (CEREBRAS/GROQ) in .env. If absent, SKIPS cleanly (exit 0).
# Writes rate_limit_validation_trace.md. Uses an isolated budget file (no live
# state touched). Honors RL_ROUNDS / RL_MAX_TOKENS overrides.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

ESC="${REPO_ROOT}/mcp-escalation"
PY="${ESC}/.venv/bin/python"; [ -x "${PY}" ] || PY="python3"

if [ -z "${CEREBRAS_API_KEY:-}" ] && [ -z "${GROQ_API_KEY:-}" ]; then
  echo "• no free-tier keys (CEREBRAS/GROQ) set — nothing to rate-limit. SKIPPING (informational)."
  exit 0
fi

echo "═══ Stage-3 rate-limit validation (real free-tier, \$0) ═══"
cd "${ESC}" && exec "${PY}" validate_rate_limits.py
