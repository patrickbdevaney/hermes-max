#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# dspy-evolution — weekly wrapper around the OFFICIAL hermes-agent-self-evolution
# module (DSPy + GEPA). Evolves the most-used skills/prompts against accumulated
# session history. Designed to run under Hermes native cron with --no-agent, so
# its stdout becomes the weekly digest delivered to the operator.
#
# It NEVER hard-fails the cron: if the self-evolution package isn't installed
# (it ships as a separate repo, not bundled with Hermes v0.15.1), this prints
# install instructions and exits 0.
#
# Config (all optional, with sane defaults):
#   DSPY_PYTHON        interpreter that has hermes-agent-self-evolution installed
#                      (default: python3)
#   DSPY_EVOLVE_CMD    full command to run the evolution (overrides autodetect)
#   DSPY_EVOLVE_ARGS   extra args appended to the autodetected command
#   DSPY_TIMEOUT       max seconds for the evolution run (default: 3600)
#   HERMES_HOME        Hermes home (default: ~/.hermes) — source of session data
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load the single-port-story env if present (gives VLLM_BASE_URL etc.).
if [ -f "${REPO_ROOT}/.env" ]; then
  set -a; . "${REPO_ROOT}/.env"; set +a
fi

DSPY_PYTHON="${DSPY_PYTHON:-python3}"
DSPY_TIMEOUT="${DSPY_TIMEOUT:-3600}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SESSIONS_DIR="${HERMES_HOME}/sessions"
SKILLS_DIR="${HERMES_HOME}/skills"

LOGDIR="${HOME}/.hermes-max/dspy-evolution"
mkdir -p "${LOGDIR}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${LOGDIR}/run_${STAMP}.log"

echo "=== dspy-evolution weekly run @ ${STAMP} ==="
echo "log: ${LOG}"
{
  echo "VLLM_BASE_URL=${VLLM_BASE_URL:-<unset>}"
  echo "sessions: ${SESSIONS_DIR}"
  echo "skills:   ${SKILLS_DIR}"
} >>"${LOG}" 2>&1

# Pick a timeout helper if available (GNU coreutils `timeout`).
TIMEOUT_BIN="$(command -v timeout || true)"
run_with_timeout() {
  if [ -n "${TIMEOUT_BIN}" ]; then
    "${TIMEOUT_BIN}" "${DSPY_TIMEOUT}" "$@"
  else
    "$@"
  fi
}

# Detect how to invoke the self-evolution module.
detect_cmd() {
  if [ -n "${DSPY_EVOLVE_CMD:-}" ]; then
    echo "${DSPY_EVOLVE_CMD}"
    return 0
  fi
  # 1) CLI on PATH?
  if command -v hermes-agent-self-evolution >/dev/null 2>&1; then
    echo "hermes-agent-self-evolution"
    return 0
  fi
  # 2) importable module?
  if "${DSPY_PYTHON}" -c "import hermes_agent_self_evolution" >/dev/null 2>&1; then
    echo "${DSPY_PYTHON} -m hermes_agent_self_evolution"
    return 0
  fi
  # 3) optional first-run install (opt-in; respects the no-lazy-install default)
  if [ "${DSPY_AUTO_INSTALL:-false}" = "true" ]; then
    if "${DSPY_PYTHON}" -m pip install -q hermes-agent-self-evolution >/dev/null 2>&1 \
       && "${DSPY_PYTHON}" -c "import hermes_agent_self_evolution" >/dev/null 2>&1; then
      echo "${DSPY_PYTHON} -m hermes_agent_self_evolution"
      return 0
    fi
  fi
  return 1
}

if ! CMD="$(detect_cmd)"; then
  cat <<EOF
dspy-evolution: SKIPPED — hermes-agent-self-evolution is not installed.

This is the official DSPy+GEPA self-evolution module and ships as a SEPARATE
repo (not bundled with Hermes v0.15.1). To enable weekly evolution:

  git clone <hermes-agent-self-evolution repo>
  ${DSPY_PYTHON} -m pip install -e <that repo>

Then this weekly cron will optimize your most-used skills/prompts against
accumulated session history automatically. No action needed now; exiting 0 so
the schedule stays healthy.
EOF
  echo "skipped: package not installed" >>"${LOG}" 2>&1
  exit 0
fi

# Default args point the optimizer at the real session history + skills. The
# package's exact flags may differ across versions; override with
# DSPY_EVOLVE_ARGS once installed if needed.
DEFAULT_ARGS="--sessions ${SESSIONS_DIR} --skills ${SKILLS_DIR}"
ARGS="${DSPY_EVOLVE_ARGS:-${DEFAULT_ARGS}}"

echo "dspy-evolution: running -> ${CMD} ${ARGS}"
echo "cmd: ${CMD} ${ARGS}" >>"${LOG}" 2>&1

# shellcheck disable=SC2086
if run_with_timeout ${CMD} ${ARGS} >>"${LOG}" 2>&1; then
  echo "dspy-evolution: completed OK (details in ${LOG})"
  tail -n 5 "${LOG}" 2>/dev/null || true
  exit 0
else
  rc=$?
  echo "dspy-evolution: run exited ${rc} — see ${LOG} (cron stays healthy; not failing)"
  tail -n 10 "${LOG}" 2>/dev/null || true
  # Exit 0 on purpose: a bad evolution run must not spam the scheduler as failed.
  exit 0
fi
