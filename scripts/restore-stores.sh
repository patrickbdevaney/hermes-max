#!/usr/bin/env bash
# restore-stores.sh <name> — swap a named snapshot back into the active stores.
#
# Backs up the CURRENT state first (an auto `_pre-restore-<ts>` snapshot) so a
# restore is itself reversible, then replaces the active RAG index + KG db + corpus
# with the snapshot's. Default no-snapshot behaviour (permanent compounding) is
# unchanged — this only runs when you explicitly restore.
#
#   restore-stores.sh <name>
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

NAME="${1:-}"
if [ -z "${NAME}" ] || [ "${NAME}" = "-h" ] || [ "${NAME}" = "--help" ]; then
  echo "usage: restore-stores.sh <name>   (see list-snapshots.sh)"; exit 2
fi
SRC="$(hmx_snap_root)/${NAME}"
if [ ! -d "${SRC}" ]; then
  echo "✗ no snapshot '${NAME}' at ${SRC}"; echo "  available:"; "${SCRIPT_DIR}/list-snapshots.sh" 2>/dev/null
  exit 1
fi

# Swapping a SQLite file under a live server is unsafe — warn if rag/kg are up.
for s in rag kg; do
  if curl -fsS -m 2 "$(hmx_health_url "${s}")" >/dev/null 2>&1; then
    echo "  ⚠ mcp-${s} is RUNNING — stop it first (scripts/stop-all.sh or restart.sh ${s} after)."
  fi
done

# 1. reversible: back up the current state before overwriting.
PRE="_pre-restore-$(date -u +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
echo "── backing up current state → snapshot '${PRE}' (restore is reversible) ──"
"${SCRIPT_DIR}/snapshot-stores.sh" "${PRE}" >/dev/null 2>&1 || \
  echo "  ⚠ pre-restore backup hit a warning (continuing)"

RAG="$(hmx_rag_path)"; KG="$(hmx_kg_path)"; CORPUS="$(hmx_corpus_dir)"

restore_sqlite() {  # snap-subdir, active-db-path, kind
  local sub="$1" active="$2" kind="$3" base; base="$(basename "${active}")"
  if [ -f "${SRC}/${sub}/${base}" ]; then
    mkdir -p "$(dirname "${active}")"
    rm -f "${active}" "${active}-wal" "${active}-shm"   # drop stale sidecars
    cp -p "${SRC}/${sub}/${base}" "${active}"
    [ -f "${SRC}/${sub}/${base}-wal" ] && cp -p "${SRC}/${sub}/${base}-wal" "${active}-wal"
    [ -f "${SRC}/${sub}/${base}-shm" ] && cp -p "${SRC}/${sub}/${base}-shm" "${active}-shm"
    echo "  ✓ ${kind} restored → ${active}"
  else
    # snapshot captured an absent store -> make the active one absent too
    rm -f "${active}" "${active}-wal" "${active}-shm"
    echo "  ✓ ${kind} restored → absent (snapshot had none)"
  fi
}

echo "── restoring snapshot '${NAME}' ──"
restore_sqlite rag "${RAG}" "rag index"
restore_sqlite kg  "${KG}"  "kg db"
# corpus dir: replace wholesale
if [ -d "${SRC}/corpus" ]; then
  rm -rf "${CORPUS}"; mkdir -p "${CORPUS}"
  cp -a "${SRC}/corpus/." "${CORPUS}/" 2>/dev/null || true
  echo "  ✓ corpus restored → ${CORPUS} ($(find "${CORPUS}" -type f 2>/dev/null | wc -l | tr -d ' ') files)"
else
  rm -rf "${CORPUS}"; echo "  ✓ corpus restored → empty (snapshot had none)"
fi

echo "── restored '${NAME}'. Previous state saved as '${PRE}' (restore-stores.sh ${PRE} to undo). ──"
echo "   If a server was running, restart it: scripts/restart.sh rag ; scripts/restart.sh kg"
