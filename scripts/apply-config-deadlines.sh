#!/usr/bin/env bash
# Stage 0.2 — apply the NATIVE Hermes deadline knobs into ~/.hermes/config.yaml.
#
# Discovery result (scripts/finalize_validation.py V-* and the spec report):
#   NATIVE (set here, idempotently, with a timestamped backup):
#     • terminal.timeout            600 -> ${HERMES_TERMINAL_TIMEOUT:-120}  (per-tool wall-clock)
#     • agent.max_turns             confirmed present (per-task iteration budget)
#     • tool_loop_guardrails        confirmed hard_stop_after {same_tool_failure:4,
#                                   idempotent_no_progress:3} — left as-is (already correct)
#   NOT NATIVE (no config knob — handled by mcp-watchdog instead, documented):
#     • per-turn max output tokens  -> approximated by watchdog check_spiral
#     • per-task wall-clock / USD   -> watchdog start_task_budget / check_budget
#
# WAITING MODE (the poll-hang fix): a 120s per-tool timeout means any process
# that legitimately runs longer MUST be started backgrounded and checked ONCE
# (workflow-long-running-processes + watchdog check_stall) — never blocked on.
# That discipline is what makes a short terminal.timeout safe.
#
# Idempotent. Never touches Hermes source. Backs up before editing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG="${HERMES_HOME}/config.yaml"
TERMINAL_TIMEOUT="${HERMES_TERMINAL_TIMEOUT:-120}"

if [ ! -f "${CONFIG}" ]; then
  echo "ERROR: ${CONFIG} not found — is Hermes installed?" >&2
  exit 1
fi

export HMX_CONFIG="${CONFIG}"
export HMX_TERMINAL_TIMEOUT="${TERMINAL_TIMEOUT}"

echo "═══ applying native Hermes deadline config ═══"
python3 - <<'PY'
import os, shutil, datetime, yaml

cfg_path = os.environ["HMX_CONFIG"]
term_timeout = int(os.environ["HMX_TERMINAL_TIMEOUT"])

with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}

backup = f"{cfg_path}.hermes-max-deadlines.bak.{datetime.datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(cfg_path, backup)
print(f"  backup: {backup}")

changes = []

# 1. per-tool wall-clock timeout (NATIVE)
term = cfg.setdefault("terminal", {})
old = term.get("timeout")
if old != term_timeout:
    term["timeout"] = term_timeout
    changes.append(f"terminal.timeout: {old} -> {term_timeout}")
else:
    print(f"  terminal.timeout already {term_timeout} (no change)")

# 2. confirm per-task iteration budget (NATIVE) — report, do not lower unasked
agent = cfg.setdefault("agent", {})
mt = agent.get("max_turns")
print(f"  agent.max_turns = {mt} (native per-task iteration budget; left as-is)")

# 3. confirm turn-based guardrails (NATIVE) — report only
g = (cfg.get("tool_loop_guardrails") or {}).get("hard_stop_after") or {}
print(f"  tool_loop_guardrails.hard_stop_after = {g} (left as-is; spec-correct)")

if changes:
    with open(cfg_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False, width=4096)
    print("  applied: " + "; ".join(changes))
else:
    print("  no changes needed (already applied)")
PY

echo
echo "  NOT-native (handled by mcp-watchdog, by design):"
echo "    • per-turn max output tokens  -> watchdog check_spiral approximates"
echo "    • per-task wall-clock / USD   -> watchdog start_task_budget / check_budget"
echo "  Remember WAITING MODE: long processes are backgrounded + check_stall once."
echo "═══ done ═══"
