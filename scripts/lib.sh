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

# The servers are NOT hardcoded here anymore — they are loaded from the single
# source of truth, mcp-manifest.yaml, via scripts/manifest.py (stdlib-only, so
# this works on a freshly-cloned machine before bootstrap installs anything).
# Adding a server = one manifest entry; every script that sources lib.sh picks
# it up automatically. The variable NAMES are unchanged, so all existing scripts
# (start-all/healthcheck/smoke-test, which loop over HMX_SERVERS) keep working.
#
# Ordering note preserved from the manifest: mcp-verify is listed first so that,
# in smoke-test ordering, its venv exists before mcp-checkpoint's smoke test
# boots a throwaway verify against the real boundary.
HMX_MANIFEST="${HMX_MANIFEST:-${REPO_ROOT}/mcp-manifest.yaml}"
declare -a HMX_SERVERS=()
declare -A HMX_DIR HMX_PORTVAR HMX_PORTDEF HMX_REGISTER_AS HMX_HEALTH
if [ -f "${HMX_MANIFEST}" ]; then
  if ! eval "$(HMX_MANIFEST="${HMX_MANIFEST}" python3 "${HMX_LIB_DIR}/manifest.py" 2>/dev/null)"; then
    echo "lib.sh: WARNING — failed to parse ${HMX_MANIFEST}; server list is empty" >&2
  fi
else
  echo "lib.sh: WARNING — manifest ${HMX_MANIFEST} not found; server list is empty" >&2
fi

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
  local path="${HMX_HEALTH[$name]:-/health}"
  echo "http://$(hmx_bind_host):$(hmx_port "$name")${path}"
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
