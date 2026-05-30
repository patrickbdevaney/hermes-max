#!/usr/bin/env bash
# snapshot-stores.sh <name> — capture the RAG + KG + corpus stores (Stage 6).
#
# The RAG/KG/corpus stores are PERMANENT and COMPOUNDING by default (long-term
# accumulated knowledge — that is unchanged). This captures the CURRENT state to
# ~/.hermes-max/snapshots/<name>/ with a timestamp + a manifest, so you can isolate
# a test session: snapshot baseline → run an eval → inspect the compounded stores →
# keep (compounding continues) or restore the baseline (clean slate). Default
# behaviour with no snapshot calls = permanent compounding, untouched.
#
#   snapshot-stores.sh <name> [--force]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

NAME="${1:-}"
FORCE="${2:-}"
if [ -z "${NAME}" ] || [ "${NAME}" = "-h" ] || [ "${NAME}" = "--help" ]; then
  echo "usage: snapshot-stores.sh <name> [--force]"; exit 2
fi
case "${NAME}" in *[!A-Za-z0-9._-]*)
  echo "✗ name must be alphanumeric / . _ - only"; exit 2 ;;
esac

DEST="$(hmx_snap_root)/${NAME}"
if [ -e "${DEST}" ] && [ "${FORCE}" != "--force" ]; then
  echo "✗ snapshot '${NAME}' already exists at ${DEST} (use --force to overwrite)"; exit 1
fi
rm -rf "${DEST}"; mkdir -p "${DEST}"

RAG="$(hmx_rag_path)"; KG="$(hmx_kg_path)"; CORPUS="$(hmx_corpus_dir)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
MAN="${DEST}/MANIFEST.txt"
{
  echo "snapshot: ${NAME}"
  echo "created:  ${TS}"
  echo "host:     $(hostname 2>/dev/null || echo '?')"
  echo "── captured stores ──"
} >"${MAN}"

cap() {  # kind, source, copy-cmd-result-note
  local kind="$1" src="$2" note="$3"
  echo "${kind}: ${src} (${note})" >>"${MAN}"
  echo "  ✓ ${kind}: ${note}"
}

# RAG index (+ wal/shm)
if [ -f "${RAG}" ]; then
  hmx_copy_sqlite "${RAG}" "${DEST}/rag"
  cap "rag" "${RAG}" "$(du -h "${RAG}" | cut -f1)"
else
  cap "rag" "${RAG}" "absent (empty/never indexed)"
fi
# KG db (+ wal/shm)
if [ -f "${KG}" ]; then
  hmx_copy_sqlite "${KG}" "${DEST}/kg"
  cap "kg" "${KG}" "$(du -h "${KG}" | cut -f1)"
else
  cap "kg" "${KG}" "absent (empty)"
fi
# Corpus dir (markdown tree)
if [ -d "${CORPUS}" ]; then
  mkdir -p "${DEST}/corpus"
  cp -a "${CORPUS}/." "${DEST}/corpus/" 2>/dev/null || true
  n="$(find "${CORPUS}" -type f 2>/dev/null | wc -l | tr -d ' ')"
  cap "corpus" "${CORPUS}" "${n} files, $(du -sh "${CORPUS}" 2>/dev/null | cut -f1)"
else
  cap "corpus" "${CORPUS}" "absent (no research corpus yet)"
fi

echo "total: $(du -sh "${DEST}" 2>/dev/null | cut -f1)" >>"${MAN}"
echo "── snapshot '${NAME}' written to ${DEST} ($(du -sh "${DEST}" 2>/dev/null | cut -f1)) ──"
