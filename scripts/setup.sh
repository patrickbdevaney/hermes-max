#!/usr/bin/env bash
# setup.sh — first-run wiring for the inference fabric.
#
# Copies the shipped config trinity into ~/.hermes-max on first run so the user can
# edit their own copy (the loaders fall back to the repo files if these are absent,
# so this is purely to give the user an editable, persistent config). Idempotent:
# an existing file is never overwritten. Then prints which providers are present and
# how to turn on the rest. Run standalone, or via `hm up --setup`.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_DIR="${HERMES_MAX_STATE_DIR:-${HOME}/.hermes-max}"
mkdir -p "${CFG_DIR}"

echo "═══ hermes-max inference fabric setup ═══"
echo "config dir: ${CFG_DIR}"

# 1. config trinity — copy the shipped defaults only if the user has no copy yet.
_copy_if_absent() {
  local src="$1" dst="$2" label="$3"
  if [ -f "${dst}" ]; then
    echo "  • ${label}: already present (${dst}) — left untouched"
  elif [ -f "${src}" ]; then
    cp "${src}" "${dst}"
    echo "  ✓ ${label}: copied default → ${dst} (edit this, not the code)"
  fi
}
_copy_if_absent "${REPO_ROOT}/config/inference.example.yaml" "${CFG_DIR}/inference.yaml" "inference.yaml (backends)"
_copy_if_absent "${REPO_ROOT}/config/roles.yaml"             "${CFG_DIR}/roles.yaml"     "roles.yaml (role→chain)"
_copy_if_absent "${REPO_ROOT}/config/modes.yaml"             "${CFG_DIR}/modes.yaml"     "modes.yaml (posture presets)"

# 2. .env from the example, if the user has none yet (keys go here).
if [ ! -f "${REPO_ROOT}/.env" ] && [ -f "${REPO_ROOT}/.env.example" ]; then
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  echo "  ✓ .env: created from .env.example — put your API keys here"
fi

# 3. provider presence + how to enable the rest (a missing key = that rung silently
#    drops; with nothing but local_vllm the system is fully local and free).
echo
echo "Providers (a missing key silently drops that rung — never an error):"
( cd "${REPO_ROOT}" && python3 -m lib.inference.modes_cli providers ) 2>/dev/null || \
  echo "  (install pyyaml + httpx to enable the fabric: pip install pyyaml httpx)"

# 4. the posture + the spend ceiling.
echo
echo "Active posture:"
( cd "${REPO_ROOT}" && python3 -m lib.inference.modes_cli show ) 2>/dev/null | sed 's/^/  /' | head -4
cat <<'EOF'

Next steps:
  • Put the keys you have in .env (none required — zero keys = fully local & free).
  • Pick a posture:   hm mode --list     then   hm mode <name>     (default: free)
  • Start the stack:  hm up              (keeps your posture; never escalates to paid)
  • See spend:        hm cost
EOF
