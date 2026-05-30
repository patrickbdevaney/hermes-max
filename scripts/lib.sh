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
declare -A HMX_PROFILES HMX_REQUIRES HMX_DEGRADES
if [ -f "${HMX_MANIFEST}" ]; then
  if ! eval "$(HMX_MANIFEST="${HMX_MANIFEST}" python3 "${HMX_LIB_DIR}/manifest.py" 2>/dev/null)"; then
    echo "lib.sh: WARNING — failed to parse ${HMX_MANIFEST}; server list is empty" >&2
  fi
else
  echo "lib.sh: WARNING — manifest ${HMX_MANIFEST} not found; server list is empty" >&2
fi

# ── deploy-profile bifurcation (Stage 0) ──────────────────────────────────────
# ONE codebase, two profiles: gpu_local (DEFAULT — maximalist; CUDA embed/rerank
# is optional and started by serve-*.sh, NOT by any MCP venv) and lean_cloud
# (CPU / Mac-mini / VPS; no torch/CUDA anywhere; cloud chat via $VLLM_BASE_URL).
# Profiles filter WHICH manifest servers run — never cap a gpu_local capability.
# Resolution order (highest first): explicit env/CLI DEPLOY_PROFILE > .env > default.
HMX_VALID_PROFILES="gpu_local lean_cloud"
HMX_PROFILE="${DEPLOY_PROFILE:-gpu_local}"

# True if a server (by manifest name) runs in the active profile. Servers with no
# `profiles:` entry default to BOTH (manifest.py fills that in).
hmx_in_profile() {
  # NOTE: keep these as SEPARATE `local` statements — a single
  # `local name=$1 profs=${HMX_PROFILES[$name]...}` expands profs' RHS before
  # `name` is bound (bash gotcha → empty subscript / unbound under set -u).
  local name="$1"
  local profs="${HMX_PROFILES[$name]:-gpu_local lean_cloud}"
  case " ${profs} " in *" ${HMX_PROFILE} "*) return 0 ;; *) return 1 ;; esac
}

# Same check by DIRECTORY (used by bootstrap's filesystem discovery). Unknown
# dirs (not in the manifest) are NOT filtered out — they are warned about instead.
hmx_dir_in_profile() {
  local dir="$1" n
  for n in "${HMX_SERVERS[@]}"; do
    if [ "${HMX_DIR[$n]}" = "${dir}" ]; then hmx_in_profile "${n}"; return $?; fi
  done
  return 0
}

# Rebuild HMX_ACTIVE_SERVERS for the current HMX_PROFILE.
declare -a HMX_ACTIVE_SERVERS=()
hmx_compute_active() {
  HMX_ACTIVE_SERVERS=()
  local n
  for n in "${HMX_SERVERS[@]}"; do
    hmx_in_profile "${n}" && HMX_ACTIVE_SERVERS+=("${n}")
  done
}
hmx_compute_active

hmx_load_env() {
  # An explicit env/CLI DEPLOY_PROFILE (e.g. from bootstrap-lean.sh) must win over
  # whatever .env says, so capture it before sourcing .env, then restore it.
  local _pre_profile="${DEPLOY_PROFILE:-}"
  if [ -f "${REPO_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "${REPO_ROOT}/.env"
    set +a
  fi
  [ -n "${_pre_profile}" ] && DEPLOY_PROFILE="${_pre_profile}"
  HMX_PROFILE="${DEPLOY_PROFILE:-gpu_local}"
  hmx_compute_active
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

# ── process lifecycle helpers (Stage 5) ───────────────────────────────────────
# All manifest-driven: stop-all/restart/status use these so adding a server (one
# manifest entry) needs no lifecycle-script edits.

hmx_pidfile() { echo "${HMX_RUN_DIR}/$1.pid"; }
hmx_logfile() { echo "${HMX_LOG_DIR}/$1.log"; }

# PID(s) listening on a TCP port — lsof, then ss, then fuser (whichever exists).
hmx_port_pids() {
  local port="$1" pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -ti :"${port}" -sTCP:LISTEN 2>/dev/null)"
  fi
  if [ -z "${pids}" ] && command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnpH "sport = :${port}" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)"
  fi
  if [ -z "${pids}" ] && command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "${port}"/tcp 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$')"
  fi
  echo "${pids}" | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//'
}

hmx_pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

# Human uptime of a PID (etimes -> Xs / Xm Ys / Xh Ym). Empty if not running.
hmx_pid_uptime() {
  local pid="$1" s
  s="$(ps -o etimes= -p "${pid}" 2>/dev/null | tr -d ' ')"
  [ -z "${s}" ] && { echo ""; return; }
  if   [ "${s}" -lt 60 ];   then echo "${s}s"
  elif [ "${s}" -lt 3600 ]; then echo "$((s/60))m $((s%60))s"
  else echo "$((s/3600))h $(((s%3600)/60))m"; fi
}

# Stop ONE server: TERM the pidfile PID, then any port listener, KILL if stubborn.
# Echoes a short what-happened note. Idempotent (no-op if already down).
hmx_stop_one() {
  local name="$1" port pidfile pid stopped="" leftover
  port="$(hmx_port "${name}")"
  pidfile="$(hmx_pidfile "${name}")"
  if [ -f "${pidfile}" ]; then
    pid="$(cat "${pidfile}" 2>/dev/null)"
    if hmx_pid_alive "${pid}"; then
      kill "${pid}" 2>/dev/null
      for _ in 1 2 3 4 5 6 7 8 9 10; do hmx_pid_alive "${pid}" || break; sleep 0.3; done
      hmx_pid_alive "${pid}" && kill -9 "${pid}" 2>/dev/null
      stopped="pid ${pid}"
    fi
    rm -f "${pidfile}"
  fi
  # Port fallback: anything still listening on the server's port.
  leftover="$(hmx_port_pids "${port}")"
  if [ -n "${leftover}" ]; then
    kill ${leftover} 2>/dev/null
    for _ in 1 2 3 4 5 6; do [ -z "$(hmx_port_pids "${port}")" ] && break; sleep 0.3; done
    leftover="$(hmx_port_pids "${port}")"
    [ -n "${leftover}" ] && kill -9 ${leftover} 2>/dev/null
    stopped="${stopped:+${stopped}, }port ${port}"
  fi
  echo "${stopped}"
}

# Start ONE server in the background (mirrors start-all.sh; pidfile holds the real
# python PID). No-op-friendly: caller decides whether to skip an already-healthy one.
hmx_start_one() {
  local name="$1" dir host port log pidfile
  dir="${HMX_DIR[$name]}"; host="$(hmx_bind_host)"; port="$(hmx_port "${name}")"
  log="$(hmx_logfile "${name}")"; pidfile="$(hmx_pidfile "${name}")"
  mkdir -p "${HMX_RUN_DIR}" "${HMX_LOG_DIR}"
  hmx_ensure_venv "${dir}"
  nohup "${REPO_ROOT}/${dir}/.venv/bin/python" "${REPO_ROOT}/${dir}/server.py" >>"${log}" 2>&1 &
  echo $! >"${pidfile}"
}

# Poll a server's /health until ok or timeout. Returns 0/1.
hmx_wait_health() {
  local name="$1" timeout="${2:-15}" url; url="$(hmx_health_url "${name}")"
  local end=$(( $(date +%s) + timeout ))
  while [ "$(date +%s)" -lt "${end}" ]; do
    curl -fsS -m 2 "${url}" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

# ── store paths (Stage 6 snapshots) ───────────────────────────────────────────
# The PERMANENT, COMPOUNDING stores. Resolved from .env (same vars the servers
# read), with the documented defaults and ~ expanded.
hmx_expand()     { echo "${1/#\~/$HOME}"; }
hmx_rag_path()   { hmx_expand "${RAG_INDEX_PATH:-$HOME/.hermes-max/rag/index.db}"; }
hmx_kg_path()    { hmx_expand "${KG_DB_PATH:-$HOME/.hermes-max/kg/graph.db}"; }
hmx_corpus_dir() { hmx_expand "${RESEARCH_CORPUS_DIR:-$HOME/.hermes-max/corpus}"; }
hmx_snap_root()  { hmx_expand "${HMX_SNAPSHOT_DIR:-$HOME/.hermes-max/snapshots}"; }

# Copy a SQLite db AND its -wal/-shm sidecars (uncheckpointed state) if present.
hmx_copy_sqlite() {
  local src="$1" destdir="$2" f
  mkdir -p "${destdir}"
  for f in "${src}" "${src}-wal" "${src}-shm"; do
    [ -f "${f}" ] && cp -p "${f}" "${destdir}/"
  done
}
