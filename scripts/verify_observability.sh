#!/usr/bin/env bash
# verify_observability.sh — run the LIVE observability checks on the real host (where the
# vLLM endpoint + hermes binary are reachable). Claude Code cannot reach those from its
# sandbox, so it wrote this for you to run; paste the output back.
#
#   ./scripts/verify_observability.sh [all|1|2|3|5]   (default: all)
#
# Each section prints WHAT IT DOES, then "EXPECTED:" describing a PASS. Compare the actual
# output to EXPECTED and paste both back. Check 1 (TUI) and Check 4 (Tauri) need your eyes on
# a pane/window — those print guided steps. Read-only against your repo; runs tiny throwaway
# tasks in a scratch dir.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
HM="./hm"
LOG_DIR="${HERMES_MAX_LOG_DIR:-$HOME/.hermes-max}"
LIVE_LOG="${LOG_DIR}/live.log"
UI_PORT="${HM_UI_PORT:-7080}"
SCRATCH="$(mktemp -d /tmp/obs-verify.XXXXXX)"
WHICH="${1:-all}"

say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
exp(){ printf '\033[1;33mEXPECTED:\033[0m %s\n' "$*"; }
note(){ printf '  • %s\n' "$*"; }

preflight(){
  say "PREFLIGHT"
  command -v hermes >/dev/null 2>&1 && note "hermes: $(command -v hermes)" || note "✗ hermes NOT on PATH"
  command -v tmux   >/dev/null 2>&1 && note "tmux: yes" || note "✗ tmux missing (cockpit/headless need it)"
  command -v curl   >/dev/null 2>&1 && note "curl: yes" || note "✗ curl missing (Check 3/5 need it)"
  : "${VLLM_BASE_URL:=http://100.72.56.36:8001/v1}"; export VLLM_BASE_URL
  note "VLLM_BASE_URL=${VLLM_BASE_URL}"
  if curl -s --max-time 5 "${VLLM_BASE_URL%/}/models" >/dev/null 2>&1; then
    note "vLLM /models reachable ✓"
  else
    note "✗ vLLM not reachable from THIS shell — Check 1/2/5 live runs will not produce tokens"
  fi
  note "scratch task dir: ${SCRATCH}"
}

# ── CHECK 1: native hermes TUI intact AND livelog gets tokens + conductor events ──
check1(){
  say "CHECK 1 — hermes TUI not swallowed + livelog tap co-exist (HermesWorker path)"
  note "Launching a tiny task HEADLESS into a tmux window so hermes owns a real TTY there."
  note "Watch that window: you should see hermes's native TUI (tokens, tool calls)."
  : > "${SCRATCH}/c1_tail.txt"
  ( tail -n0 -F "${LIVE_LOG}" 2>/dev/null > "${SCRATCH}/c1_tail.txt" & echo $! > "${SCRATCH}/c1_tail.pid" )
  ( cd "${SCRATCH}" && HM_HEADLESS=1 "${HM}" run "write a file hello.py with a function add(a,b) returning a+b and a pytest test_add; run pytest" )
  note "A tmux window 'run-…' was created. Attach and WATCH the native TUI render:"
  note "    tmux attach -t hermes-max    (or: tmux ls; tmux attach -t <session>)"
  note "Let it run ~30-60s, then re-run this with arg 1 again, or inspect the tail:"
  sleep 2 2>/dev/null || true
  [ -f "${SCRATCH}/c1_tail.pid" ] && kill "$(cat "${SCRATCH}/c1_tail.pid")" 2>/dev/null || true
  echo "  --- livelog lines captured during the run (grep gen.token / conductor) ---"
  grep -aE 'gen\.token|gen\.reasoning|conductor\.' "${SCRATCH}/c1_tail.txt" 2>/dev/null | head -20 || true
  echo "  --- raw event-type counts ---"
  grep -aoE '"(gen\.token|gen\.reasoning|gen\.thinking)"|conductor\.[a-z_]+' "${SCRATCH}/c1_tail.txt" 2>/dev/null | sort | uniq -c | head
  exp "In the tmux window: hermes's native TUI renders normally (NOT a blank/captured screen)."
  exp "Here: live.log shows conductor.* events (llm_call/verify_pass/file_write...) AND, if this"
  exp "hermes build exposes stream_delta_callback, gen.token lines too. Both arriving = the tap"
  exp "co-exists with the TUI (separate channels). If gen.token is ABSENT but conductor.* is"
  exp "present: TUI intact + discrete events flow, but per-token UI streaming is unavailable on"
  exp "this hermes build (the known limitation) — report that precisely."
}

# ── CHECK 2: cheap-path cockpit (livelog) clean + unblocked in tmux ──
check2(){
  say "CHECK 2 — cheap-path cockpit livelog, one clean line per event"
  note "Tailing the cockpit formatter over the next run's events:"
  ( cd "${SCRATCH}" && HM_HEADLESS=1 "${HM}" run "create util.py with slugify(s) lowercasing and hyphenating; add test; pytest" )
  sleep 2 2>/dev/null || true
  echo "  --- last 25 cockpit/live.log lines ---"
  if [ -x "${REPO}/scripts/cockpit_livelog.py" ] || [ -f "${REPO}/scripts/cockpit_livelog.py" ]; then
    tail -n 25 "${LIVE_LOG}" 2>/dev/null | python3 "${REPO}/scripts/cockpit_livelog.py" --replay 2>/dev/null || tail -n 25 "${LIVE_LOG}"
  else
    tail -n 25 "${LIVE_LOG}" 2>/dev/null
  fi
  exp "One clean line per event: plan / execute / verify / (escalation) / checkpoint, each with"
  exp "provider·model·backend where applicable. In a real 'hm dev' tmux cockpit the watch pane"
  exp "shows these WHILE the hermes pane renders — neither blocks the other."
}

# ── CHECK 3: web UI — run appears <1s, tokens+events merge, terminal run visible ──
check3(){
  say "CHECK 3 — web UI live within ~1s, two-source merge, terminal-launched run visible"
  note "Ensuring 'hm ui' is up on :${UI_PORT} (start it in another pane: ./hm ui)…"
  if ! curl -s --max-time 3 "http://127.0.0.1:${UI_PORT}/api/status" >/dev/null 2>&1; then
    note "UI not up — starting it backgrounded for this check"
    ( "${HM}" ui --no-open --port "${UI_PORT}" >/tmp/obs_ui.log 2>&1 & echo $! > "${SCRATCH}/ui.pid" )
    sleep 3 2>/dev/null || true
  fi
  local t0 rid
  t0=$(date +%s.%N)
  ( cd "${SCRATCH}" && HM_HEADLESS=1 "${HM}" run "add a function fib(n) to math2.py with a memoized impl and a pytest; pytest" ) >/dev/null 2>&1 || true
  # poll /api/runs for the newest running run
  for _ in 1 2 3 4 5; do
    rid="$(curl -s --max-time 3 "http://127.0.0.1:${UI_PORT}/api/runs" 2>/dev/null \
           | python3 -c 'import sys,json;d=json.load(sys.stdin);r=d.get("runs",d) if isinstance(d,dict) else d;print((r[0]["run_id"]) if r else "")' 2>/dev/null)"
    [ -n "$rid" ] && break; sleep 0.5 2>/dev/null || true
  done
  printf '  newest run_id on /api/runs: %s  (appeared after %.1fs)\n' "${rid:-<none>}" "$(echo "$(date +%s.%N) - $t0" | bc 2>/dev/null || echo '?')"
  if [ -n "${rid:-}" ]; then
    echo "  --- first ~3s of SSE /api/events/${rid} ---"
    curl -s --max-time 4 -N "http://127.0.0.1:${UI_PORT}/api/events/${rid}" 2>/dev/null | head -40
  fi
  [ -f "${SCRATCH}/ui.pid" ] && kill "$(cat "${SCRATCH}/ui.pid")" 2>/dev/null || true
  exp "The run_id appears on /api/runs within ~1s of launch (terminal-launched runs ARE"
  exp "registered). The SSE stream shows, keyed by run_id with monotonic id:, a MERGED feed:"
  exp "  event: conductor (llm_call/verify_pass/checkpoint...) AND event: gen.token deltas."
  exp "If the run does NOT appear: shell-integration/registry gap — report it."
}

# ── CHECK 5: each backend surfaces in the feed + cost ledger with the right label ──
check5(){
  say "CHECK 5 — backend × surface + cost attribution"
  note "Runs one tiny task per backend by switching mode, then reads the cost ledger + SSE."
  note "Real modes (./hm mode --list): local→local-executor(Qwen); full→cloud-conductor(V4-Pro"
  note "plans)+cloud-flash(V4-Flash executes); frontier→Opus plans (needs an Opus key)."
  note "Default sweep below = 'local full'; pass args to add frontier: ./verify_observability.sh 5 local full frontier"
  for MODE in "${@:-local full}"; do
    note "── mode: ${MODE} ──"
    "${HM}" mode "${MODE}" >/dev/null 2>&1 || { note "mode ${MODE} not switchable — skipping"; continue; }
    ( cd "${SCRATCH}" && HM_HEADLESS=1 "${HM}" run "write ok_${MODE//[^a-z]/}.py with f()=42 and a test; pytest" ) >/dev/null 2>&1 || true
    sleep 2 2>/dev/null || true
  done
  echo "  --- cost ledger by backend (hm cost / cost.db) ---"
  "${HM}" cost 2>/dev/null | head -20 || true
  if command -v sqlite3 >/dev/null 2>&1 && [ -f "${LOG_DIR}/cost.db" ]; then
    sqlite3 "${LOG_DIR}/cost.db" "SELECT backend, count(*), round(sum(cost_usd),6) FROM calls GROUP BY backend;" 2>/dev/null
  fi
  echo "  --- backend labels seen in recent livelog events ---"
  grep -aoE '"backend": *"[a-z-]+"|"provider": *"[a-z_]+"' "${LIVE_LOG}" 2>/dev/null | sort | uniq -c | tail -20
  exp "Each backend you exercised (local / cloud-deepseek / cloud-frontier) shows up in the cost"
  exp "ledger with a non-zero cost for cloud (\$0 for local/fabric) and a correct backend tag,"
  exp "AND its label appears in live.log + (via the SSE feed → web/Tauri) after the feeds.py fix."
  exp "Any backend invisible in any surface = report which surface + which backend."
}

case "$WHICH" in
  all) preflight; check1; check2; check3; check5 ;;
  1) preflight; check1 ;;
  2) preflight; check2 ;;
  3) preflight; check3 ;;
  5) shift || true; preflight; check5 "$@" ;;
  *) echo "usage: $0 [all|1|2|3|5]"; exit 2 ;;
esac

say "DONE — paste the output above (the lines under each section + EXPECTED) back to Claude."
note "Check 4 (Tauri parity): run './hm studio', open the SAME run, confirm tokens+events appear"
note "with parity to the web UI within ~1s. Report any divergence. (Visual — no script output.)"
note "scratch dir left at ${SCRATCH} (rm -rf when done)"
