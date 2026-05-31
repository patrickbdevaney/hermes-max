#!/usr/bin/env bash
# Stage 4 (long-horizon) — pre-flight validation. ONE command that validates the
# whole stack before a serious long-horizon task, so a misconfiguration is caught
# in seconds instead of wasting an hour mid-run.
#
# Prints PASS / FAIL / WARN per check. BLOCKING checks (the known long-run killers)
# gate the exit code: exit 0 only if every BLOCKING check passes, else exit 1.
#
# Auto-fix (default ON; disable with --no-fix): applies the safe, idempotent
# remedies — fix-hermes-timeouts.sh, reinstall missing skills, start embed/rerank,
# and bring the MCP stack up — then RE-checks. `hm dev` runs this and blocks on a
# BLOCKING failure; `hm preflight` is the manual entry point.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

# The directory the AGENT will run in (its cwd), which is NOT necessarily the repo.
# hm passes the caller's cwd through; default to it.
AGENT_CWD="${PREFLIGHT_AGENT_CWD:-$PWD}"

AUTO_FIX=1
for a in "$@"; do case "${a}" in
  --no-fix) AUTO_FIX=0 ;;
  --fix)    AUTO_FIX=1 ;;
esac; done

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; D=""; B=""; Z=""; fi

BLOCKING_FAILS=0
NONBLOCK_FAILS=0
WARNS=0

pass()  { printf '  %s✓ PASS%s  %s\n' "${G}" "${Z}" "$1"; }
warn()  { printf '  %s• WARN%s  %s\n' "${Y}" "${Z}" "$1"; WARNS=$((WARNS+1)); }
# fail <BLOCKING|soft> <message>
fail()  {
  local kind="$1"; shift
  if [ "${kind}" = "BLOCKING" ]; then
    printf '  %s✗ FAIL%s  %s%s [BLOCKING]%s\n' "${R}" "${Z}" "$1" "${R}${B}" "${Z}"
    BLOCKING_FAILS=$((BLOCKING_FAILS+1))
  else
    printf '  %s✗ FAIL%s  %s\n' "${R}" "${Z}" "$1"
    NONBLOCK_FAILS=$((NONBLOCK_FAILS+1))
  fi
}
hdr() { printf '\n%s── %s ──%s\n' "${B}" "$1" "${Z}"; }

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG="${HERMES_HOME}/config.yaml"

echo "═══ hermes-max pre-flight ═══  profile=${HMX_PROFILE}  auto-fix=$([ "${AUTO_FIX}" = 1 ] && echo on || echo off)"
echo "${D}agent cwd: ${AGENT_CWD}${Z}"

# ── auto-fix pass (idempotent, safe) — run BEFORE the checks so the checks see the
#    fixed state, then report PASS instead of FAIL-then-fix-then-confusion. ───────
if [ "${AUTO_FIX}" = 1 ]; then
  hdr "auto-fix (idempotent)"
  # 1. Hermes MCP timeouts (the primary killer) — always safe, no-op once applied.
  if [ -f "${SCRIPT_DIR}/fix-hermes-timeouts.sh" ] && [ -f "${CONFIG}" ]; then
    "${SCRIPT_DIR}/fix-hermes-timeouts.sh" >/dev/null 2>&1 \
      && echo "  ${D}• applied fix-hermes-timeouts.sh${Z}" \
      || echo "  ${D}• fix-hermes-timeouts.sh skipped/failed (non-fatal)${Z}"
  fi
  # 2. Reinstall any missing long-horizon skills.
  SKILL_DEST="${HERMES_HOME}/skills/hermes-max"
  if [ -d "${REPO_ROOT}/skills" ]; then
    reinstalled=0
    mkdir -p "${SKILL_DEST}" 2>/dev/null || true
    for d in "${REPO_ROOT}"/skills/*/; do
      [ -f "${d}SKILL.md" ] || continue
      n="$(basename "${d}")"
      if [ ! -f "${SKILL_DEST}/${n}/SKILL.md" ]; then
        rm -rf "${SKILL_DEST:?}/${n}" 2>/dev/null || true
        cp -r "${d%/}" "${SKILL_DEST}/${n}" 2>/dev/null && reinstalled=$((reinstalled+1))
      fi
    done
    [ "${reinstalled}" -gt 0 ] && echo "  ${D}• reinstalled ${reinstalled} missing skill(s)${Z}"
  fi
  # 3. Start embed/rerank + bring the stack up if anything is down.
  if [ "${HMX_PROFILE}" = "gpu_local" ]; then
    [ -z "$(hmx_port_pids "${EMBED_PORT:-8002}")" ] && [ -x "${REPO_ROOT}/serve-embed.sh" ] && \
      { nohup "${REPO_ROOT}/serve-embed.sh" >>"${HMX_LOG_DIR}/embed.log" 2>&1 & echo "  ${D}• started embed serve${Z}"; }
    [ -z "$(hmx_port_pids "${RERANK_PORT:-8003}")" ] && [ -x "${REPO_ROOT}/serve-rerank.sh" ] && \
      { nohup "${REPO_ROOT}/serve-rerank.sh" >>"${HMX_LOG_DIR}/rerank.log" 2>&1 & echo "  ${D}• started rerank serve${Z}"; }
  fi
  if ! "${SCRIPT_DIR}/healthcheck.sh" >/dev/null 2>&1; then
    echo "  ${D}• MCP stack not fully healthy → start-all.sh${Z}"
    "${SCRIPT_DIR}/start-all.sh" >/dev/null 2>&1 || true
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
hdr "inference"
if [ -z "${VLLM_BASE_URL:-}" ]; then
  fail BLOCKING "VLLM_BASE_URL unset — no chat model"
else
  if models_json="$(curl -fsS -m 6 "${VLLM_BASE_URL}/models" 2>/dev/null)"; then
    pass "vLLM reachable at ${VLLM_BASE_URL}"
    mml="$(printf '%s' "${models_json}" | python3 -c \
      'import json,sys; d=json.load(sys.stdin).get("data") or [{}]; print(d[0].get("max_model_len") or 0)' \
      2>/dev/null || echo 0)"
    if [ "${mml}" -ge 200000 ] 2>/dev/null; then
      pass "vLLM max_model_len=${mml} (>=200000, long-horizon ready)"
    else
      fail BLOCKING "vLLM max_model_len=${mml} < 200000 — re-serve longctx (MAX_LEN=262144)"
    fi
    # responsiveness: one tiny completion
    body='{"model":"'"${VLLM_MODEL:-/model}"'","messages":[{"role":"user","content":"ping"}],"max_tokens":1}'
    if curl -fsS -m 20 "${VLLM_BASE_URL}/chat/completions" -H 'Content-Type: application/json' \
         -d "${body}" >/dev/null 2>&1; then
      pass "model responds to a tiny completion"
    else
      warn "model did not answer a tiny completion in 20s (cold/loaded? — not blocking)"
    fi
  else
    fail BLOCKING "vLLM NOT reachable at ${VLLM_BASE_URL}"
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
hdr "MCP servers"
down=0
for name in "${HMX_ACTIVE_SERVERS[@]}"; do
  if hmx_health_get "${name}" >/dev/null 2>&1; then
    :
  else
    fail soft "${HMX_DIR[$name]} ($(hmx_port "${name}")) DOWN"
    down=$((down+1))
  fi
done
if [ "${down}" -eq 0 ]; then
  pass "all ${#HMX_ACTIVE_SERVERS[@]} MCP servers healthy"
else
  warn "${down} MCP server(s) down — tools degrade; run 'hm up' (a long task needs them)"
fi
# embed/rerank
if [ "${HMX_PROFILE}" = "gpu_local" ]; then
  [ -n "$(hmx_port_pids "${EMBED_PORT:-8002}")" ] && pass "embed serve up on :${EMBED_PORT:-8002}" \
    || warn "embed serve down on :${EMBED_PORT:-8002} (RAG dense lane off; serve-embed.sh)"
  [ -n "$(hmx_port_pids "${RERANK_PORT:-8003}")" ] && pass "rerank serve up on :${RERANK_PORT:-8003}" \
    || warn "rerank serve down on :${RERANK_PORT:-8003} (RAG rerank off; serve-rerank.sh)"
fi

# ════════════════════════════════════════════════════════════════════════════
hdr "Hermes config (the killers)"
if [ ! -f "${CONFIG}" ]; then
  fail BLOCKING "Hermes config not found at ${CONFIG}"
else
  # Read the timeouts + gateway in one python pass.
  read -r t_research t_docs t_rag gateway < <(HMX_CFG="${CONFIG}" python3 - <<'PY' 2>/dev/null || echo "0 0 0 0"
import os, yaml
cfg = yaml.safe_load(open(os.environ["HMX_CFG"])) or {}
srv = cfg.get("mcp_servers") or {}
def t(name):
    e = srv.get(name) or {}
    v = e.get("timeout")
    return int(v) if isinstance(v, (int, float)) else 0
gw = ((cfg.get("agent") or {}).get("gateway_timeout"))
gw = int(gw) if isinstance(gw, (int, float)) else 0
print(t("hermes-max-research"), t("hermes-max-docs"), t("hermes-max-codebase-rag"), gw)
PY
)
  [ "${t_research:-0}" -ge 900 ] 2>/dev/null \
    && pass "hermes-max-research timeout=${t_research} (>=900)" \
    || fail BLOCKING "hermes-max-research timeout=${t_research:-?} < 900 — run fix-hermes-timeouts.sh"
  [ "${t_docs:-0}" -ge 300 ] 2>/dev/null \
    && pass "hermes-max-docs timeout=${t_docs} (>=300)" \
    || fail BLOCKING "hermes-max-docs timeout=${t_docs:-?} < 300 — run fix-hermes-timeouts.sh"
  [ "${t_rag:-0}" -ge 300 ] 2>/dev/null \
    && pass "hermes-max-codebase-rag timeout=${t_rag} (>=300)" \
    || fail soft "hermes-max-codebase-rag timeout=${t_rag:-?} < 300 — run fix-hermes-timeouts.sh"
  [ "${gateway:-0}" -ge 1800 ] 2>/dev/null \
    && pass "agent.gateway_timeout=${gateway} (>=1800)" \
    || warn "agent.gateway_timeout=${gateway:-?} < 1800 (do not lower; expected 1800)"
fi

# ════════════════════════════════════════════════════════════════════════════
hdr "watchdog (deep_research budget)"
if [ -x "${REPO_ROOT}/mcp-watchdog/.venv/bin/python" ]; then
  read -r dr_budget dr_ceiling dr_hb < <(cd "${REPO_ROOT}/mcp-watchdog" && \
    .venv/bin/python -c "
import watchdog_core as wc
b = wc.tool_budget('deep_research')
print(int(b['budget_s']), int(b['ceiling_s'] or 0), int(b['heartbeat_timeout_s']))
" 2>/dev/null || echo "0 0 0")
  [ "${dr_budget:-0}" -ge 600 ] 2>/dev/null \
    && pass "deep_research budget_s=${dr_budget} (>=600)" \
    || fail soft "deep_research budget_s=${dr_budget:-?} < 600 (set BUDGET_DEEP_RESEARCH_S / registry)"
  [ "${dr_ceiling:-0}" -ge 900 ] 2>/dev/null \
    && pass "deep_research ceiling_s=${dr_ceiling} (>=900)" \
    || fail soft "deep_research ceiling_s=${dr_ceiling:-?} < 900"
  [ "${dr_hb:-0}" -ge 120 ] 2>/dev/null \
    && pass "deep_research heartbeat_timeout_s=${dr_hb} (>=120)" \
    || fail soft "deep_research heartbeat_timeout_s=${dr_hb:-?} < 120"
else
  warn "mcp-watchdog venv missing — cannot read budgets (run bootstrap.sh)"
fi

# ════════════════════════════════════════════════════════════════════════════
hdr "datastores"
# KG + RAG: the on-disk sqlite the MCP servers back onto. "reachable" = file is a
# valid, openable sqlite (a serious task checkpoints/recalls against these).
_sqlite_ok() { python3 -c "import sqlite3,sys; sqlite3.connect('file:%s?mode=ro'%sys.argv[1], uri=True).execute('PRAGMA schema_version').fetchone()" "$1" 2>/dev/null; }
KG_DB="$(hmx_kg_path)"; RAG_DB="$(hmx_rag_path)"
if [ -f "${KG_DB}" ] && _sqlite_ok "${KG_DB}"; then pass "KG db reachable (${KG_DB})"
elif [ -f "${KG_DB}" ]; then warn "KG db present but not a clean sqlite (${KG_DB})"
else warn "KG db not yet created (${KG_DB}) — first kg_record will create it"; fi
if [ -f "${RAG_DB}" ] && _sqlite_ok "${RAG_DB}"; then pass "RAG db reachable (${RAG_DB})"
elif [ -f "${RAG_DB}" ]; then warn "RAG db present but not a clean sqlite (${RAG_DB})"
else warn "RAG db not yet created (${RAG_DB}) — first index/ingest will create it"; fi
# SearXNG + Crawl4AI: the research fetch path (degrades, so WARN not FAIL).
SEARX="${SEARXNG_URL:-http://localhost:8080}"
curl -fsS -m 4 "${SEARX}" >/dev/null 2>&1 && pass "SearXNG reachable (${SEARX})" \
  || warn "SearXNG unreachable (${SEARX}) — explore is empty; run ./searXNG.sh"
CRAWL="${CRAWL4AI_URL:-http://localhost:11235}"
curl -fsS -m 4 "${CRAWL%/}/health" >/dev/null 2>&1 || curl -fsS -m 4 "${CRAWL}" >/dev/null 2>&1 \
  && pass "Crawl4AI reachable (${CRAWL})" \
  || warn "Crawl4AI unreachable (${CRAWL}) — falls back to trafilatura; run ./crawl4ai.sh"

# ════════════════════════════════════════════════════════════════════════════
hdr "environment"
# HERMES_AGENT_TIMEOUT: if set to a low value it OVERRIDES config and kills the
# agent early (600 = 10-minute death). Unset or >=3600 is fine.
if [ -z "${HERMES_AGENT_TIMEOUT:-}" ]; then
  pass "HERMES_AGENT_TIMEOUT unset (config gateway_timeout governs)"
elif [ "${HERMES_AGENT_TIMEOUT}" -ge 3600 ] 2>/dev/null; then
  pass "HERMES_AGENT_TIMEOUT=${HERMES_AGENT_TIMEOUT} (>=3600)"
else
  fail BLOCKING "HERMES_AGENT_TIMEOUT=${HERMES_AGENT_TIMEOUT} < 3600 — overrides config, kills the agent early (unset it or >=3600)"
fi
# Don't run the agent inside the harness repo (it would edit hermes-max itself).
if [ "$(cd "${AGENT_CWD}" 2>/dev/null && pwd -P)" = "$(cd "${REPO_ROOT}" && pwd -P)" ]; then
  warn "agent cwd IS the harness repo (${REPO_ROOT}) — run the agent in a separate project dir"
else
  pass "agent cwd is not the harness repo"
fi
# git repo in the agent cwd (checkpoint needs it).
if git -C "${AGENT_CWD}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  pass "git repo present in agent cwd (checkpoint can commit)"
else
  warn "no git repo in agent cwd (${AGENT_CWD}) — 'git init' so checkpoint works"
fi
# Long-horizon skills installed.
SKILL_DEST="${HERMES_HOME}/skills/hermes-max"
missing=()
if [ -d "${REPO_ROOT}/skills" ]; then
  for d in "${REPO_ROOT}"/skills/*/; do
    [ -f "${d}SKILL.md" ] || continue
    n="$(basename "${d}")"
    [ -f "${SKILL_DEST}/${n}/SKILL.md" ] || missing+=("${n}")
  done
fi
if [ "${#missing[@]}" -eq 0 ]; then
  pass "all long-horizon skills installed in ${SKILL_DEST}"
else
  fail soft "missing skills (${missing[*]}) — run register-mcp.sh or preflight --fix"
fi
# Critical single-call skills must be installed AND carry the single-call constraint
# (a stale copy without it is the multi-deep_research-call trap).
for s in workflow-deep-research workflow-tool-selection; do
  f="${SKILL_DEST}/${s}/SKILL.md"
  if [ ! -f "${f}" ]; then
    fail soft "critical skill ${s} NOT installed in ${SKILL_DEST} — run register-mcp.sh"
  elif [ "${s}" = "workflow-deep-research" ] && ! grep -qiE "more than once|ONCE per (task|session)" "${f}"; then
    fail soft "${s} installed but missing the single-call constraint (stale copy) — reinstall"
  else
    pass "critical skill ${s} installed in Hermes skills dir"
  fi
done
# Also confirm Hermes actually has it enabled (best-effort; informational).
if command -v hermes >/dev/null 2>&1; then
  if hermes skills list 2>/dev/null | grep -qE "workflow-deep-research\b.*enabled"; then
    pass "Hermes reports workflow-deep-research enabled"
  else
    warn "could not confirm workflow-deep-research enabled via 'hermes skills list'"
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
echo
printf '%s═══ pre-flight summary ═══%s\n' "${B}" "${Z}"
echo "  BLOCKING failures: ${BLOCKING_FAILS}   soft failures: ${NONBLOCK_FAILS}   warnings: ${WARNS}"
if [ "${BLOCKING_FAILS}" -eq 0 ]; then
  printf '  %s✓ all BLOCKING checks pass — cleared for a long-horizon task%s\n' "${G}" "${Z}"
  exit 0
else
  printf '  %s✗ %d BLOCKING failure(s) — fix before starting a serious task%s\n' "${R}" "${BLOCKING_FAILS}" "${Z}"
  exit 1
fi
