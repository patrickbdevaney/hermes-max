#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# bootstrap.sh — the ONE command that brings hermes-max fully live on any of the
# operator's machines (laptop, your inference host, mini-PC), with NO chmod, NO manual venv
# creation, and NO manual MCP registration.
#
#   bash bootstrap.sh            # set everything up (idempotent, safe to re-run)
#   bash bootstrap.sh --check    # dry-run AUDIT: report what's missing, change nothing
#   bash bootstrap.sh --no-smoke # skip the per-server smoke tests (faster)
#
# Always invoke via `bash bootstrap.sh` — it needs no execute bit, and it
# chmod +x's the repo's own scripts so YOU never have to.
#
# What it does (idempotent throughout):
#   1. chmod +x every repo script (so no `chmod +x` dance, ever)
#   2. detect OS/arch, python, the Hermes install + its interpreter, Docker
#   3. create .env from .env.example if absent (prompts only for no-safe-default)
#   4. DISCOVER every MCP server dir (scan for */server.py + */requirements.txt —
#      so a server added by a later stage is picked up with no edits here), then
#      for each: create .venv if missing, pip install -r requirements.txt, run
#      smoke_test.py, report PASS/FAIL
#   5. register the servers + skills with Hermes (register-mcp.sh) and apply the
#      native deadline knobs (apply-config-deadlines.sh) — both idempotent
#   6. print a healthcheck summary and the exact `hermes` restart line
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
# shellcheck source=scripts/lib.sh
. "${REPO_ROOT}/scripts/lib.sh"
# shellcheck source=lib/ensure_dep.sh
. "${REPO_ROOT}/lib/ensure_dep.sh"

CHECK=0
DO_SMOKE=1
for arg in "$@"; do
  case "${arg}" in
    --check)    CHECK=1 ;;
    --no-smoke) DO_SMOKE=0 ;;
    -h|--help)
      sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "bootstrap.sh: unknown arg '${arg}' (try --help)" >&2; exit 2 ;;
  esac
done

# ── tiny output helpers ───────────────────────────────────────────────────────
c_ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
c_warn() { printf '  \033[33m•\033[0m %s\n' "$*"; }
c_bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
hdr()    { printf '\n\033[1m═══ %s ═══\033[0m\n' "$*"; }

MISSING=0   # incremented by --check when something would need doing

if [ "${CHECK}" -eq 1 ]; then
  echo "═══ hermes-max bootstrap — DRY-RUN AUDIT (no changes) ═══"
else
  echo "═══ hermes-max bootstrap ═══"
fi

# ── 1. chmod +x repo scripts (so the user never needs to) ─────────────────────
hdr "1. executable bits"
mapfile -t _scripts < <(
  find "${REPO_ROOT}" -maxdepth 2 -type f -name '*.sh' \
    -not -path '*/.venv/*' -not -path '*/.git/*' 2>/dev/null
)
_need_chmod=0
for s in "${_scripts[@]}"; do
  [ -x "${s}" ] || _need_chmod=$((_need_chmod + 1))
done
if [ "${_need_chmod}" -eq 0 ]; then
  c_ok "all ${#_scripts[@]} repo scripts already executable"
elif [ "${CHECK}" -eq 1 ]; then
  c_warn "${_need_chmod} script(s) need +x"; MISSING=$((MISSING + 1))
else
  chmod +x "${_scripts[@]}" 2>/dev/null || true
  c_ok "chmod +x applied to ${#_scripts[@]} repo scripts"
fi

# ── 2. detect environment ─────────────────────────────────────────────────────
hdr "2. environment"
OS="$(uname -s)"; ARCH_RAW="$(uname -m)"
case "${ARCH_RAW}" in
  x86_64|amd64)        ARCH=amd64 ;;
  aarch64|arm64)       ARCH=arm64 ;;
  *)                   ARCH="${ARCH_RAW}" ;;
esac
c_ok "OS=${OS}  arch=${ARCH_RAW} (docker-platform: linux/${ARCH})"

if command -v python3 >/dev/null 2>&1; then
  c_ok "python3 = $(python3 --version 2>&1 | awk '{print $2}') ($(command -v python3))"
else
  c_bad "python3 NOT found — required"; MISSING=$((MISSING + 1))
fi

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
if command -v hermes >/dev/null 2>&1; then
  _hbin="$(command -v hermes)"
  # The shebang may be `#!/usr/bin/env python3` (wrapper) or a direct interpreter
  # path. Show whichever is informative; we never pip into it regardless.
  _hsheb="$(head -1 "${_hbin}" 2>/dev/null | sed 's/^#!//')"
  c_ok "hermes = $(hermes --version 2>&1 | head -1)"
  [ -n "${_hsheb}" ] && c_ok "hermes shebang =${_hsheb}"
  if [ -f "${HERMES_HOME}/config.yaml" ]; then
    c_ok "hermes config = ${HERMES_HOME}/config.yaml"
  else
    c_warn "hermes config not found at ${HERMES_HOME}/config.yaml (register step will warn)"
  fi
else
  c_warn "hermes NOT on PATH — servers will still run; registration step will be skipped"
fi
# NOTE: we never pip into Hermes's own interpreter. Each MCP server (and the
# dspy-evolution job) owns an isolated .venv — that is the anti-Frankenstein rule.

if command -v docker >/dev/null 2>&1; then
  if docker ps >/dev/null 2>&1; then
    c_ok "docker = $(docker --version | awk '{print $3}' | tr -d ,) (daemon up)"
    for cn in phoenix searxng; do
      docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${cn}" \
        && c_ok "  container '${cn}' running" \
        || c_warn "  container '${cn}' not running (optional: ./${cn}.sh / ./searXNG.sh / ./phoenix.sh)"
    done
  else
    c_warn "docker present but daemon not reachable (Phoenix/SearXNG/Crawl4AI optional)"
  fi
else
  c_warn "docker NOT found — Phoenix/SearXNG/Crawl4AI are optional; core stack runs without them"
fi

# ── 3. .env from .env.example ─────────────────────────────────────────────────
hdr "3. .env"
if [ -f "${REPO_ROOT}/.env" ]; then
  c_ok ".env present"
else
  if [ "${CHECK}" -eq 1 ]; then
    c_warn ".env missing — would be created from .env.example"; MISSING=$((MISSING + 1))
  else
    cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
    c_ok ".env created from .env.example"
    # The only value with no safe default is the chat model host. Prompt (with a
    # localhost default) ONLY when interactive; otherwise leave the example value.
    if [ -t 0 ]; then
      printf '    VLLM_BASE_URL [http://localhost:8001/v1]: ' >&2
      read -r _vllm || true
      _vllm="${_vllm:-http://localhost:8001/v1}"
      python3 - "$REPO_ROOT/.env" "$_vllm" <<'PY'
import re, sys
path, url = sys.argv[1], sys.argv[2]
txt = open(path).read()
txt = re.sub(r'(?m)^VLLM_BASE_URL=.*$', f'VLLM_BASE_URL={url}', txt, count=1)
open(path, "w").write(txt)
PY
      c_ok "VLLM_BASE_URL set to ${_vllm}"
    else
      c_warn "non-interactive — left VLLM_BASE_URL at the .env.example default; edit .env if needed"
    fi
  fi
fi
# (re)load env now that .env exists
hmx_load_env

# ── 4. discover + set up every MCP server (generic: scan, don't hardcode) ─────
hdr "4. MCP servers (discover → venv → deps → smoke)"
# Discover by filesystem scan so a server dropped in by a later stage is picked
# up with ZERO edits to this script. A "server dir" = has BOTH server.py and
# requirements.txt directly inside it.
mapfile -t _server_dirs < <(
  find "${REPO_ROOT}" -maxdepth 2 -mindepth 2 -name requirements.txt \
    -not -path '*/.venv/*' -not -path '*/.git/*' -printf '%h\n' 2>/dev/null \
    | while read -r d; do [ -f "${d}/server.py" ] && basename "${d}"; done | sort -u
)
if [ "${#_server_dirs[@]}" -eq 0 ]; then
  c_bad "no MCP server dirs discovered (expected */server.py + */requirements.txt)"
  MISSING=$((MISSING + 1))
fi

# Cross-check against the manifest so a discovered-but-unregistered server is loud.
declare -A _in_manifest=()
for n in "${HMX_SERVERS[@]}"; do _in_manifest["${HMX_DIR[$n]}"]=1; done

declare -a SMOKE_FAIL=()
for dir in "${_server_dirs[@]}"; do
  path="${REPO_ROOT}/${dir}"
  py="${path}/.venv/bin/python"
  printf '\n── %s ──\n' "${dir}"
  [ -n "${_in_manifest[$dir]:-}" ] || c_warn "NOT in mcp-manifest.yaml — add an entry so all scripts pick it up"

  if [ "${CHECK}" -eq 1 ]; then
    [ -x "${py}" ] && c_ok "venv present" || { c_warn "venv missing — would create"; MISSING=$((MISSING + 1)); }
    stamp="${path}/.venv/.requirements.sha"
    if [ -f "${path}/requirements.txt" ]; then
      cur="$(sha1sum "${path}/requirements.txt" | awk '{print $1}')"
      if [ -x "${py}" ] && [ "$(cat "${stamp}" 2>/dev/null)" = "${cur}" ]; then
        c_ok "requirements installed (up to date)"
      else
        c_warn "requirements not installed / stale — would pip install"; MISSING=$((MISSING + 1))
      fi
    fi
    continue
  fi

  # real run: create venv + install reqs (hmx_ensure_venv handles both, stamped)
  hmx_ensure_venv "${dir}"
  if [ ! -x "${py}" ]; then
    c_bad "venv creation failed"; SMOKE_FAIL+=("${dir}"); continue
  fi
  c_ok "venv + requirements ready"

  if [ "${DO_SMOKE}" -eq 1 ] && [ -f "${path}/smoke_test.py" ]; then
    if timeout 180 "${py}" "${path}/smoke_test.py" >/tmp/hmx_smoke_"${dir}".log 2>&1; then
      c_ok "smoke_test PASSED"
    else
      c_bad "smoke_test FAILED (see /tmp/hmx_smoke_${dir}.log)"; SMOKE_FAIL+=("${dir}")
    fi
  elif [ "${DO_SMOKE}" -eq 1 ]; then
    c_warn "no smoke_test.py"
  fi
done

# ── 5. register with Hermes + apply native deadline knobs ─────────────────────
hdr "5. register with Hermes"
if [ "${CHECK}" -eq 1 ]; then
  if [ -f "${HERMES_HOME}/config.yaml" ]; then
    _expected=""
    for n in "${HMX_SERVERS[@]}"; do _expected+="${HMX_REGISTER_AS[$n]} "; done
    if HMX_EXPECTED="${_expected}" HMX_CFG="${HERMES_HOME}/config.yaml" python3 - <<'PY' 2>/dev/null; then
import os, sys, yaml
cfg = yaml.safe_load(open(os.environ["HMX_CFG"])) or {}
m = cfg.get("mcp_servers") or {}
want = os.environ["HMX_EXPECTED"].split()
sys.exit(0 if all(w in m for w in want) else 1)
PY
      c_ok "all manifest servers already registered in config.yaml"
    else
      c_warn "some servers not yet registered — would run register-mcp.sh"; MISSING=$((MISSING + 1))
    fi
  else
    c_warn "no hermes config — registration would be skipped"
  fi
else
  if command -v hermes >/dev/null 2>&1 && [ -f "${HERMES_HOME}/config.yaml" ]; then
    bash "${REPO_ROOT}/scripts/register-mcp.sh" || c_bad "register-mcp.sh returned non-zero"
    if [ -f "${REPO_ROOT}/scripts/apply-config-deadlines.sh" ]; then
      bash "${REPO_ROOT}/scripts/apply-config-deadlines.sh" || c_warn "apply-config-deadlines.sh returned non-zero"
    fi
  else
    c_warn "hermes/config not found — skipping registration (servers still runnable standalone)"
  fi
fi

# ── 6. summary ────────────────────────────────────────────────────────────────
hdr "summary"
if [ "${CHECK}" -eq 1 ]; then
  if [ "${MISSING}" -eq 0 ]; then
    c_ok "audit clean — bootstrap would make no changes"
    exit 0
  fi
  c_warn "${MISSING} item(s) need setup — run:  bash bootstrap.sh"
  exit 1
fi

echo "Discovered servers: ${#_server_dirs[@]}   manifest servers: ${#HMX_SERVERS[@]}"
if [ "${#SMOKE_FAIL[@]}" -gt 0 ]; then
  c_bad "smoke failures: ${SMOKE_FAIL[*]}"
fi
echo
echo "Now bring the stack up and (re)start Hermes:"
echo "    bash ${REPO_ROOT}/scripts/start-all.sh"
echo "    hermes        # restart so it loads the new mcp_servers + skills"
echo
echo "Healthcheck any time:  bash ${REPO_ROOT}/scripts/healthcheck.sh"
[ "${#SMOKE_FAIL[@]}" -eq 0 ] && exit 0 || exit 1
