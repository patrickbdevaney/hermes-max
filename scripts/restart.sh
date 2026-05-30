#!/usr/bin/env bash
# restart.sh [server|all] — stop then start one named server, or the whole stack.
#
#   restart.sh research     # restart ONLY mcp-research
#   restart.sh all          # restart everything (default if no arg)
#   restart.sh              # same as `all`
#
# Manifest-driven; re-runs the health check and reports the final state. The
# server name is the short manifest key (verify/rag/kg/research/…), with or
# without an `mcp-` prefix.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

TARGET="${1:-all}"
TARGET="${TARGET#mcp-}"   # accept `mcp-research` or `research`

# Resolve the target to manifest names.
declare -a TARGETS=()
if [ "${TARGET}" = "all" ]; then
  TARGETS=("${HMX_ACTIVE_SERVERS[@]}")
else
  for name in "${HMX_SERVERS[@]}"; do
    [ "${name}" = "${TARGET}" ] && TARGETS=("${name}")
  done
  if [ "${#TARGETS[@]}" -eq 0 ]; then
    echo "✗ unknown server '${1}'. Known: ${HMX_SERVERS[*]} (or 'all')."
    exit 2
  fi
fi

echo "═══ restarting: ${TARGETS[*]} ═══"
RC=0
for name in "${TARGETS[@]}"; do
  dir="${HMX_DIR[$name]}"; port="$(hmx_port "${name}")"
  note="$(hmx_stop_one "${name}")"
  echo "• ${dir}: stopped${note:+ (${note})}; starting on $(hmx_bind_host):${port}…"
  hmx_start_one "${name}"
  if hmx_wait_health "${name}" 20; then
    pid="$(cat "$(hmx_pidfile "${name}")" 2>/dev/null)"
    echo "  ✓ ${dir} healthy (port ${port}, pid ${pid})"
  else
    echo "  ✗ ${dir} did NOT come up — see $(hmx_logfile "${name}")"
    RC=1
  fi
done
exit "${RC}"
