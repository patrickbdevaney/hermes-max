#!/usr/bin/env bash
# watch.sh — the LIVE tool-call stream (Stage 3 observability).
#
# Run this in a side terminal and see the ENTIRE agent loop in real time: which
# tool is running, its input, how long it took, what came back, every heartbeat,
# every fallback, and every routing/kill DECISION with its reason. It is the
# operator-facing real-time-clarity view; Phoenix is the post-hoc analysis view.
# Both are fed by the same events (lib/livelog.py via every server's otel_emit).
#
# It simply colourises and tails $HERMES_MAX_LOG_DIR/live.log. Read-only; safe to
# start/stop anytime; if the log doesn't exist yet it waits for the first event.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env 2>/dev/null || true

# Prefer the structured JSONL renderer (server · tool · input · status · timing +
# research fan-out sub-entries) — the same view the cockpit shows. Opt out with
# HMX_WATCH_PLAIN=1 for the legacy colourised tail of live.log below.
if [ -z "${HMX_WATCH_PLAIN:-}" ] && command -v python3 >/dev/null 2>&1 \
   && [ -f "${SCRIPT_DIR}/cockpit_livelog.py" ]; then
  exec python3 "${SCRIPT_DIR}/cockpit_livelog.py"
fi

LOG_DIR="${HERMES_MAX_LOG_DIR:-${HMX_LOG_DIR:-${HOME}/.hermes-max/logs}}"
LOG_DIR="${LOG_DIR/#\~/$HOME}"
LIVE="${LOG_DIR}/live.log"

mkdir -p "${LOG_DIR}"
[ -f "${LIVE}" ] || : >"${LIVE}"

# Colour only when stdout is a TTY (so piping stays clean).
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
  C_YEL=$'\033[33m'; C_CYAN=$'\033[36m'; C_BLUE=$'\033[34m'; C_MAG=$'\033[35m'
else
  C_RESET=""; C_DIM=""; C_GREEN=""; C_RED=""; C_YEL=""; C_CYAN=""; C_BLUE=""; C_MAG=""
fi

echo "${C_DIM}── hermes-max LIVE tool-call stream  (verbosity=${HERMES_MAX_VERBOSITY:-verbose})${C_RESET}"
echo "${C_DIM}── tailing ${LIVE} — Ctrl-C to stop ──${C_RESET}"

colour() {
  # Tag each glyph with a colour so the stream is scannable at a glance.
  while IFS= read -r line; do
    case "${line}" in
      *"→ TOOL"*)      printf '%s%s%s\n' "${C_CYAN}"  "${line}" "${C_RESET}" ;;
      *"✓"*"OK"*)      printf '%s%s%s\n' "${C_GREEN}" "${line}" "${C_RESET}" ;;
      *"✗"*)           printf '%s%s%s\n' "${C_RED}"   "${line}" "${C_RESET}" ;;
      *"⚠"*)           printf '%s%s%s\n' "${C_YEL}"   "${line}" "${C_RESET}" ;;
      *"⟳"*)           printf '%s%s%s\n' "${C_DIM}"   "${line}" "${C_RESET}" ;;
      *"DECISION"*)    printf '%s%s%s\n' "${C_MAG}"   "${line}" "${C_RESET}" ;;
      *"look-ahead"*|*"⊙"*) printf '%s%s%s\n' "${C_BLUE}" "${line}" "${C_RESET}" ;;
      *)               printf '%s\n' "${line}" ;;
    esac
  done
}

# -F: follow across truncation/rotation (a new task may reset the log).
exec tail -n "${WATCH_TAIL_LINES:-40}" -F "${LIVE}" | colour
