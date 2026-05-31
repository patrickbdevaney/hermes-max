#!/usr/bin/env bash
# Stage 5 (long-horizon) — the end-to-end proof. Runs ONE real ~15-minute Hermes
# agent task through the full loop in a fresh temp project, then asserts the five
# real-world effects. Passing this means a legitimate long-horizon run (deep_research
# + implement + verify + checkpoint) completes WITHOUT a timeout/stall kill — i.e.
# the Groth16 prompt will work.
#
#   hermes "Research the Miller-Rabin primality test (use deep_research), implement a
#   correct Python primality checker with tests using real test vectors from the
#   research, verify green, checkpoint. Done when verify is green and at least 2
#   sources are in the corpus."
#
# Asserts: (1) deep_research completed without a timeout/kill in the live log,
# (2) >=2 markdown files in the corpus, (3) >=1 KG entity recorded, (4) the verify
# gate returns GREEN on the project, (5) a git checkpoint commit exists — and the
# whole thing finishes in < 20 minutes.
#
# `hm smoke` runs this. Standalone-runnable too.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

command -v hermes >/dev/null 2>&1 || { echo "✗ 'hermes' not on PATH — install/activate Hermes first."; exit 127; }

LIVE="${HMX_LOG_DIR:-${HOME}/.hermes-max/logs}/live.jsonl"
CORPUS_DIR="$(hmx_corpus_dir)"
KG_DB="$(hmx_kg_path)"
# Per-turn kill cap: a single deep_research is ~600-1000s on a slow laptop GPU, then
# implement + verify + checkpoint. Give the turn room to FINISH (so we can measure a
# real wall time) — the separate < 20-min (WALL_CAP_S) assertion judges speed. With
# the multi-call retry trap fixed (single call, confidence not a retry signal, raised
# ceiling), a clean run lands well under the cap. Both env-overridable.
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-1500}"
WALL_CAP_S="${SMOKE_WALL_CAP_S:-1200}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/hmx-smoke.XXXXXX")"
PROJ="${WORK}/primality"
mkdir -p "${PROJ}"
( cd "${PROJ}" && git init -q && git config user.email smoke@hermes-max && git config user.name hermes-max-smoke \
    && echo "# Miller-Rabin primality (hermes-max integration smoke)" > README.md \
    && git add -A && git commit -qm "init" )

cleanup() { rm -rf "${WORK}"; }
trap cleanup EXIT

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; D=""; B=""; Z=""; fi

echo "═══ hermes-max integration smoke ═══"
echo "project: ${PROJ}   corpus: ${CORPUS_DIR}"

# ── pre-flight: the stack must be cleared, or the real loop can't run ──────────
if [ -x "${SCRIPT_DIR}/preflight.sh" ]; then
  echo "• pre-flight (must clear all BLOCKING checks for the project dir) …"
  if ! PREFLIGHT_AGENT_CWD="${PROJ}" "${SCRIPT_DIR}/preflight.sh" --fix >/dev/null 2>&1; then
    echo "${R}✗ pre-flight reported BLOCKING failures — run 'hm preflight' and fix them first.${Z}"
    exit 1
  fi
  echo "  ${G}✓${Z} pre-flight clear"
elif ! "${SCRIPT_DIR}/healthcheck.sh" >/dev/null 2>&1; then
  echo "${R}✗ stack not healthy (run: hm up).${Z}"; exit 1
fi

# ── skills must be LOADED by Hermes (a missing single-call skill is the multi-call
#    trap) — assert before spending 15 minutes on the agent turn. ───────────────
echo "• confirming the long-horizon skills are loaded by Hermes …"
SKILLS_LIST="$(hermes skills list 2>/dev/null || true)"
skills_ok=1
for s in workflow-deep-research workflow-tool-selection; do
  if printf '%s\n' "${SKILLS_LIST}" | grep -qE "${s}\b.*enabled" \
     || [ -f "${HOME}/.hermes/skills/hermes-max/${s}/SKILL.md" ]; then
    echo "  ${G}✓${Z} ${s} loaded"
  else
    echo "  ${R}✗${Z} ${s} NOT loaded — run scripts/register-mcp.sh"; skills_ok=0
  fi
done
if [ "${skills_ok}" != "1" ]; then
  echo "${R}✗ required skills not loaded — fix before the smoke (the single-call constraint won't apply).${Z}"
  exit 1
fi

# ── baselines (so we measure THIS run's effect) ──────────────────────────────
# Corpus writes land in the GLOBAL corpus tree (~/.hermes-max/corpus/<namespace>/
# <type>/<slug>.md — e.g. a research-* namespace), NOT the project temp dir. So we
# count *.md files NEWER than a marker stamped at run start, across the whole corpus.
kg_entities() { sqlite3 "${KG_DB}" 'select count(*) from entities' 2>/dev/null || echo 0; }
new_corpus_md() { find "${CORPUS_DIR}" -type f -name '*.md' -newer "${START_MARKER}" 2>/dev/null | wc -l | tr -d ' '; }
START_MARKER="${WORK}/.start_marker"
mkdir -p "${CORPUS_DIR}" 2>/dev/null || true
: > "${START_MARKER}"          # mtime = run start; corpus files newer than this are THIS run's
KG_BEFORE="$(kg_entities)"
LIVE_MARK="$(wc -l < "${LIVE}" 2>/dev/null || echo 0)"

TASK="Research the Miller-Rabin primality test (use deep_research), implement a correct Python primality checker with tests using real test vectors from the research, verify green, checkpoint. Done when verify is green and at least 2 sources are in the corpus."

echo "• running the real agent turn (timeout ${SMOKE_TIMEOUT}s) — this is a multi-minute task …"
START="${SECONDS}"
AGENT_OUT="$( cd "${PROJ}" && timeout "${SMOKE_TIMEOUT}" hermes -z "${TASK}" --yolo 2>/dev/null )"
ELAPSED=$(( SECONDS - START ))
echo "  turn finished in ${ELAPSED}s"

# spans emitted during the turn (one name per line)
SPANS="$(tail -n +$((LIVE_MARK+1)) "${LIVE}" 2>/dev/null | python3 -c '
import json,sys
for ln in sys.stdin:
    try: e=json.loads(ln)
    except Exception: continue
    n=e.get("span") or e.get("tool") or ""
    if n: print(n)
' 2>/dev/null)"
span_fired() { printf '%s\n' "${SPANS}" | grep -qiE "$1"; }

PASS=0; FAILN=0
ok()   { printf '  %s✓ PASS%s  %s\n' "${G}" "${Z}" "$1"; PASS=$((PASS+1)); }
no()   { printf '  %s✗ FAIL%s  %s\n' "${R}" "${Z}" "$1"; FAILN=$((FAILN+1)); }

echo
echo "${B}── assertions ──${Z}"

# 1. deep_research completed without a timeout/kill in the live log.
#    Evidence: a deep_research synthesis/done span fired AND the turn produced
#    output (didn't time out) AND no hung/kill marker appeared during the run.
empty=0; [ -z "${AGENT_OUT}" ] && empty=1
# the actual completion span (research server emits it only when the full
# plan->verify->synthesize finished) — the rigorous "no finish-line kill" signal.
dr_done=0; span_fired "deep_research_done|report_synthesized" && dr_done=1
killed=0;  span_fired "hung|killed|stall_kill|over.?ceiling|gateway_timeout" && killed=1
if [ "${dr_done}" = "1" ] && [ "${empty}" = "0" ] && [ "${killed}" = "0" ]; then
  ok "deep_research completed without timeout/kill (deep_research_done fired, no kill marker)"
else
  no "deep_research did not cleanly complete (done=${dr_done} empty_out=${empty} kill_marker=${killed})"
fi

# 2. >= 2 markdown files written to the corpus DURING this run (newer than the
#    start marker), counted across the whole global corpus tree.
CORPUS_NEW="$(new_corpus_md)"
if [ "${CORPUS_NEW:-0}" -ge 2 ] 2>/dev/null; then
  ok "corpus gained ${CORPUS_NEW} new markdown file(s) this run (>=2, under ${CORPUS_DIR})"
else
  no "corpus gained only ${CORPUS_NEW} new markdown file(s) this run (<2, under ${CORPUS_DIR})"
fi

# 3. >= 1 KG entity recorded.
KG_AFTER="$(kg_entities)"
if [ "${KG_AFTER:-0}" -ge 1 ] 2>/dev/null; then
  ok "KG has ${KG_AFTER} entit(y/ies) (>=1; was ${KG_BEFORE})"
else
  no "KG has ${KG_AFTER} entities (<1; was ${KG_BEFORE})"
fi

# 4. the verify gate returns GREEN on the project (independent check, not the
#    agent's self-report).
green="$(MCP_URL="http://$(hmx_bind_host):${MCP_VERIFY_PORT:-9101}/mcp" EVAL_DIR="${PROJ}" \
  "${REPO_ROOT}/mcp-verify/.venv/bin/python" - <<'PY' 2>/dev/null || echo 0
import asyncio,json,os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client(os.environ["MCP_URL"], timeout=30, sse_read_timeout=300) as (r,w,_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            res=await s.call_tool("verify",{"path":os.environ["EVAL_DIR"]})
            d=res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(d,dict) and "result" in d and "passed" not in d: d=d["result"]
            print(1 if d.get("passed") else 0)
asyncio.run(main())
PY
)"
if [ "${green}" = "1" ]; then
  ok "verify gate is GREEN on the project"
else
  no "verify gate is NOT green on the project (green=${green})"
fi

# 5. a git checkpoint commit exists.
ckpt="$( cd "${PROJ}" && git log --grep="hermes-max checkpoint" --oneline 2>/dev/null | wc -l | tr -d ' ' )"
if [ "${ckpt:-0}" -ge 1 ] 2>/dev/null; then
  ok "git checkpoint commit exists (${ckpt} marker commit(s))"
else
  no "no [hermes-max checkpoint] commit in the project repo"
fi

# wall-time bound
if [ "${ELAPSED}" -lt "${WALL_CAP_S}" ] 2>/dev/null; then
  ok "total wall time ${ELAPSED}s < ${WALL_CAP_S}s (<20 min)"
else
  no "total wall time ${ELAPSED}s >= ${WALL_CAP_S}s (over the 20-min bound)"
fi

echo
if [ "${FAILN}" -eq 0 ]; then
  printf '%s✓ integration smoke PASSED%s — %d/%d assertions. Stack cleared for a long-horizon task (Groth16-ready).\n' \
    "${G}${B}" "${Z}" "${PASS}" "$((PASS+FAILN))"
  exit 0
else
  printf '%s✗ integration smoke FAILED%s — %d/%d assertions passed. See spans in %s.\n' \
    "${R}${B}" "${Z}" "${PASS}" "$((PASS+FAILN))" "${LIVE}"
  exit 1
fi
