#!/usr/bin/env bash
# bottleneck-eval.sh — do the advanced features EARN their latency? (Stage 7c)
#
# Runs the SAME task twice — once FULL (all advanced MCPs active) and once BARE
# (the minimal path) — and prints, for each, the wall-clock AND the 3-bucket timing
# split (inference / tool-work / artificial) plus a quality note. This is the only
# empirical way to answer "do the advanced features earn their time?": if full is
# 3x slower but meaningfully better, justified; if 3x slower and no better, the
# features are an artificial bottleneck → gate them more conservatively. Writes a
# readable bottleneck_report.md.
#
# The task to run is `$HMX_BENCH_CMD` (a command). With no model/keys available it
# defaults to an OFFLINE DEMONSTRATION using the reliability dry-run as the task, so
# the report format + split math are proven now; for a real coding/research task,
# set HMX_BENCH_CMD to a model-driven command (the same compare structure applies).
#
#   bottleneck-eval.sh
#   HMX_BENCH_CMD="hermes --task 'fix the failing test'" bottleneck-eval.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

REPORT="${REPO_ROOT}/bottleneck_report.md"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# The "task": default = reliability dry-run (model-independent, runnable now).
# FULL exercises the whole reliability sequence; BARE trims to the minimal path
# (index + query only) to stand in for "fewer advanced features".
DEFAULT_FULL="python3 ${SCRIPT_DIR}/dry_run_reliability.py"
DEFAULT_BARE="env HMX_BENCH_BARE=1 python3 ${SCRIPT_DIR}/dry_run_reliability.py"
BENCH_FULL="${HMX_BENCH_CMD:-${DEFAULT_FULL}}"
BENCH_BARE="${HMX_BENCH_CMD_BARE:-${DEFAULT_BARE}}"
IS_DEMO=0; [ -z "${HMX_BENCH_CMD:-}" ] && IS_DEMO=1

run_one() {  # label, cmd  -> echoes "wall_s|json"
  local label="$1"
  local cmd="$2"
  local logdir="${TMP}/${label}"
  mkdir -p "${logdir}"
  local t0 t1
  t0="$(date +%s.%N)"
  HERMES_MAX_LOG_DIR="${logdir}" HERMES_MAX_VERBOSITY=verbose \
    bash -c "${cmd}" >"${logdir}/stdout.txt" 2>&1 || true
  t1="$(date +%s.%N)"
  local wall json
  wall="$(awk "BEGIN{printf \"%.2f\", ${t1}-${t0}}")"
  json="$(python3 "${SCRIPT_DIR}/run_summary.py" --json "${logdir}/live.jsonl" 2>/dev/null || echo '{}')"
  echo "${wall}|${json}"
}

echo "═══ bottleneck eval — full vs bare ═══"
[ "${IS_DEMO}" -eq 1 ] && echo "  (offline demonstration: task = reliability dry-run; set HMX_BENCH_CMD for a real task)"
echo "  running FULL…";  FULL_OUT="$(run_one full "${BENCH_FULL}")"
echo "  running BARE…";  BARE_OUT="$(run_one bare "${BENCH_BARE}")"

FULL_WALL="${FULL_OUT%%|*}"; FULL_JSON="${FULL_OUT#*|}"
BARE_WALL="${BARE_OUT%%|*}"; BARE_JSON="${BARE_OUT#*|}"

# Render + write the report via a tiny python helper (stdlib).
python3 - "$REPORT" "$IS_DEMO" "$FULL_WALL" "$BARE_WALL" "$FULL_JSON" "$BARE_JSON" <<'PY'
import json, sys
report, is_demo, fwall, bwall, fjson, bjson = sys.argv[1:7]
def load(s):
    try: return json.loads(s)
    except Exception: return {}
F, B = load(fjson), load(bjson)
def split(j):
    b = j.get("buckets", {}) or {}
    tot = sum(b.values()) or 1.0
    return b, tot
fb, ft = split(F); bb, bt = split(B)
def line(name, wall, b, tot):
    inf, tw, ar = b.get("inference",0), b.get("tool-work",0), b.get("artificial",0)
    return (f"- **{name}** — wall {wall}s · inference {inf:.1f}s ({100*inf/tot:.0f}%) · "
            f"tool-work {tw:.1f}s ({100*tw/tot:.0f}%) · artificial {ar:.1f}s ({100*ar/tot:.0f}%) · "
            f"{F.get('calls',0) if name=='FULL' else B.get('calls',0)} tool-calls")
try: ratio = float(fwall)/float(bwall) if float(bwall) else 0.0
except Exception: ratio = 0.0
ar_frac = 100*fb.get("artificial",0)/ft
verdict = []
if ar_frac >= 15:
    causes = F.get("artificial_by_cause", {})
    top = max(causes.items(), key=lambda kv: kv[1]["secs"]) if causes else ("?", {"secs":0,"count":0})
    verdict.append(f"⚠ FULL spends {ar_frac:.0f}% of its time ARTIFICIAL "
                   f"(dominant: {top[0]} — {top[1]['secs']:.0f}s) → gate that feature more conservatively.")
else:
    verdict.append(f"✓ FULL has low artificial cost ({ar_frac:.0f}%) — its extra latency is real work "
                   "(inference + tool-work), not rate-limit waiting.")
verdict.append(f"FULL is {ratio:.1f}x the bare wall-clock. "
               + ("If the result quality is meaningfully better, that is justified; if not, the advanced "
                  "features are not earning their latency." ))
lines = [
    "# bottleneck_report.md — do the advanced features earn their latency?",
    "",
    ("_Offline demonstration (task = reliability dry-run). Set `HMX_BENCH_CMD` to a "
     "model-driven task for a real comparison; the same full-vs-bare split applies._"
     if is_demo == "1" else "_Same task run FULL (all advanced MCPs) vs BARE (minimal path)._"),
    "",
    "## Wall-clock + 3-bucket timing split",
    "",
    line("FULL", fwall, fb, ft),
    line("BARE", bwall, bb, bt),
    "",
    "## Verdict",
    "",
] + [f"- {v}" for v in verdict] + [
    "",
    "## How to read this",
    "",
    "- **inference** — local model thinking/generation (irreducible real work).",
    "- **tool-work** — tool execution doing real work (crawl, tests, indexing, retrieval).",
    "- **artificial** — waiting on rate-limited APIs, 429/5xx backoff+retries, redundant "
    "sequential calls, MCP overhead. A large artificial fraction means a specific feature "
    "is wasting the agent's time — the line above names which.",
    "",
]
open(report, "w").write("\n".join(lines))
print("\n".join(lines))
print(f"\n  report -> {report}")
PY
