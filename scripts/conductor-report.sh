#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Honest conductor invocation-frequency + cost report (Stage 5).
#
# Reads the conductor cost ledger (per-day/month, by provider + role) and the KG
# conductor_event tier counts, and checks them against the per-project targets
# (synthesize <= ~15, Opus escalate <= ~3). A breach means the brief-assembler
# quality is the bottleneck — fix the assembler, don't spend more on the frontier.
#
# Pure-local; no cloud calls. Degrades cleanly if the KG isn't running (counts
# unavailable, spend still reported from the local ledger file).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

PY="${REPO_ROOT}/mcp-escalation/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

cd "${REPO_ROOT}/mcp-escalation"
exec "${PY}" - <<'PY'
import json
import conductor_policy as cp

rep = cp.frequency_report()
s = rep["spend"]
print("═══ conductor report — invocation frequency + cost ═══\n")
print(f"  spend today:  ${s['today_usd']:.5f}")
print(f"  spend month:  ${s['month_usd']:.5f}")
print(f"  by provider:  {json.dumps(s['by_provider']) or '{}'}")
print(f"  by role:      {json.dumps(s['by_role']) or '{}'}")
print()
print(f"  tier counts (KG{'' if rep['kg_available'] else ' UNAVAILABLE — start mcp-knowledge-graph'}):")
counts = rep["tier_counts"] or {}
for tier in ("local", "parallel_draft", "steer", "synthesize", "escalate"):
    print(f"    {tier:<16} {counts.get(tier, 0)}")
t = rep["targets"]
print(f"\n  targets: synthesize <= {t['synthesize']}/project, Opus escalate <= {t['escalate_opus']}/project")
print("  status:")
for w in rep["warnings"]:
    print(f"    • {w}")
print("\n  NOTE: the design intent is that routine subtasks stay LOCAL ($0); verifiable-hard")
print("  uses free parallel_draft; ambiguous-hard a cheap steer then synth; Opus is the rare")
print("  exception. Realistic heavy month ~ $3-8 (Opus dominates despite its rarity).")
PY
