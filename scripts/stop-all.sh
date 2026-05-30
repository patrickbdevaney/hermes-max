#!/usr/bin/env bash
# stop-all.sh — cleanly stop EVERY hermes-max MCP server (Stage 5).
#
# Kills each MCP by its PID file, falls back to a port-based kill for any that
# don't die, then confirms all MCP ports + the optional embed/rerank ports are
# free. Manifest-driven (adding a server needs no edit here). Idempotent — safe to
# run when nothing is up.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

echo "═══ stopping hermes-max MCP servers ═══"
# Stop ALL manifest servers (not just the active profile) so a profile switch can
# never strand a process from the other profile.
for name in "${HMX_SERVERS[@]}"; do
  note="$(hmx_stop_one "${name}")"
  if [ -n "${note}" ]; then
    echo "  ✓ stopped ${HMX_DIR[$name]} (${note})"
  else
    echo "  • ${HMX_DIR[$name]} already down"
  fi
done

# Optional embed/rerank serves (gpu_local only; not MCP servers, no manifest entry).
for pair in "embed:${EMBED_PORT:-8002}" "rerank:${RERANK_PORT:-8003}"; do
  svc="${pair%%:*}"; port="${pair##*:}"
  pids="$(hmx_port_pids "${port}")"
  if [ -n "${pids}" ]; then
    kill ${pids} 2>/dev/null
    sleep 0.5
    pids="$(hmx_port_pids "${port}")"; [ -n "${pids}" ] && kill -9 ${pids} 2>/dev/null
    echo "  ✓ stopped ${svc} serve (port ${port})"
  fi
done

echo "── confirming ports are free ──"
BUSY=0
# MCP ports from the manifest + embed/rerank.
declare -a CHECK_PORTS=()
for name in "${HMX_SERVERS[@]}"; do CHECK_PORTS+=("$(hmx_port "${name}")"); done
CHECK_PORTS+=("${EMBED_PORT:-8002}" "${RERANK_PORT:-8003}")
for port in "${CHECK_PORTS[@]}"; do
  pids="$(hmx_port_pids "${port}")"
  if [ -n "${pids}" ]; then
    echo "  ✗ port ${port} STILL in use by: ${pids}"
    BUSY=1
  fi
done
if [ "${BUSY}" -eq 0 ]; then
  echo "  ✓ all MCP + embed/rerank ports free"
fi
exit "${BUSY}"
