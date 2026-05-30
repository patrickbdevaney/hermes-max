#!/usr/bin/env bash
# Run every MCP server's STANDALONE smoke test (build -> test isolated).
# Each server is tested alone, on a throwaway port/DB, with no dependency on the
# others — if one fails, the rest still run and the overall exit code is non-zero.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

echo "═══ hermes-max standalone smoke tests ═══"
echo "DEPLOY_PROFILE=${HMX_PROFILE}  (active servers: ${HMX_ACTIVE_SERVERS[*]})"
declare -a FAILED=()
for name in "${HMX_ACTIVE_SERVERS[@]}"; do
  dir="${HMX_DIR[$name]}"
  echo
  echo "── ${dir} ──"
  hmx_ensure_venv "${dir}"
  if "${REPO_ROOT}/${dir}/.venv/bin/python" "${REPO_ROOT}/${dir}/smoke_test.py"; then
    echo "  ✓ ${dir} PASSED"
  else
    echo "  ✗ ${dir} FAILED"
    FAILED+=("${dir}")
  fi
done

echo
echo "── dspy-evolution wrapper (graceful-skip check) ──"
if bash "${REPO_ROOT}/dspy-evolution/run-evolution.sh" >/dev/null 2>&1; then
  echo "  ✓ dspy-evolution wrapper runs and exits 0"
else
  echo "  ✗ dspy-evolution wrapper returned non-zero"
  FAILED+=("dspy-evolution")
fi

echo
if [ "${#FAILED[@]}" -eq 0 ]; then
  echo "═══ ALL SMOKE TESTS PASSED ═══"
  exit 0
fi
echo "═══ FAILURES: ${FAILED[*]} ═══"
exit 1
