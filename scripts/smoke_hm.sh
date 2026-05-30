#!/usr/bin/env bash
# smoke_hm.sh — prove BOTH invocation styles are first-class (Stage 8):
#   (a) every scripts/*.sh runs standalone exactly as before, AND
#   (b) the matching `hm <verb>` dispatches to that same script.
# Neither is privileged; `hm` is sugar, the scripts are the substance.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HM="${REPO_ROOT}/hm"
SCRIPTS="${REPO_ROOT}/scripts"
fail=0
ok()  { echo "  ok: $*"; }
bad() { echo "  FAIL: $*"; fail=1; }

echo "[hm] dispatch + standalone equivalence"

# verb -> the script it must map to (HM_PRINT echoes the resolved command)
declare -A MAP=(
  [up]="start-all.sh" [down]="stop-all.sh" [restart]="restart.sh"
  [status]="status.sh" [watch]="watch.sh" [health]="healthcheck.sh"
  [summary]="run-summary.sh" [bottleneck]="bottleneck-eval.sh"
  [snapshot]="snapshot-stores.sh" [restore]="restore-stores.sh"
)
for verb in "${!MAP[@]}"; do
  script="${MAP[$verb]}"
  # (a) the standalone script exists, is executable, and parses
  if [ -x "${SCRIPTS}/${script}" ] && bash -n "${SCRIPTS}/${script}"; then
    : # ok
  else
    bad "scripts/${script} not standalone-runnable"; continue
  fi
  # (b) hm <verb> maps to exactly that script
  out="$(HM_PRINT=1 "${HM}" "${verb}" 2>/dev/null || true)"
  case "${out}" in
    *"/scripts/${script}"*) ok "hm ${verb} → scripts/${script} (standalone ✓)" ;;
    *) bad "hm ${verb} mapped to '${out}', expected scripts/${script}" ;;
  esac
done

# logs / run / attach / dev map to the right targets too
[ "$(HM_PRINT=1 "${HM}" logs research)" = "tail -F ${HOME}/.hermes-max/logs/research.log" ] \
  && ok "hm logs <srv> → tail the server log" || bad "hm logs mapping wrong"
case "$(HM_PRINT=1 "${HM}" run 'do x')" in *"hermes do x"*) ok "hm run → hermes <task>" ;; *) bad "hm run mapping wrong" ;; esac

# no-arg prints usage; unknown verb is a helpful non-zero
"${HM}" >/tmp/hm_usage.$$ 2>&1; uexit=$?
grep -q "hm up" /tmp/hm_usage.$$ && [ "${uexit}" -eq 0 ] && ok "no-arg prints the verb list" || bad "no-arg usage missing"
rm -f /tmp/hm_usage.$$
"${HM}" definitely-not-a-verb >/dev/null 2>&1; [ "$?" -eq 2 ] && ok "unknown verb → exit 2 + usage" || bad "unknown verb not handled"

# tmux cockpit: present → spawn/verify/kill ; absent → manual-instruction degrade
if command -v tmux >/dev/null 2>&1; then
  HM_NO_ATTACH=1 "${HM}" dev >/dev/null 2>&1 || true
  panes="$(tmux list-panes -t hermes-max 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${panes}" -ge 3 ]; then ok "hm dev spawned a tmux cockpit (${panes} panes)"; else bad "hm dev cockpit panes=${panes} (<3)"; fi
  tmux kill-session -t hermes-max 2>/dev/null || true
else
  out="$("${HM}" dev 2>&1 || true)"
  case "${out}" in
    *"tmux is not installed"*"start-all.sh"*"watch.sh"*)
      ok "tmux absent → hm dev degrades to manual instructions (scripts unaffected)" ;;
    *) bad "hm dev tmux-absent degradation missing instructions: ${out}" ;;
  esac
fi

[ "${fail}" -eq 0 ] && echo "hm smoke test PASSED" || { echo "hm smoke test FAILED"; exit 1; }
