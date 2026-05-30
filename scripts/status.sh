#!/usr/bin/env bash
# status.sh — the human view of the whole stack (Stage 5).
#
# For every server in the manifest: UP/DOWN, port, PID, uptime, last health
# result. One glance shows the whole stack's state. (Distinct from healthcheck.sh,
# which is pass/fail for scripting; status.sh is for a human.) Manifest-driven.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; Z=$'\033[0m'
else G=""; R=""; D=""; Z=""; fi

echo "═══ hermes-max status ═══  profile=${HMX_PROFILE}  host=$(hmx_bind_host)"
printf '%-16s %-5s %-7s %-8s %-9s %s\n' "server" "port" "state" "pid" "uptime" "health"
printf '%s\n' "$(printf '─%.0s' $(seq 1 64))"

UP=0; TOTAL=0
for name in "${HMX_ACTIVE_SERVERS[@]}"; do
  TOTAL=$((TOTAL+1))
  port="$(hmx_port "${name}")"
  pidfile="$(hmx_pidfile "${name}")"
  pid="$(cat "${pidfile}" 2>/dev/null || true)"
  # Prefer the pidfile PID; fall back to whoever holds the port.
  if ! hmx_pid_alive "${pid}"; then pid="$(hmx_port_pids "${port}")"; fi
  uptime="$(hmx_pid_uptime "${pid%% *}")"
  if body="$(curl -fsS -m 3 "$(hmx_health_url "${name}")" 2>/dev/null)"; then
    UP=$((UP+1))
    state="${G}UP${Z}"
    # pull a short field from the health JSON if present
    hb="$(printf '%s' "${body}" | grep -oE '"status"[ ]*:[ ]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
    health="${D}${hb:-ok}${Z}"
  elif [ -n "${pid}" ]; then
    state="${R}DOWN${Z}"; health="${D}process up, health failing${Z}"
  else
    state="${R}DOWN${Z}"; health="${D}not running${Z}"
  fi
  printf '%-16s %-5s %-16b %-8s %-9s %b\n' \
    "${HMX_DIR[$name]#mcp-}" "${port}" "${state}" "${pid:-—}" "${uptime:-—}" "${health}"
done
printf '%s\n' "$(printf '─%.0s' $(seq 1 64))"
echo "${UP}/${TOTAL} servers up"

echo "── supporting (informational) ──"
hmx_phoenix_otlp_ok && echo "  ${G}✓${Z} Phoenix OTLP ${PHOENIX_COLLECTOR_ENDPOINT:-http://localhost:4317}" \
                    || echo "  ${D}• Phoenix OTLP down (./phoenix.sh)${Z}"
for pair in "embed:${EMBED_PORT:-8002}" "rerank:${RERANK_PORT:-8003}"; do
  svc="${pair%%:*}"; port="${pair##*:}"
  [ -n "$(hmx_port_pids "${port}")" ] && echo "  ${G}✓${Z} ${svc} serve (port ${port})" \
                                      || echo "  ${D}• ${svc} serve down (serve-${svc}.sh)${Z}"
done
[ "${UP}" -eq "${TOTAL}" ] && exit 0 || exit 1
