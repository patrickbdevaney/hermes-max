#!/usr/bin/env bash
# Start every hermes-max MCP server as an INDEPENDENT background process.
# Identical on laptop and your inference host — only $VLLM_BASE_URL differs (read from .env).
#
# Each server: own venv, own port (127.0.0.1), own log + pidfile. Killing any
# one does not affect the others (anti-Frankenstein). Re-running is safe: a
# server already healthy on its port is left alone.
#
# This script brings up the five MCP servers (what this repo owns). Hermes
# itself, Phoenix and SearXNG are separate processes — their status is checked
# and printed, but they are started by `hermes`, `./phoenix.sh`, `./searXNG.sh`.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

mkdir -p "${HMX_RUN_DIR}" "${HMX_LOG_DIR}"

echo "═══ starting hermes-max MCP servers ═══"
echo "VLLM_BASE_URL=${VLLM_BASE_URL:-<unset!>}"
[ -z "${VLLM_BASE_URL:-}" ] && echo "  WARNING: VLLM_BASE_URL is unset — copy .env.example to .env"

echo "DEPLOY_PROFILE=${HMX_PROFILE}  (active servers: ${HMX_ACTIVE_SERVERS[*]})"

for name in "${HMX_ACTIVE_SERVERS[@]}"; do
  dir="${HMX_DIR[$name]}"
  host="$(hmx_bind_host)"
  port="$(hmx_port "$name")"
  url="http://${host}:${port}/health"
  pidfile="${HMX_RUN_DIR}/${name}.pid"
  log="${HMX_LOG_DIR}/${name}.log"

  if curl -fsS -m 2 "${url}" >/dev/null 2>&1; then
    echo "• ${dir}: already healthy on ${host}:${port} — skipping"
    continue
  fi

  hmx_ensure_venv "${dir}"
  echo "• ${dir}: starting on ${host}:${port}"
  # Run python directly (no wrapper subshell) so the pidfile holds python's real
  # PID. Running by absolute path puts the script's dir on sys.path, so the
  # server's sibling imports resolve regardless of cwd.
  nohup "${REPO_ROOT}/${dir}/.venv/bin/python" "${REPO_ROOT}/${dir}/server.py" >>"${log}" 2>&1 &
  echo $! >"${pidfile}"
done

echo
echo "waiting for health…"
sleep 2
ALL_OK=1
for name in "${HMX_ACTIVE_SERVERS[@]}"; do
  url="$(hmx_health_url "$name")"
  ok=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS -m 2 "${url}" >/dev/null 2>&1; then ok=1; break; fi
    sleep 0.5
  done
  if [ "${ok}" -eq 1 ]; then
    echo "  ✓ ${HMX_DIR[$name]} healthy ($(hmx_port "$name"))"
  else
    echo "  ✗ ${HMX_DIR[$name]} did NOT come up — see ${HMX_LOG_DIR}/${name}.log"
    ALL_OK=0
  fi
done

echo
echo "── supporting services (started separately) ──"
hmx_phoenix_otlp_ok \
  && echo "  ✓ Phoenix OTLP reachable (${PHOENIX_COLLECTOR_ENDPOINT:-http://localhost:4317})" \
  || echo "  • Phoenix OTLP not reachable (run ./phoenix.sh)"
curl -fsS -m 2 "http://localhost:6006" >/dev/null 2>&1 \
  && echo "  ✓ Phoenix UI http://localhost:6006" || echo "  • Phoenix UI down"
curl -fsS -m 2 "http://localhost:8080" >/dev/null 2>&1 \
  && echo "  ✓ SearXNG http://localhost:8080" || echo "  • SearXNG down (run ./searXNG.sh)"
if [ -n "${VLLM_BASE_URL:-}" ]; then
  curl -fsS -m 5 "${VLLM_BASE_URL}/models" >/dev/null 2>&1 \
    && echo "  ✓ vLLM reachable at ${VLLM_BASE_URL}" || echo "  • vLLM NOT reachable at ${VLLM_BASE_URL}"
fi

echo
echo "Next: register with Hermes (scripts/register-mcp.sh), then run 'hermes'."
[ "${ALL_OK}" -eq 1 ] && exit 0 || exit 1
