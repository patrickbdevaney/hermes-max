#!/usr/bin/env bash
# Register hermes-max with the live Hermes install via NATIVE surfaces only:
#   1. inject the five MCP servers into ~/.hermes/config.yaml (mcp_servers:)
#   2. install the Tier-2 workflow skills into ~/.hermes/skills/hermes-max/
#   3. (optional) --sync-model-url: point model.base_url at $VLLM_BASE_URL
#
# Idempotent. Backs up config.yaml before editing. Never touches Hermes source.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG="${HERMES_HOME}/config.yaml"
SYNC_MODEL_URL=0
[ "${1:-}" = "--sync-model-url" ] && SYNC_MODEL_URL=1

if [ ! -f "${CONFIG}" ]; then
  echo "ERROR: ${CONFIG} not found — is Hermes installed?" >&2
  exit 1
fi

# Export resolved values for the python step.
export HMX_HOST="$(hmx_bind_host)"
export HMX_VERIFY_PORT="$(hmx_port verify)"
export HMX_RAG_PORT="$(hmx_port rag)"
export HMX_KG_PORT="$(hmx_port kg)"
export HMX_OBS_PORT="$(hmx_port observability)"
export HMX_ESC_PORT="$(hmx_port escalation)"
export HMX_CHECKPOINT_PORT="$(hmx_port checkpoint)"
export HMX_WATCHDOG_PORT="$(hmx_port watchdog)"
export HMX_SEARCH_PORT="$(hmx_port search)"
export HMX_CONFIG="${CONFIG}"
export HMX_SYNC_MODEL_URL="${SYNC_MODEL_URL}"
export HMX_VLLM_BASE_URL="${VLLM_BASE_URL:-}"

echo "═══ registering hermes-max with Hermes ═══"

# ── 1. inject mcp_servers ─────────────────────────────────────────────────────
python3 - <<'PY'
import os, shutil, datetime, yaml

cfg_path = os.environ["HMX_CONFIG"]
host = os.environ["HMX_HOST"]

with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}

backup = f"{cfg_path}.hermes-max.bak.{datetime.datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(cfg_path, backup)
print(f"  backup: {backup}")

servers = {
    "hermes-max-verify":          os.environ["HMX_VERIFY_PORT"],
    "hermes-max-codebase-rag":    os.environ["HMX_RAG_PORT"],
    "hermes-max-knowledge-graph": os.environ["HMX_KG_PORT"],
    "hermes-max-observability":   os.environ["HMX_OBS_PORT"],
    "hermes-max-escalation":      os.environ["HMX_ESC_PORT"],
    "hermes-max-checkpoint":      os.environ["HMX_CHECKPOINT_PORT"],
    "hermes-max-watchdog":        os.environ["HMX_WATCHDOG_PORT"],
    "hermes-max-search":          os.environ["HMX_SEARCH_PORT"],
}

mcp = cfg.setdefault("mcp_servers", {})
for name, port in servers.items():
    mcp[name] = {"url": f"http://{host}:{port}/mcp", "enabled": True, "timeout": 120}
    print(f"  + {name} -> http://{host}:{port}/mcp")

if os.environ.get("HMX_SYNC_MODEL_URL") == "1":
    url = os.environ.get("HMX_VLLM_BASE_URL", "")
    if url:
        if isinstance(cfg.get("model"), dict):
            cfg["model"]["base_url"] = url
        for prov in cfg.get("custom_providers", []) or []:
            if isinstance(prov, dict):
                prov["base_url"] = url
        print(f"  model.base_url synced to {url}")
    else:
        print("  --sync-model-url given but VLLM_BASE_URL is empty; skipped")

with open(cfg_path, "w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False, width=4096)
print("  config.yaml updated")
PY

# ── 2. install Tier-2 skills ──────────────────────────────────────────────────
SKILL_DEST="${HERMES_HOME}/skills/hermes-max"
mkdir -p "${SKILL_DEST}"
count=0
for d in "${REPO_ROOT}"/skills/*/; do
  [ -f "${d}/SKILL.md" ] || continue
  name="$(basename "${d}")"
  rm -rf "${SKILL_DEST:?}/${name}"
  cp -r "${d%/}" "${SKILL_DEST}/${name}"
  count=$((count + 1))
done
echo "  installed ${count} Tier-2 skills -> ${SKILL_DEST}"

echo
echo "═══ done ═══"
echo "Next:"
echo "  • Start servers:   scripts/start-all.sh"
echo "  • Restart Hermes so it loads the new mcp_servers + skills."
echo "  • Wire DSPy cron:  dspy-evolution/register-cron.sh"
echo "  • Port to your inference host:    set VLLM_BASE_URL=http://localhost:8001/v1 and re-run with --sync-model-url"
