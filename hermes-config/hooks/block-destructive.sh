#!/usr/bin/env bash
# Optional Hermes pre_tool_call hook: block obviously-destructive terminal
# commands during unattended runs. Belt-and-suspenders for overnight autonomy
# (the spec's Lane-1 posture: non-root user + sandboxed workdir + allow-lists).
#
# Wire it (opt-in) in ~/.hermes/config.yaml:
#   hooks:
#     pre_tool_call:
#       - matcher: "terminal"
#         command: "~/.hermes/agent-hooks/block-destructive.sh"
#         timeout: 5
#
# Protocol: reads a JSON event on stdin, prints {"action":"block",...} to veto.
# Printing nothing / {} allows the call. Fails OPEN (allows) on any parse error
# so a hook bug never wedges the agent.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

# Extract the command field without requiring jq (best-effort).
cmd="$(printf '%s' "${payload}" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get("tool_input") or {}).get("command", ""))
except Exception:
    print("")
' 2>/dev/null || true)"

# Dangerous patterns. Conservative: only the clearly-catastrophic ones.
deny_regex='rm[[:space:]]+(-[a-zA-Z]*[rR][a-zA-Z]*[[:space:]]+)?(-[a-zA-Z]*[fF][a-zA-Z]*[[:space:]]+)?(/|~|\*|\$HOME)([[:space:]]|$)|mkfs|dd[[:space:]]+if=.*of=/dev/|:\(\)\{|>[[:space:]]*/dev/sd|chmod[[:space:]]+-R[[:space:]]+777[[:space:]]+/|git[[:space:]]+push.*((--force|-f)([[:space:]].*)?(main|master)|(main|master)([[:space:]].*)?(--force|-f))'

if printf '%s' "${cmd}" | grep -Eq "${deny_regex}"; then
  printf '{"action":"block","message":"hermes-max: refused a destructive command (%s). Confirm with the operator if intended."}\n' \
    "$(printf '%s' "${cmd}" | head -c 80 | sed 's/"/\\"/g')"
  exit 0
fi

# Allow.
echo '{}'
exit 0
