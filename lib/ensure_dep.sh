#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Shared lazy-install guard for hermes-max scripts. SOURCE this, then call:
#
#     hmx_ensure_dep <python-bin> <import-name> [pip-spec]
#
# Ensures <import-name> is importable by <python-bin>; if not, pip-installs
# <pip-spec> (defaults to <import-name>) into THAT interpreter's environment and
# retries the import. Mirrors Hermes's own lazy-install behaviour (e.g. `ddgs`)
# so a partially set-up venv self-heals on first use instead of crashing.
#
# Idempotent. Returns 0 if the dep is (now) present, non-zero with a clear
# warning if it could not be installed (callers degrade gracefully — they never
# hard-fail the agent just because an OPTIONAL dep is missing).
# ─────────────────────────────────────────────────────────────────────────────
hmx_ensure_dep() {
  local py="$1" imp="$2" spec="${3:-$2}"
  if [ -z "${py}" ] || [ ! -x "${py}" ]; then
    echo "  [ensure_dep] no python at '${py}'" >&2
    return 2
  fi
  if "${py}" -c "import ${imp}" >/dev/null 2>&1; then
    return 0
  fi
  echo "  [ensure_dep] '${imp}' missing — installing '${spec}' into $(${py} -c 'import sys;print(sys.prefix)')" >&2
  if "${py}" -m pip install -q "${spec}" >/dev/null 2>&1 \
     && "${py}" -c "import ${imp}" >/dev/null 2>&1; then
    echo "  [ensure_dep] '${imp}' OK" >&2
    return 0
  fi
  echo "  [ensure_dep] WARNING: could not provide '${imp}' (${spec}) for ${py} — continuing degraded" >&2
  return 1
}
