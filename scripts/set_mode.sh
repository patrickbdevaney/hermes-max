#!/usr/bin/env bash
# set_mode.sh — Option A: swap the Hermes AGENT-LOOP backend to a posture's executor.
#
# Atomically rewrites the `model:` block of ~/.hermes/config.yaml to the executor
# backend that lib/inference resolves for the given posture mode (the first present,
# under-ceiling rung of `code_execute`). This is the model the Hermes loop ITSELF
# runs on — distinct from the conductor's per-role routing.
#
#   • local-executor postures (free / full-local / frontier-local / local) →
#     local vLLM ($VLLM_BASE_URL, no key).
#   • remote-executor postures (full / frontier) → DeepSeek-V4-Flash via the funded
#     DeepInfra endpoint, with the key resolved from env/.env.
#
# Safety: backs up to config.yaml.bak before every write; captures the ORIGINAL
# model block once to config.model.orig.yaml; only the `model:` block is mutated
# (everything else round-trips untouched). Reversible — `hm mode free` rewrites the
# loop back to local. Override the target path with HERMES_CONFIG (used by tests).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"
[ -z "${MODE}" ] && { echo "usage: set_mode.sh <mode>"; exit 2; }
HERMES_CONFIG="${HERMES_CONFIG:-${HOME}/.hermes/config.yaml}"
ENV_FILE="${HMX_ENV_FILE:-${REPO_ROOT}/.env}"
ORIG="$(dirname "${HERMES_CONFIG}")/config.model.orig.yaml"

if [ ! -f "${HERMES_CONFIG}" ]; then
  echo "  • ${HERMES_CONFIG} absent — skipping Hermes backend swap (config-only run)"
  exit 0
fi

# Resolve the executor backend (shell-eval-able vars; the secret is NOT among them).
eval "$( cd "${REPO_ROOT}" && python3 -m lib.inference.modes_cli executor "${MODE}" 2>/dev/null )"
if [ -z "${HERMES_EXEC_PROVIDER:-}" ]; then
  echo "  • could not resolve executor backend for '${MODE}' — Hermes config untouched"
  exit 0
fi

# Resolve the api key VALUE from the live env or .env (never echoed).
_resolve_key() {
  local name="$1"; [ -z "${name}" ] && return 0
  if [ -n "${!name:-}" ]; then printf '%s' "${!name}"; return 0; fi
  grep -E "^${name}=" "${ENV_FILE}" 2>/dev/null | head -1 | sed "s/^${name}=//; s/[[:space:]]*#.*$//"
}
APIKEY=""
if [ "${HERMES_EXEC_LOCAL}" != "1" ] && [ -n "${HERMES_EXEC_API_KEY_ENV}" ]; then
  APIKEY="$(_resolve_key "${HERMES_EXEC_API_KEY_ENV}")"
fi

# Back up + capture the original model block ONCE (so the very first state is always
# recoverable, even after several swaps).
cp -p "${HERMES_CONFIG}" "${HERMES_CONFIG}.bak" 2>/dev/null || true
if [ ! -f "${ORIG}" ]; then
  ( cd "${REPO_ROOT}" && HC="${HERMES_CONFIG}" OUT="${ORIG}" python3 - <<'PY' 2>/dev/null
import os, yaml
c = yaml.safe_load(open(os.environ["HC"])) or {}
yaml.safe_dump(c.get("model", {}), open(os.environ["OUT"], "w"), sort_keys=False)
PY
  ) || true
fi

# Write the model block (yaml round-trip; mutate ONLY `model`, atomic replace).
HERMES_CONFIG="${HERMES_CONFIG}" MODEL_ID="${HERMES_EXEC_MODEL_ID}" \
BASE_URL="${HERMES_EXEC_BASE_URL}" APIKEY="${APIKEY}" LOCAL="${HERMES_EXEC_LOCAL}" \
python3 - <<'PY'
import os, yaml
path = os.environ["HERMES_CONFIG"]
c = yaml.safe_load(open(path)) or {}
m = dict(c.get("model") or {})
m["default"] = os.environ["MODEL_ID"]
m["provider"] = "custom"
m["base_url"] = os.environ["BASE_URL"]
m["api_mode"] = "chat_completions"
if os.environ.get("LOCAL") == "1":
    m.pop("api_key", None)                       # local needs no key
else:
    key = os.environ.get("APIKEY", "")
    if key:
        m["api_key"] = key
    else:
        m.pop("api_key", None)                   # absent key → leave unset (auth will fail loudly)
c["model"] = m
tmp = path + ".tmp"
yaml.safe_dump(c, open(tmp, "w"), default_flow_style=False, sort_keys=False)
os.replace(tmp, path)
PY

if [ "${HERMES_EXEC_LOCAL}" = "1" ]; then
  echo "  ▸ Hermes loop → LOCAL  ${HERMES_EXEC_MODEL_ID}  @ ${HERMES_EXEC_BASE_URL}  (no key)"
else
  _key_note="(⚠ no key for ${HERMES_EXEC_API_KEY_ENV} — auth will fail)"
  [ -n "${APIKEY}" ] && _key_note="(key from ${HERMES_EXEC_API_KEY_ENV})"
  echo "  ▸ Hermes loop → ${HERMES_EXEC_MODEL_ID}  @ ${HERMES_EXEC_BASE_URL}  ${_key_note}"
fi
echo "    backup: ${HERMES_CONFIG}.bak   ·   original model block: ${ORIG}"
