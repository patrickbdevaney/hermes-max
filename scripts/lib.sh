#!/usr/bin/env bash
# Shared helpers for the hermes-max scripts. Sourced, not executed.
# Works UNCHANGED on the laptop (Tailscale VLLM_BASE_URL) and on your inference host
# (localhost VLLM_BASE_URL) — the only difference is the value of $VLLM_BASE_URL,
# which is read from the environment / .env. No host is ever hardcoded here.

# Resolve repo root regardless of where the caller invoked from.
HMX_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HMX_LIB_DIR}/.." && pwd)"

HMX_RUN_DIR="${HOME}/.hermes-max/run"
HMX_LOG_DIR="${HOME}/.hermes-max/logs"

# The servers: name -> directory, port env var, default port. mcp-verify is
# listed first so that, in smoke-test ordering, its venv exists before
# mcp-checkpoint's smoke test boots a throwaway verify against the real boundary.
HMX_SERVERS=(verify rag kg observability escalation checkpoint watchdog search)
declare -A HMX_DIR=(
  [verify]="mcp-verify"
  [rag]="mcp-codebase-rag"
  [kg]="mcp-knowledge-graph"
  [observability]="mcp-observability"
  [escalation]="mcp-escalation"
  [checkpoint]="mcp-checkpoint"
  [watchdog]="mcp-watchdog"
  [search]="mcp-search"
)
declare -A HMX_PORTVAR=(
  [verify]="MCP_VERIFY_PORT"
  [rag]="MCP_RAG_PORT"
  [kg]="MCP_KG_PORT"
  [observability]="MCP_OBSERVABILITY_PORT"
  [escalation]="MCP_ESCALATION_PORT"
  [checkpoint]="MCP_CHECKPOINT_PORT"
  [watchdog]="MCP_WATCHDOG_PORT"
  [search]="MCP_SEARCH_PORT"
)
declare -A HMX_PORTDEF=(
  [verify]="9101"
  [rag]="9102"
  [kg]="9103"
  [observability]="9104"
  [escalation]="9105"
  [checkpoint]="9106"
  [watchdog]="9107"
  [search]="9108"
)

hmx_load_env() {
  if [ -f "${REPO_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "${REPO_ROOT}/.env"
    set +a
  fi
}

# Echo the resolved port for a server (env override or default).
hmx_port() {
  local name="$1" var def
  var="${HMX_PORTVAR[$name]}"
  def="${HMX_PORTDEF[$name]}"
  echo "${!var:-$def}"
}

hmx_bind_host() {
  echo "${MCP_BIND_HOST:-127.0.0.1}"
}

# Create a server's venv and install its requirements if needed.
hmx_ensure_venv() {
  local dir="$1"
  local path="${REPO_ROOT}/${dir}"
  local py="${path}/.venv/bin/python"
  if [ ! -x "${py}" ]; then
    echo "  [setup] creating venv for ${dir}"
    python3 -m venv "${path}/.venv"
    "${py}" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  fi
  # Install requirements if a marker is missing or requirements changed.
  local req="${path}/requirements.txt"
  local stamp="${path}/.venv/.requirements.sha"
  if [ -f "${req}" ]; then
    local cur
    cur="$(sha1sum "${req}" | awk '{print $1}')"
    if [ ! -f "${stamp}" ] || [ "$(cat "${stamp}" 2>/dev/null)" != "${cur}" ]; then
      echo "  [setup] installing requirements for ${dir}"
      "${py}" -m pip install -q -r "${req}" && echo "${cur}" >"${stamp}"
    fi
  fi
}

hmx_health_url() {
  local name="$1"
  echo "http://$(hmx_bind_host):$(hmx_port "$name")/health"
}

# TCP reachability (for gRPC/OTLP ports that don't answer plain HTTP GET).
hmx_tcp_ok() {
  local host="$1" port="$2"
  timeout 2 bash -c ">/dev/tcp/${host}/${port}" 2>/dev/null
}

# TCP-check the OTLP endpoint from PHOENIX_COLLECTOR_ENDPOINT (host:port).
hmx_phoenix_otlp_ok() {
  local ep="${PHOENIX_COLLECTOR_ENDPOINT:-http://localhost:4317}"
  ep="${ep#*://}"
  hmx_tcp_ok "${ep%%:*}" "${ep##*:}"
}
