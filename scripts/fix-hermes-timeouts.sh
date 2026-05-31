#!/usr/bin/env bash
# Stage 1 (long-horizon) — fix the PRIMARY long-run killer: Hermes' own MCP call
# timeout.
#
# Every mcp_servers.* entry in ~/.hermes/config.yaml ships with `timeout: 120` —
# Hermes kills the HTTP call to the MCP server after 120s REGARDLESS of what the
# watchdog or the MCP server itself does. deep_research legitimately runs 300-900s,
# so the 120s MCP timeout severed it at the two-minute mark, every time. This is the
# single biggest blocker to a multi-hour long-horizon run.
#
# This script raises ONLY the hermes-max-* MCP call timeouts to match each server's
# real worst-case wall-clock:
#   • hermes-max-research      -> 900  (deep_research: multi-loop source synthesis)
#   • hermes-max-docs          -> 300  (research_topic: ingest + distill inference)
#   • hermes-max-codebase-rag  -> 300  (index_repo on a large tree)
#   • all other hermes-max-*   -> 180  (headroom over the 120s default)
#
# It deliberately does NOT touch terminal.timeout, browser.inactivity_timeout,
# vision.timeout, compression.timeout or triage_specifier.timeout — those are
# unrelated subsystems with their own correct values (see apply-config-deadlines.sh,
# which owns terminal.timeout).
#
# Idempotent. Backs up config.yaml (timestamped) before editing and prints a diff.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG="${HERMES_HOME}/config.yaml"

if [ ! -f "${CONFIG}" ]; then
  echo "ERROR: ${CONFIG} not found — is Hermes installed?" >&2
  exit 1
fi

# The desired per-server MCP call timeouts (seconds). Override the defaults via env
# if a host needs more headroom; the script clamps UP only (never lowers a value
# that is already at least the target).
export HMX_CONFIG="${CONFIG}"
export HMX_T_RESEARCH="${HERMES_MCP_TIMEOUT_RESEARCH:-900}"
export HMX_T_DOCS="${HERMES_MCP_TIMEOUT_DOCS:-300}"
export HMX_T_RAG="${HERMES_MCP_TIMEOUT_RAG:-300}"
export HMX_T_OTHER="${HERMES_MCP_TIMEOUT_OTHER:-180}"

echo "═══ fixing Hermes MCP call timeouts (the 120s long-run killer) ═══"
python3 - <<'PY'
import os, shutil, datetime, yaml

cfg_path = os.environ["HMX_CONFIG"]
T_RESEARCH = int(os.environ["HMX_T_RESEARCH"])
T_DOCS     = int(os.environ["HMX_T_DOCS"])
T_RAG      = int(os.environ["HMX_T_RAG"])
T_OTHER    = int(os.environ["HMX_T_OTHER"])

# Per-server target timeout. Anything not listed (but still hermes-max-*) gets
# T_OTHER. Non-hermes-max servers are left entirely alone.
TARGETS = {
    "hermes-max-research":     T_RESEARCH,
    "hermes-max-docs":         T_DOCS,
    "hermes-max-codebase-rag": T_RAG,
}

with open(cfg_path) as f:
    before_text = f.read()
cfg = yaml.safe_load(before_text) or {}

servers = cfg.get("mcp_servers") or {}
if not isinstance(servers, dict) or not servers:
    print("  no mcp_servers section found — nothing to patch")
    raise SystemExit(0)

changes = []
for name, entry in servers.items():
    if not isinstance(entry, dict):
        continue
    if not str(name).startswith("hermes-max-"):
        continue  # leave non-hermes-max servers (if any) untouched
    target = TARGETS.get(name, T_OTHER)
    old = entry.get("timeout")
    # Clamp UP only: never lower a timeout that is already generous.
    if not isinstance(old, (int, float)) or old < target:
        entry["timeout"] = target
        changes.append(f"{name}.timeout: {old} -> {target}")
    else:
        print(f"  {name}.timeout already {old} (>= {target}, no change)")

if not changes:
    print("  no changes needed (all hermes-max-* timeouts already raised)")
    raise SystemExit(0)

backup = f"{cfg_path}.hermes-max-timeouts.bak.{datetime.datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(cfg_path, backup)
print(f"  backup: {backup}")

with open(cfg_path, "w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False, width=4096)

print("  applied: " + "; ".join(changes))

# Print a focused diff of just the mcp_servers timeout lines (the whole-file
# yaml round-trip can reflow unrelated formatting; the operator wants to see the
# meaningful change, not noise).
import difflib
with open(cfg_path) as f:
    after_text = f.read()
def _mcp_block(text):
    out, grab = [], False
    for ln in text.splitlines():
        if ln.startswith("mcp_servers:"):
            grab = True
        elif grab and ln and not ln[0].isspace() and not ln.startswith("mcp_servers"):
            grab = False
        if grab:
            out.append(ln)
    return out
diff = difflib.unified_diff(_mcp_block(before_text), _mcp_block(after_text),
                            fromfile="config.yaml (before)", tofile="config.yaml (after)",
                            lineterm="")
print("  ── mcp_servers diff ──")
for line in diff:
    print("  " + line)
PY

echo
echo "  confirm:"
grep -A3 "hermes-max-research:" "${CONFIG}" | sed 's/^/    /'
echo "═══ done ═══"
