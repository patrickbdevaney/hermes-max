#!/usr/bin/env bash
# setup-serena.sh — provision the Serena LSP engine for mcp-lsp (M-Stage 1).
# Clones oraios/serena into vendor/ and installs it in its own venv. Idempotent:
# skips the clone/install if already present. vendor/ is gitignored (3rd-party repo);
# this script makes the mcp-lsp backend reproducible on a fresh checkout.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERENA_DIR="${REPO_ROOT}/vendor/serena"
mkdir -p "${REPO_ROOT}/vendor"
if [ ! -d "${SERENA_DIR}/.git" ]; then
  echo "• cloning oraios/serena …"
  git clone --depth 1 https://github.com/oraios/serena "${SERENA_DIR}"
else
  echo "• serena already cloned (${SERENA_DIR})"
fi
if [ ! -x "${SERENA_DIR}/.venv/bin/serena" ]; then
  echo "• creating serena venv + installing (heavy; bundles language-server tooling) …"
  python3 -m venv "${SERENA_DIR}/.venv"
  "${SERENA_DIR}/.venv/bin/pip" install -q --upgrade pip
  "${SERENA_DIR}/.venv/bin/pip" install -q "${SERENA_DIR}"
else
  echo "• serena already installed"
fi
echo "✓ serena ready: ${SERENA_DIR}/.venv/bin/serena (mcp-lsp launches it on \$SERENA_BACKEND_PORT)"
