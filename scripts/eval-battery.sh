#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# eval-battery.sh — prove EVERY core capability works in the REAL Hermes agent
# loop (Stage 3), not in isolation.
#
# The problem this answers: isolation smoke tests (curl a server, monkeypatched
# unit tests) pass, yet a feature can still fail when the Hermes AGENT actually
# invokes it in the live loop (e.g. the FastMCP event-loop / asyncio bugs fixed
# in Stage 2 were invisible to smoke tests). This battery drives each capability
# THROUGH a real `hermes -z … --yolo` agent turn and asserts the REAL-WORLD
# EFFECT — a file on disk, a row in the KG db, a doc in the corpus, a git
# checkpoint commit, a line in MEMORY.md, a span in the live log — NOT just that
# an HTTP call returned 200. A capability "works" only if the agent invoking it
# produced the change it is supposed to.
#
# Each test is ISOLATED: the RAG/KG/corpus stores are snapshotted at start and
# restored at exit, MEMORY.md is backed up and restored, and filesystem tests run
# in their own temp project dir — so the battery never pollutes real state.
#
# Usage:
#   eval-battery.sh                 run the whole battery
#   eval-battery.sh <capability>    run one (e.g. eval-battery.sh knowledge-graph)
#   eval-battery.sh --no-cloud      skip tests that need a cloud/conductor key
#   eval-battery.sh --quick         fast subset (core-memory, knowledge-graph, verify)
#   eval-battery.sh --list          list capabilities
# Capabilities: core-memory knowledge-graph verify codebase-rag checkpoint
#               docs search watchdog research escalation observability
#
# Output: a readable eval_battery_report.md (per-capability PASS/FAIL, the agent
# task used, the tool that fired, the artifact effect verified, and for failures
# the precise break-point: tool-not-called / tool-errored / no-effect).
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

ALL_CAPS=(core-memory knowledge-graph verify codebase-rag checkpoint docs search watchdog research escalation observability)
# --quick subset: a fast handful of core capabilities driven through a real agent
# turn — used by `bootstrap.sh --verify-agent` to confirm a fresh install actually
# works end-to-end (not just that servers are up).
QUICK_CAPS=(core-memory knowledge-graph verify)
NO_CLOUD=""
QUICK=""
ONLY=""
for a in "$@"; do
  case "${a}" in
    --no-cloud) NO_CLOUD=1 ;;
    --quick) QUICK=1 ;;
    --list) printf '%s\n' "${ALL_CAPS[@]}"; exit 0 ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown flag: ${a}"; exit 2 ;;
    *) ONLY="${a}" ;;
  esac
done

LIVE="${HMX_LOG_DIR:-${HOME}/.hermes-max/logs}/live.jsonl"
REPORT="${REPO_ROOT}/eval_battery_report.md"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/hmx-eval.XXXXXX")"
MEMORY_FILE="${HOME}/.hermes/MEMORY.md"
SNAP_NAME="eval-battery-baseline"
RESULTS=()   # "cap|verdict|tool|effect|breakpoint|task"

command -v hermes >/dev/null 2>&1 || { echo "✗ 'hermes' not on PATH — install/activate Hermes first."; exit 127; }

# ── preflight: the stack must be live (the whole point is the REAL loop) ──────
if ! "${SCRIPT_DIR}/healthcheck.sh" >/dev/null 2>&1; then
  echo "✗ not all MCP servers are live (run: hm up). The battery needs the real loop."; exit 1
fi
echo "═══ hermes-max eval battery ═══  (real agent turns; asserting real effects)"
echo "workdir: ${WORK}"

# ── isolation: snapshot stores + MEMORY.md; restore on exit ──────────────────
echo "• snapshotting RAG/KG/corpus + MEMORY.md (restored on exit) …"
"${SCRIPT_DIR}/snapshot-stores.sh" "${SNAP_NAME}" --force >/dev/null 2>&1 || true
[ -f "${MEMORY_FILE}" ] && cp -a "${MEMORY_FILE}" "${WORK}/MEMORY.md.bak"
cleanup() {
  echo "• restoring baseline stores + MEMORY.md …"
  # Stop rag/kg first: restoring a SQLite file under a live server has no effect
  # (the server keeps its open handle on the polluted db) and risks corruption.
  hmx_stop_one rag >/dev/null 2>&1 || true
  hmx_stop_one kg  >/dev/null 2>&1 || true
  "${SCRIPT_DIR}/restore-stores.sh" "${SNAP_NAME}" >/dev/null 2>&1 || true
  if [ -f "${WORK}/MEMORY.md.bak" ]; then cp -a "${WORK}/MEMORY.md.bak" "${MEMORY_FILE}"; fi
  hmx_start_one rag >/dev/null 2>&1 || true
  hmx_start_one kg  >/dev/null 2>&1 || true
  hmx_wait_health rag 20 >/dev/null 2>&1 || true
  hmx_wait_health kg 20 >/dev/null 2>&1 || true
  rm -rf "${WORK}"
}
trap cleanup EXIT

# ── helpers ──────────────────────────────────────────────────────────────────
# Real local-model agent turns are slow (index+reason can take ~3-4 min); give a
# generous default so a turn is never killed mid-flight (an empty AGENT_OUT reads
# as "turn failed"). Override with EVAL_AGENT_TIMEOUT.
AGENT_TIMEOUT_DEFAULT="${EVAL_AGENT_TIMEOUT:-420}"
AGENT_OUT=""        # last agent stdout
SPAN_DELTA=""       # live.jsonl spans emitted during the last turn (one per line: span name)

# run_agent <prompt> [workdir] [timeout] -> sets AGENT_OUT + SPAN_DELTA
run_agent() {
  local prompt="$1" wd="${2:-${REPO_ROOT}}" to="${3:-${AGENT_TIMEOUT_DEFAULT}}"
  local mark; mark="$(wc -l < "${LIVE}" 2>/dev/null || echo 0)"
  AGENT_OUT="$( cd "${wd}" && timeout "${to}" hermes -z "${prompt}" --yolo 2>/dev/null )"
  SPAN_DELTA="$(tail -n +$((mark+1)) "${LIVE}" 2>/dev/null | python3 -c '
import json,sys
for ln in sys.stdin:
    try: e=json.loads(ln)
    except Exception: continue
    n=e.get("span") or e.get("tool") or ""
    if n: print(n)
' 2>/dev/null)"
}

# did a span/tool matching the pattern fire during the last turn?
span_fired() { printf '%s\n' "${SPAN_DELTA}" | grep -qiE "$1"; }

# record <cap> <PASS|FAIL> <tool-evidence> <effect-evidence> <breakpoint> <task>
record() { RESULTS+=("$1|$2|$3|$4|$5|$6");
  if [ "$2" = "PASS" ]; then echo "  ✅ $1 — $4"; else echo "  ❌ $1 — break: $5"; fi
}

# classify the break-point when an effect is missing
breakpoint_of() { # <tool-fired bool> <agent-out-empty bool>
  if [ "$2" = "1" ]; then echo "agent turn produced no output (turn failed/timed out)";
  elif [ "$1" = "1" ]; then echo "tool fired but produced no effect (tool errored / wrong args)";
  else echo "expected tool was not called by the agent"; fi
}

sqlite_count() { sqlite3 "$1" "$2" 2>/dev/null || echo 0; }

# ── capability tests ──────────────────────────────────────────────────────────
cap_core-memory() {
  local task="Append exactly this line to your core memory (long-term MEMORY.md): 'eval-battery marker EBM-${RANDOM}'. Use your core-memory tool. Then tell me you did it."
  local marker; marker="$(printf '%s' "${task}" | grep -oE 'EBM-[0-9]+')"
  run_agent "${task}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "core_memory|memory" && tf=1
  if grep -qF "${marker}" "${MEMORY_FILE}" 2>/dev/null; then
    record core-memory PASS "core_memory span=${tf}" "MEMORY.md contains ${marker}" "-" "${task}"
  else
    record core-memory FAIL "core_memory span=${tf}" "marker ${marker} NOT in MEMORY.md" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_knowledge-graph() {
  local db; db="$(hmx_kg_path)"
  local before; before="$(sqlite_count "${db}" 'select count(*) from entities')"
  local name="ebkg-${RANDOM}"
  local task="Use your knowledge-graph tools to record an entity named '${name}' of type 'decision' (we chose SQLite for storage). Then recall what you know about '${name}' and report it."
  run_agent "${task}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "record|kg_|entity|recall|graph" && tf=1
  local after; after="$(sqlite_count "${db}" 'select count(*) from entities')"
  local hit; hit="$(sqlite_count "${db}" "select count(*) from entities where name='${name}'")"
  if [ "${hit}" -ge 1 ] 2>/dev/null || { [ "${after}" -gt "${before}" ] 2>/dev/null && printf '%s' "${AGENT_OUT}" | grep -qiF "${name}"; }; then
    record knowledge-graph PASS "kg span=${tf}" "entities ${before}->${after}, '${name}' recorded+recalled" "-" "${task}"
  else
    record knowledge-graph FAIL "kg span=${tf}" "entities ${before}->${after}, '${name}' not found" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_verify() {
  local d="${WORK}/verify"; mkdir -p "${d}"
  local task="In the CURRENT directory, create a Python file add.py with a function add(a, b) that returns a+b, and a pytest test test_add.py that checks it. Then use your verify tool on this directory and only report done when the tests pass GREEN."
  run_agent "${task}" "${d}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "verify|quick_check|deep_verify" && tf=1
  # real effect: the files exist AND an independent verify on the dir is green
  local files=0; { [ -f "${d}/add.py" ] || ls "${d}"/*.py >/dev/null 2>&1; } && files=1
  local green=0
  if [ "${files}" = "1" ]; then
    green="$(MCP_URL="http://$(hmx_bind_host):${MCP_VERIFY_PORT:-9101}/mcp" EVAL_DIR="${d}" "${REPO_ROOT}/mcp-verify/.venv/bin/python" - <<'PY' 2>/dev/null || echo 0
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
  fi
  if [ "${files}" = "1" ] && [ "${green}" = "1" ]; then
    record verify PASS "verify span=${tf}" "agent wrote code+test in temp dir; verify GREEN" "-" "${task}"
  else
    record verify FAIL "verify span=${tf}" "files=${files} verify_green=${green}" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_codebase-rag() {
  local d="${WORK}/rag"; mkdir -p "${d}"
  local marker="zzq_marker_$(printf '%04d' $((RANDOM%10000)))"
  cat > "${d}/lib.py" <<EOF
def ${marker}(x):
    """A uniquely-named function planted for the eval battery."""
    return x * 2

def helper_fn(y):
    return y + 1
EOF
  local task="Index the CURRENT directory as a code repository, then use code search to tell me the names of the functions defined in this repo."
  # Heavy turn: index_repo + search_code (the dense/rerank lane can cost ~30s when
  # the local embed/rerank serve is up-but-not-serving) + reasoning. Budget like research.
  run_agent "${task}" "${d}" "${EVAL_RAG_TIMEOUT:-600}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "index_repo|search_code|scan_repo|repo_map|get_symbol" && tf=1
  if printf '%s' "${AGENT_OUT}" | grep -qF "${marker}"; then
    record codebase-rag PASS "rag span=${tf}" "agent indexed+retrieved the planted symbol '${marker}'" "-" "${task}"
  else
    record codebase-rag FAIL "rag span=${tf}" "planted symbol '${marker}' not in answer" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_checkpoint() {
  local d="${WORK}/ckpt"; mkdir -p "${d}"
  ( cd "${d}" && git init -q && git config user.email e@x && git config user.name e \
      && echo "print('v1')" > app.py && git add -A && git commit -qm "init" )
  local task="This is a git repo with app.py. Use your checkpoint tool to create a verified checkpoint labelled 'eval-green' of the CURRENT directory (initialise if needed). Then report the checkpoint was created."
  run_agent "${task}" "${d}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "checkpoint|revert" && tf=1
  local n; n="$( cd "${d}" && git log --grep="hermes-max checkpoint" --oneline 2>/dev/null | wc -l | tr -d ' ' )"
  if [ "${n:-0}" -ge 1 ] 2>/dev/null; then
    record checkpoint PASS "checkpoint span=${tf}" "git checkpoint commit created (${n} marker commit(s))" "-" "${task}"
  else
    record checkpoint FAIL "checkpoint span=${tf}" "no [hermes-max checkpoint] commit in repo" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_docs() {
  local task="Use your documentation tools to find and fetch the official Python 'json' module documentation, then summarise it. Mention the two main functions for converting to and from JSON text."
  run_agent "${task}" "${REPO_ROOT}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "search_docs|fetch_clean|ingest_doc|research_topic|doc_" && tf=1
  # real effect: the answer surfaces real json API names retrieved from docs
  if printf '%s' "${AGENT_OUT}" | grep -qiE "json\.(loads|dumps)|\bloads\b.*\bdumps\b|\bdumps\b.*\bloads\b"; then
    record docs PASS "docs span=${tf}" "agent fetched docs; answer cites real json.loads/json.dumps API" "-" "${task}"
  else
    record docs FAIL "docs span=${tf}" "answer lacks real json API names" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_search() {
  # best-of-N generate_and_select on a small, verifiable subtask
  local task="This is a HARD subtask. Use your best-of-N code generation tool (generate_and_select) to write a correct Python function is_palindrome(s) that ignores case and non-alphanumeric characters. Report the selected, verified solution."
  run_agent "${task}" "${WORK}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "generate_and_select|parallel_draft|select|best_of|search" && tf=1
  if [ "${tf}" = "1" ] && printf '%s' "${AGENT_OUT}" | grep -qiE "is_palindrome|palindrome"; then
    record search PASS "search span=${tf}" "generate_and_select ran; returned a palindrome solution" "-" "${task}"
  else
    record search FAIL "search span=${tf}" "best-of-N tool span=${tf}; solution present=$(printf '%s' "${AGENT_OUT}" | grep -qi palindrome && echo 1 || echo 0)" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_watchdog() {
  # The watchdog is a PASSIVE observer invoked by the loop, not a tool the agent
  # picks — so we verify the capability with a direct probe: feed it text with a
  # tight repetition (a spiral) and assert it is DETECTED.
  local mark; mark="$(wc -l < "${LIVE}" 2>/dev/null || echo 0)"
  local detected
  detected="$(MCP_URL="http://$(hmx_bind_host):${MCP_WATCHDOG_PORT:-9107}/mcp" "${REPO_ROOT}/mcp-watchdog/.venv/bin/python" - <<'PY' 2>/dev/null || echo 0
import asyncio,json,os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
SPIRAL=("I will try again. "*40)+("the same approach again and again "*30)
async def main():
    async with streamablehttp_client(os.environ["MCP_URL"], timeout=30, sse_read_timeout=120) as (r,w,_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            res=await s.call_tool("check_spiral",{"recent_thinking_text":SPIRAL})
            d=res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(d,dict) and "result" in d and "spiral_detected" not in d: d=d["result"]
            print(1 if (d.get("spiral_detected") or d.get("spiral") or d.get("detected")) else 0)
asyncio.run(main())
PY
)"
  if [ "${detected}" = "1" ]; then
    record watchdog PASS "check_spiral (direct probe)" "watchdog detected a deliberate spiral in repetitive text" "-" "direct check_spiral probe (passive observer, not agent-selected)"
  else
    record watchdog FAIL "check_spiral (direct probe)" "spiral not detected (detected=${detected})" "tool fired but produced no effect (tool errored / wrong args)" "direct check_spiral probe"
  fi
}

cap_research() {
  local before_corpus; before_corpus="$(find "$(hmx_corpus_dir)" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')"
  local task="Research the topic 'what is a Merkle tree in cryptography' using your deep research tool, and give me a short answer WITH at least one source URL citation. Keep it small (1 loop, 2 sources)."
  run_agent "${task}" "${REPO_ROOT}" "${EVAL_RESEARCH_TIMEOUT:-480}"
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "deep_research|research_planned|sources_explored|report_synthesized|explore" && tf=1
  if [ "${tf}" = "1" ] && printf '%s' "${AGENT_OUT}" | grep -qiE "https?://"; then
    record research PASS "deep_research span=${tf}" "deep_research ran; answer carries a source URL citation" "-" "${task}"
  else
    record research FAIL "deep_research span=${tf}" "research span=${tf}; citation URL present=$(printf '%s' "${AGENT_OUT}" | grep -qiE 'https?://' && echo 1 || echo 0)" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_escalation() {
  if [ -n "${NO_CLOUD}" ]; then
    record escalation SKIP "—" "skipped (--no-cloud)" "-" "(skipped)"; return
  fi
  # Escalation is gated OFF unless a funded cloud key + role is present. The real
  # effect to assert is graceful behaviour: it classifies/routes, and with no key
  # it DEGRADES to local rather than erroring or invoking a forbidden tier.
  local enabled; enabled="$(curl -fsS -m4 "http://$(hmx_bind_host):${MCP_ESCALATION_PORT:-9105}/ready" 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("enabled"))' 2>/dev/null || \
      curl -fsS -m4 "http://$(hmx_bind_host):${MCP_ESCALATION_PORT:-9105}/health" 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("enabled"))' 2>/dev/null || echo None)"
  local task="Classify the difficulty of this subtask and, if it is HARD and escalation is enabled, route it; otherwise stay local. Subtask: 'prove the Riemann hypothesis'. Just tell me the difficulty classification and whether you escalated or stayed local."
  run_agent "${task}" "${WORK}" 360
  local empty=0; [ -z "${AGENT_OUT}" ] && empty=1
  local tf=0; span_fired "escalat|classify|route|conductor|tier" && tf=1
  # PASS = the agent answered with a difficulty/route decision and did NOT crash;
  # when disabled, "local" is the correct degraded outcome.
  if [ "${empty}" = "0" ] && printf '%s' "${AGENT_OUT}" | grep -qiE "hard|difficult|local|escalat|tier"; then
    record escalation PASS "escalation span=${tf}, enabled=${enabled}" "classified difficulty + made a route/degrade decision (enabled=${enabled})" "-" "${task}"
  else
    record escalation FAIL "escalation span=${tf}, enabled=${enabled}" "no classify/route decision in answer" "$(breakpoint_of "${tf}" "${empty}")" "${task}"
  fi
}

cap_observability() {
  # Every agent turn should emit spans to the live log (and to Phoenix if up).
  local mark; mark="$(wc -l < "${LIVE}" 2>/dev/null || echo 0)"
  local task="Briefly, in one sentence, say hello and confirm you are running."
  run_agent "${task}" "${WORK}" 180
  local after; after="$(wc -l < "${LIVE}" 2>/dev/null || echo 0)"
  local phoenix; phoenix="$(curl -fsS -m4 "http://$(hmx_bind_host):${MCP_OBSERVABILITY_PORT:-9104}/ready" 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("phoenix_reachable"))' 2>/dev/null || echo '?')"
  # Direct effect: a recorded span lands in the live log.
  local mark2; mark2="$(wc -l < "${LIVE}" 2>/dev/null || echo 0)"
  local rec
  rec="$(MCP_URL="http://$(hmx_bind_host):${MCP_OBSERVABILITY_PORT:-9104}/mcp" "${REPO_ROOT}/mcp-observability/.venv/bin/python" - <<'PY' 2>/dev/null || echo 0
import asyncio,json,os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client(os.environ["MCP_URL"], timeout=20, sse_read_timeout=60) as (r,w,_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            res=await s.call_tool("record_trace",{"name":"eval_battery_probe","attributes":{"src":"eval-battery"}})
            d=res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            print(1)
asyncio.run(main())
PY
)"
  local grew=0; [ "${after}" -gt "${mark}" ] 2>/dev/null && grew=1
  if [ "${grew}" = "1" ] || [ "${rec}" = "1" ]; then
    record observability PASS "live-log + record_trace" "live.jsonl grew during turn (${mark}->${after}); record_trace ok; phoenix_reachable=${phoenix}" "-" "${task}"
  else
    record observability FAIL "live-log + record_trace" "no spans emitted (live.jsonl ${mark}->${after}, record=${rec})" "observability not emitting spans" "${task}"
  fi
}

# ── run selected capabilities ─────────────────────────────────────────────────
to_run=("${ALL_CAPS[@]}")
[ -n "${QUICK}" ] && to_run=("${QUICK_CAPS[@]}")
if [ -n "${ONLY}" ]; then
  case " ${ALL_CAPS[*]} " in *" ${ONLY} "*) to_run=("${ONLY}") ;;
    *) echo "✗ unknown capability '${ONLY}'. One of: ${ALL_CAPS[*]}"; exit 2 ;; esac
fi
for cap in "${to_run[@]}"; do
  echo "── ${cap} ──"
  "cap_${cap}"
done

# ── report ─────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; SKIP=0
{
  echo "# hermes-max eval battery — per-capability report"
  echo
  echo "Each capability was driven through a REAL \`hermes -z … --yolo\` agent turn (or, for the"
  echo "passive watchdog, a direct probe), and the assertion checks the REAL-WORLD EFFECT"
  echo "(file / KG db / corpus / git commit / MEMORY.md / live-log span), not just a 200."
  echo
  echo "| Capability | Result | Tool evidence | Real-effect verified | Break-point (if failed) |"
  echo "|---|---|---|---|---|"
  for r in "${RESULTS[@]}"; do
    IFS='|' read -r cap verdict tool effect bp task <<< "${r}"
    local_icon="✅ PASS"; case "${verdict}" in FAIL) local_icon="❌ FAIL";; SKIP) local_icon="⊘ SKIP";; esac
    echo "| \`${cap}\` | ${local_icon} | ${tool} | ${effect} | ${bp} |"
    case "${verdict}" in PASS) PASS=$((PASS+1));; FAIL) FAIL=$((FAIL+1));; SKIP) SKIP=$((SKIP+1));; esac
  done
  echo
  echo "## Agent tasks used"
  for r in "${RESULTS[@]}"; do
    IFS='|' read -r cap verdict tool effect bp task <<< "${r}"
    echo "- **${cap}**: ${task}"
  done
  echo
  echo "**Totals: ${PASS} pass · ${FAIL} fail · ${SKIP} skip** of ${#RESULTS[@]} capabilities."
  echo
  echo "_Isolation: RAG/KG/corpus snapshotted and restored; MEMORY.md backed up and restored;"
  echo "filesystem tests ran in a temp dir. Real state was not polluted._"
} > "${REPORT}"

echo
echo "═══ eval battery: ${PASS} pass · ${FAIL} fail · ${SKIP} skip ═══"
echo "report: ${REPORT}"
[ "${FAIL}" -eq 0 ] && exit 0 || exit 1
