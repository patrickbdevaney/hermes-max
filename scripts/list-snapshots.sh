#!/usr/bin/env bash
# list-snapshots.sh — list store snapshots with timestamps + sizes (Stage 6).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

ROOT="$(hmx_snap_root)"
if [ ! -d "${ROOT}" ] || [ -z "$(ls -A "${ROOT}" 2>/dev/null)" ]; then
  echo "no snapshots yet (root: ${ROOT})"
  echo "create one: scripts/snapshot-stores.sh <name>"
  exit 0
fi

echo "═══ store snapshots (${ROOT}) ═══"
printf '%-28s %-22s %-8s %s\n' "name" "created" "size" "captured"
printf '%s\n' "$(printf '─%.0s' $(seq 1 78))"
for d in "${ROOT}"/*/; do
  [ -d "${d}" ] || continue
  name="$(basename "${d}")"
  man="${d}MANIFEST.txt"
  created="$(grep -m1 '^created:' "${man}" 2>/dev/null | sed 's/created: *//')"
  [ -z "${created}" ] && created="$(date -r "${d}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo '?')"
  size="$(du -sh "${d}" 2>/dev/null | cut -f1)"
  captured="$(grep -E '^(rag|kg|corpus):' "${man}" 2>/dev/null | sed -E 's/:.*\((.*)\)/=\1/; s/:.*//' | tr '\n' ' ')"
  printf '%-28s %-22s %-8s %s\n' "${name}" "${created}" "${size:-?}" "${captured}"
done
