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
import frontier_core as fc

rep = cp.frequency_report()
s = rep["spend"]
fst = fc.frontier_status()
byp = s["by_provider"] or {}
deepseek_usd = round(byp.get("deepinfra", 0.0) + byp.get("deepseek", 0.0), 5)
opus_usd = round(byp.get("anthropic", 0.0), 5)
calls, tgt, capm = fst["calls_month"], fst["target_calls_monthly"], fst["cap_monthly_usd"]

print("═══ hm cost — conductor spend + Opus sparing proof ═══\n")
print(f"  active mode:  {fst['mode']}   (cloud-tier ceiling; `hm up --MODE` to change)")
print(f"  spend today:  ${s['today_usd']:.5f}")
print(f"  spend month:  ${s['month_usd']:.5f}   (all cloud tiers, this month)")
print(f"  by provider:  {json.dumps(s['by_provider']) or '{}'}")
print(f"  by role:      {json.dumps(s['by_role']) or '{}'}")

print("\n  ── tiers (month-to-date) ──")
print("    local (vLLM)         $0.00       all local tokens are free (+ electricity)")
print("    free draft/steer     $0.00       Cerebras/Groq/Gemini (free tier)")
print(f"    DeepSeek synth/steer ${deepseek_usd:<11} the cheap lean frontier synthesis")
print(f"    Opus 4.8 escalate    ${opus_usd:<11} {calls} call(s) this month")

print("\n  ── frontier (Opus 4.8) sparing ──")
print(f"    eligible:    {fst['frontier_eligible']}  (needs mode=frontier + ANTHROPIC_API_KEY)")
print(f"    Opus calls:  {calls}/{tgt} this month   (sparing target ≤ {tgt}/mo)")
print(f"    Opus spend:  ${fst['spend_month_usd']:.4f}/${capm} mo   "
      f"${fst['spend_today_usd']:.4f}/${fst['cap_daily_usd']} day")

print("\n  ── the Pareto (this month) ──")
print(f"    frontier-mode total: ~${round(s['month_usd'], 2):.2f}/mo cloud  +  ~$10/mo base "
      f"(DeepSeek+electricity)   vs   Claude Code $20/mo")
print("    → ~all tokens run LOCALLY; cloud is the rare exception, Opus the rarest.")

print("\n  status:")
if calls > tgt:
    print(f"    ⚠ Opus calls {calls} EXCEED the sparing target {tgt} — frontier use is DRIFTING")
    print("      toward defeating the Pareto. TIGHTEN the difficulty gate (raise the frontier-novel")
    print("      threshold): frequent Opus means the classifier is mis-flagging merely-HARD as")
    print("      frontier-novel — OR the work is genuinely blue-ocean, in which case Claude Code's")
    print("      flat $20/mo may be the better tool for it. (Reported honestly, not hidden.)")
elif fst.get("cap_blocked"):
    print(f"    ⚠ {fst['cap_blocked']} — Opus blocked; escalations fall back to V4-Pro synth.")
else:
    print(f"    ✓ Opus is sparing ({calls}/{tgt} calls) — the affordability-performance Pareto holds.")
print(f"\n  tier counts (KG{'' if rep['kg_available'] else ' UNAVAILABLE — start mcp-knowledge-graph'}):")
counts = rep["tier_counts"] or {}
for tier in ("local", "parallel_draft", "steer", "synthesize", "escalate"):
    print(f"    {tier:<16} {counts.get(tier, 0)}")
for w in rep["warnings"]:
    print(f"    • {w}")

print("\n  NOTE: design intent — routine subtasks stay LOCAL ($0); verifiable-hard uses free")
print("  parallel_draft; ambiguous-hard a cheap steer→synth; Opus 4.8 is the THREE-gated rare")
print("  exception (compress-then-reason, ~$0.18/call). The system wins the Pareto WHILE Opus")
print("  stays rare; if your work is predominantly frontier-novel, Claude Code may fit better.")
PY
