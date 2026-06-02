#!/usr/bin/env bash
# status.sh — the human view of the whole stack (Stage 5).
#
# For every server in the manifest: UP/DOWN, port, PID, uptime, last health
# result. One glance shows the whole stack's state. (Distinct from healthcheck.sh,
# which is pass/fail for scripting; status.sh is for a human.) Manifest-driven.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; Z=$'\033[0m'
else G=""; R=""; D=""; Z=""; fi

echo "═══ hermes-max status ═══  profile=${HMX_PROFILE}  host=$(hmx_bind_host)"
printf '%-16s %-5s %-7s %-8s %-9s %s\n' "server" "port" "state" "pid" "uptime" "health"
printf '%s\n' "$(printf '─%.0s' $(seq 1 64))"

UP=0; TOTAL=0
for name in "${HMX_ACTIVE_SERVERS[@]}"; do
  TOTAL=$((TOTAL+1))
  port="$(hmx_port "${name}")"
  pidfile="$(hmx_pidfile "${name}")"
  pid="$(cat "${pidfile}" 2>/dev/null || true)"
  # Prefer the pidfile PID; fall back to whoever holds the port.
  if ! hmx_pid_alive "${pid}"; then pid="$(hmx_port_pids "${port}")"; fi
  uptime="$(hmx_pid_uptime "${pid%% *}")"
  if body="$(curl -fsS -m 3 "$(hmx_health_url "${name}")" 2>/dev/null)"; then
    UP=$((UP+1))
    state="${G}UP${Z}"
    # pull a short field from the health JSON if present
    hb="$(printf '%s' "${body}" | grep -oE '"status"[ ]*:[ ]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
    health="${D}${hb:-ok}${Z}"
  elif [ -n "${pid}" ]; then
    state="${R}DOWN${Z}"; health="${D}process up, health failing${Z}"
  else
    state="${R}DOWN${Z}"; health="${D}not running${Z}"
  fi
  printf '%-16s %-5s %-16b %-8s %-9s %b\n' \
    "${HMX_DIR[$name]#mcp-}" "${port}" "${state}" "${pid:-—}" "${uptime:-—}" "${health}"
done
printf '%s\n' "$(printf '─%.0s' $(seq 1 64))"
echo "${UP}/${TOTAL} servers up"

# ── readiness (informational) ──────────────────────────────────────────────
# Liveness above answers UP/DOWN. Readiness is the SEPARATE question "are this
# server's optional dependencies reachable?" — a failing dependency is a WARNING
# here, NEVER a DOWN (the server is live and its tools degrade gracefully). Only
# servers that expose /ready (research, docs) print a line; others are skipped.
# Bounded per-probe timeout so this never blocks the status view. Skip with
# HMX_NO_READINESS=1.
if [ -z "${HMX_NO_READINESS:-}" ]; then
  printed_hdr=""
  for name in "${HMX_ACTIVE_SERVERS[@]}"; do
    rurl="http://$(hmx_bind_host):$(hmx_port "${name}")/ready"
    rbody="$(curl -fsS -m 4 "${rurl}" 2>/dev/null)" || continue   # no /ready or unreachable → skip
    summary="$(printf '%s' "${rbody}" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
parts=[]
for k,v in d.items():
    if k.endswith("_up"):
        parts.append(("%s " % k[:-3]) + ("✓" if v else "✗"))
    elif k.endswith("_reachable"):
        parts.append(("%s " % k[:-10]) + ("✓" if v else "✗"))
src=d.get("sources") or {}
reg=src.get("registered") if isinstance(src,dict) else None
if isinstance(reg,list): parts.append("sources %d" % len(reg))
cm=d.get("chat_model")
if isinstance(cm,str): parts.append("chat " + ("set" if "unset" not in cm else "deterministic"))
print(" · ".join(parts))
' 2>/dev/null)"
    [ -z "${summary}" ] && continue
    [ -z "${printed_hdr}" ] && { echo "── readiness (informational · deps, not UP/DOWN) ──"; printed_hdr=1; }
    printf '  %s%-14s%s %s\n' "${D}" "${HMX_DIR[$name]#mcp-}" "${Z}" "${summary}"
  done
fi

# ── conductor (cloud tiers + active mode + frontier spend) ───────────────────
# The MODE is the cloud-tier CEILING set by `hm up --local|--free|--full|--frontier`.
MODE_FILE="${HERMES_MAX_STATE_DIR:-${HOME}/.hermes-max}/conductor/mode"
MODE_FILE="${MODE_FILE/#\~/$HOME}"
CMODE="${CONDUCTOR_MODE:-}"
[ -z "${CMODE}" ] && [ -f "${MODE_FILE}" ] && CMODE="$(cat "${MODE_FILE}" 2>/dev/null)"
CMODE="${CMODE:-full}"
_kp() { local k="$1"; [ -n "${!k:-}" ] || grep -qE "^${k}=[^[:space:]#]" "${REPO_ROOT}/.env" 2>/dev/null; }
tiers=""
case "${CMODE}" in
  local) tiers="(cloud OFF — fully sovereign)" ;;
  free|full|frontier)
    _kp DEEPINFRA_API_KEY && tiers="${tiers} deepinfra"
    _kp CEREBRAS_API_KEY && tiers="${tiers} cerebras"; _kp GROQ_API_KEY && tiers="${tiers} groq"
    _kp GEMINI_API_KEY && tiers="${tiers} gemini"; _kp DEEPSEEK_API_KEY && tiers="${tiers} deepseek"
    [ "${CMODE}" = "free" ] && tiers="$(echo "${tiers}" | sed 's/ deepinfra//; s/ deepseek//')"
    [ "${CMODE}" = "frontier" ] && { _kp ANTHROPIC_API_KEY && tiers="${tiers} opus-4.8" || tiers="${tiers} (opus OFF: no key)"; }
    ;;
esac
echo "── conductor (cloud tiers) ──"
echo "  ${D}mode${Z} ${CMODE}  ·  ${D}live tiers${Z}${tiers:- (none present)}"
# Synth (planner) cascade: the ordered free→paid rungs, present-gated. A 429 on a
# free rung falls through to the next before any paid token is spent.
CONDUCTOR_MODE="${CMODE}" python3 - "${REPO_ROOT}" <<'PY' 2>/dev/null || true
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "mcp-escalation"))
try:
    import conductor_registry as reg, conductor_resolver as res
except Exception:
    sys.exit(0)
cfg = reg.load_config(); env = dict(os.environ)
chain = cfg["role_chains"].get("synth", [])
allowed = set(res.resolve_chain(chain, cfg["providers"], env))
free = sum(1 for p in chain if cfg["providers"].get(p, {}).get("tier") == "free")
paid = sum(1 for p in chain if cfg["providers"].get(p, {}).get("tier") == "paid")
# plan/exec/cost summary, mode-aware (the executor + cost come from the FABRIC mode).
sys.path.insert(0, sys.argv[1])
fmode = exec_line = cost_line = ""; ceiling = ""
try:
    from lib.inference import roles
    fmode = roles.active_mode_name()
    meta = roles.mode_meta(fmode)
    ceiling = meta.get("inference_mode", "")
    b = roles.executor_backend(fmode)
    host = (b.get("base_url") or "").split("//", 1)[-1].split("/", 1)[0]
    loc = "local" if b.get("local") else "cloud"
    if b.get("local") and host and not host.startswith(("localhost", "127.0.0.1")):
        loc = "remote"
    exec_line = f"{b.get('provider', '?')} ({loc}){(' @ ' + host) if host else ''}  {b.get('model_id', '')}"
    cost_line = meta.get("monthly_cost", "?")
except Exception:
    pass
# plan kind reflects the CEILING (free → no paid fallback; full/frontier → V4-Pro fallback).
if fmode == "full-local":
    plan_kind = "V4-Pro first (paid), free cascade fallback"
elif ceiling == "free":
    plan_kind = "free cascade ($0, no paid fallback)"
elif ceiling in ("full", "frontier"):
    plan_kind = "free cascade → V4-Pro fallback"
elif ceiling == "local":
    plan_kind = "local only"
else:
    plan_kind = "free cascade"
print(f"  plan   {plan_kind}")
if exec_line:
    print(f"  exec   {exec_line}")
if cost_line:
    print(f"  cost   {cost_line}/mo  ·  $0 while the free planner tier has capacity")
print(f"  synth cascade  ({free} free → {paid} paid; 429 falls through):")
for i, pid in enumerate(chain, 1):
    p = cfg["providers"].get(pid, {})
    tier = p.get("tier", "?"); model = p.get("models", {}).get("synth", "?")
    mark = "\033[32m●\033[0m" if pid in allowed else "\033[2m○\033[0m"
    print(f"    {i} {mark} {pid:<22} {tier:<5} {model}")
PY
FRONTIER_STATE="${HERMES_MAX_STATE_DIR:-${HOME}/.hermes-max}/conductor/frontier.json"
FRONTIER_STATE="${FRONTIER_STATE/#\~/$HOME}"
if [ "${CMODE}" = "frontier" ] && [ -f "${FRONTIER_STATE}" ]; then
  python3 - "${FRONTIER_STATE}" "${FRONTIER_USD_CAP_MONTHLY:-10}" "${FRONTIER_USD_CAP_DAILY:-2}" "${FRONTIER_TARGET_CALLS_MONTHLY:-15}" <<'PY' 2>/dev/null || true
import json,sys
st=json.load(open(sys.argv[1])); capm,capd,tgt=sys.argv[2],sys.argv[3],sys.argv[4]
print(f"  frontier Opus: {st.get('calls_month',0)}/{tgt} calls this month  ·  "
      f"${st.get('spend_month',0):.2f}/${capm} mo  ${st.get('spend_today',0):.2f}/${capd} day")
PY
fi

echo "── supporting (informational) ──"
hmx_phoenix_otlp_ok && echo "  ${G}✓${Z} Phoenix OTLP ${PHOENIX_COLLECTOR_ENDPOINT:-http://localhost:4317}" \
                    || echo "  ${D}• Phoenix OTLP down (./phoenix.sh)${Z}"
for pair in "embed:${EMBED_PORT:-8002}" "rerank:${RERANK_PORT:-8003}"; do
  svc="${pair%%:*}"; port="${pair##*:}"
  [ -n "$(hmx_port_pids "${port}")" ] && echo "  ${G}✓${Z} ${svc} serve (port ${port})" \
                                      || echo "  ${D}• ${svc} serve down (serve-${svc}.sh)${Z}"
done
[ "${UP}" -eq "${TOTAL}" ] && exit 0 || exit 1
