#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stage-0 conductor eval — rank synth/draft candidates on the operator's OWN work.
#
# Sends real synthesis briefs (public architecture questions about THIS harness)
# to every candidate whose API key is present, and:
#   • CONFIRMS the operator's LOCKED DeepInfra defaults (synth=V4-Pro, steer=
#     V4-Flash) emit valid structured directives at the expected cost — a smoke,
#     not a bake-off to replace them.
#   • RANKS the FREE cross-family draft pool (Cerebras/Groq) — the genuinely open
#     question that parallel_draft (Stage 4) selects from.
#
# Presence-gated: a candidate runs only if its *_API_KEY is set; needs >=2. Free
# candidates cost $0; the paid anchor (DeepInfra) costs cents. Nothing sensitive
# is sent. Uses the escalation venv (httpx) if present, else python3.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

PY="${REPO_ROOT}/mcp-escalation/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

# Presence preflight (informational skip, not a failure, if nothing is set).
present=0
for k in DEEPINFRA_API_KEY CEREBRAS_API_KEY GROQ_API_KEY; do
  [ -n "${!k:-}" ] && present=$((present + 1))
done
if [ "${present}" -lt 2 ]; then
  echo "• Fewer than 2 conductor candidate keys set — Stage-0 eval needs >=2."
  echo "  Set DEEPINFRA_API_KEY (paid) + CEREBRAS_API_KEY/GROQ_API_KEY (free) in .env. SKIPPING."
  exit 0
fi

exec "${PY}" "${SCRIPT_DIR}/eval_synthesis.py"
