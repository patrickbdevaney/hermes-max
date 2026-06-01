#!/usr/bin/env bash
# ui-actuation-test.sh — PART V: prove the web UI ACTUATES the agent (not just
# watches it). Drives a real bounded turn through the UI's own API headlessly,
# asserts every action surfaced in the SSE visual-flow, real artifacts landed on
# disk, verify went green, the handback fired, and a follow-up turn actuated — with
# zero secret leakage. This is the standing regression check that the UI is a
# complete, equivalent replacement for `hm dev` + `hermes`.
#
#   bash scripts/ui-actuation-test.sh   (or: hm ui-test)
#
# Runs in --mode free (needs OPENROUTER_API_KEY + a reachable vLLM driver; no paid
# call). PRECONDITION: the agent must be provider-wired (run `hm up` once so the
# hermes config points at the driver) — the test reports clearly if it is not.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${HMX_UI_TEST_PORT:-7099}"
TURN_TIMEOUT="${HMX_UI_TEST_TIMEOUT:-240}"

c_ok(){ printf '  \033[32m✓\033[0m %s\n' "$*"; }
c_bad(){ printf '  \033[31m✗\033[0m %s\n' "$*"; }
c_info(){ printf '  • %s\n' "$*"; }

PROJ="$(mktemp -d)"
( cd "$PROJ" && git init -q && git config user.email ui-test@hermes-max && git config user.name ui-test )
echo "═══ UI actuation equivalence test ═══"
c_info "project: ${PROJ}   port: ${PORT}   mode: free"

# Precondition: the driver must be reachable (else actuation can't happen).
DRIVER_STATE="$(cd "$REPO_ROOT" && python3 -c "import sys;sys.path.insert(0,'.');from ui.server import feeds;print(feeds.driver_status()['state'])" 2>/dev/null)"
c_info "driver: ${DRIVER_STATE:-unknown}"
if [ "${DRIVER_STATE}" = "none" ]; then
  c_bad "no driver reachable — run 'hm up' to bring up the stack first"; rm -rf "$PROJ"; exit 1
fi

# Start the UI server (the SAME server the browser uses), no browser. HMX_UI_VERIFY
# turns on hermes-max's full plan→verify→checkpoint discipline so the run is gated by
# a REAL pytest verify and a REAL git checkpoint (off by default for normal UI runs,
# which never auto-run tests or commit the user's repo).
( cd "$REPO_ROOT" && HMX_UI_NO_OPEN=1 HMX_UI_VERIFY=1 python3 -m ui.server --no-open --port "$PORT" ) \
  >"${PROJ}/ui-server.log" 2>&1 &
SRV=$!
cleanup() {
  kill "$SRV" 2>/dev/null
  # reap any agent the run spawned in our temp project
  for pid in $(pgrep -f "yolo -z" 2>/dev/null); do kill "$pid" 2>/dev/null; done
}
trap cleanup EXIT

# Wait for readiness.
for _ in $(seq 1 30); do
  curl -s "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
  curl -s --retry 1 --retry-delay 1 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 || true
done
curl -s "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 || { c_bad "UI server did not start (see ${PROJ}/ui-server.log)"; exit 1; }
c_ok "UI server up on 127.0.0.1:${PORT}"

# Drive the real turns + assertions; the report lands in $PROJ.
( cd "$PROJ" && python3 "${REPO_ROOT}/scripts/ui_actuation_consumer.py" "$PORT" "$PROJ" "$TURN_TIMEOUT" )
RESULT=$?

echo
if [ "$RESULT" -eq 0 ]; then
  c_ok "PASS — the UI actuated the agent; every action surfaced; artifacts on disk; follow-up turn fired."
else
  c_bad "FAIL — see ${PROJ}/ui_actuation_report.md and ${PROJ}/ui-server.log"
  c_info "if the agent reported 'No LLM provider configured', wire it with: hm up   (then re-run)"
fi
c_info "report: ${PROJ}/ui_actuation_report.md"
exit "$RESULT"
