#!/usr/bin/env bash
# Register the weekly dspy-evolution job with Hermes's native cron.
#
# Installs run-evolution.sh into ~/.hermes/scripts/ (where `hermes cron --script`
# expects it) and creates a weekly --no-agent job whose stdout is delivered to
# the operator. Idempotent: re-running updates the script and skips re-creating
# the job if it already exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_SCRIPTS="${HERMES_HOME}/scripts"
JOB_NAME="dspy-weekly-evolution"
SCHEDULE="${DSPY_SCHEDULE:-0 4 * * 0}"   # Sundays 04:00 local
DELIVER="${DSPY_DELIVER:-local}"          # local | telegram | discord | ...
HERMES_BIN="${HERMES_BIN:-hermes}"

mkdir -p "${HERMES_SCRIPTS}"
cp "${SCRIPT_DIR}/run-evolution.sh" "${HERMES_SCRIPTS}/run-evolution.sh"
chmod +x "${HERMES_SCRIPTS}/run-evolution.sh"
echo "installed: ${HERMES_SCRIPTS}/run-evolution.sh"

if ! command -v "${HERMES_BIN}" >/dev/null 2>&1; then
  echo "WARNING: '${HERMES_BIN}' not on PATH — script installed but cron not registered."
  echo "Register manually once hermes is available:"
  echo "  ${HERMES_BIN} cron create '${SCHEDULE}' --no-agent --name ${JOB_NAME} --script run-evolution.sh --deliver ${DELIVER}"
  exit 0
fi

if "${HERMES_BIN}" cron list 2>/dev/null | grep -q "${JOB_NAME}"; then
  echo "cron job '${JOB_NAME}' already exists — leaving it in place."
  exit 0
fi

"${HERMES_BIN}" cron create "${SCHEDULE}" \
  --no-agent \
  --name "${JOB_NAME}" \
  --script run-evolution.sh \
  --deliver "${DELIVER}"
echo "registered weekly cron '${JOB_NAME}' (${SCHEDULE}, deliver=${DELIVER})"
