#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# dspy-evolution — the COMPOUNDING loop. Runs GEPA (reflective prompt evolution)
# OUT-OF-PROCESS in its OWN venv against accumulated traces, and writes evolved
# prompt variants by FILE PATH under ~/.hermes/skills/hermes-max/ (never imports
# Hermes, never overwrites a prior variant). Designed for weekly Hermes cron.
#
# Gating (honest): only counts REAL traces (escalation outcomes + session store).
# With fewer than MIN_REAL_TRACES it is a graceful no-op ("needs more traces") —
# optimising on no data is meaningless. Pass --seed to force a demo run on the
# built-in seed set (exercises the machinery; clearly flagged as seed).
#
#   bash run-evolution.sh           # scheduled mode: gated on real traces
#   bash run-evolution.sh --seed    # force a run (seed set) to demonstrate
#
# Config (all optional): VLLM_BASE_URL (the local model — task & reflection LM),
#   MAX_METRIC_CALLS (GEPA budget, default 50), MIN_REAL_TRACES (gate, default 10),
#   DSPY_TIMEOUT (default 3600).
# Never hard-fails the cron: missing dspy/gepa ⇒ install + exit 0.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
[ -f "${REPO_ROOT}/.env" ] && { set -a; . "${REPO_ROOT}/.env"; set +a; }

VENV="${SCRIPT_DIR}/.venv"
PY="${VENV}/bin/python"
REQ="${SCRIPT_DIR}/requirements.txt"
MIN_REAL_TRACES="${MIN_REAL_TRACES:-10}"
DSPY_TIMEOUT="${DSPY_TIMEOUT:-3600}"
SEED=0
[ "${1:-}" = "--seed" ] && SEED=1

STAMP="$(date +%Y%m%d_%H%M%S 2>/dev/null || echo run)"
echo "═══ dspy-evolution @ ${STAMP} ═══"
echo "VLLM_BASE_URL=${VLLM_BASE_URL:-<unset>}"

# ── self-bootstrap the venv (so this works even if Stage-0 bootstrap didn't) ──
if [ ! -x "${PY}" ]; then
  echo "→ creating dspy-evolution venv (one-time; dspy+gepa are heavy)…"
  python3 -m venv "${VENV}"
  "${PY}" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
fi
stamp="${VENV}/.requirements.sha"
cur="$(sha1sum "${REQ}" | awk '{print $1}')"
if [ "$(cat "${stamp}" 2>/dev/null)" != "${cur}" ]; then
  echo "→ installing dspy-evolution requirements…"
  "${PY}" -m pip install -q -r "${REQ}" && echo "${cur}" >"${stamp}" || true
fi

# ── graceful no-op if the packages still aren't importable ────────────────────
if ! "${PY}" -c "import dspy, gepa" >/dev/null 2>&1; then
  echo "• dspy/gepa not importable — skipping (no-op, exit 0)."
  echo "  install: ${PY} -m pip install -r ${REQ}"
  exit 0
fi

# ── gate on REAL trace count (unless --seed) ──────────────────────────────────
REAL="$(PYTHONPATH="${SCRIPT_DIR}" "${PY}" -c 'import traces; print(traces.real_trace_count())' 2>/dev/null || echo 0)"
echo "real traces available: ${REAL} (gate: ${MIN_REAL_TRACES})"
if [ "${SEED}" -eq 0 ] && [ "${REAL}" -lt "${MIN_REAL_TRACES}" ]; then
  echo "• needs more traces (${REAL} < ${MIN_REAL_TRACES}) — graceful no-op (exit 0)."
  echo "  Escalation outcomes + sessions accumulate over use; re-runs auto-trigger once enough exist."
  echo "  To demonstrate the machinery now: bash run-evolution.sh --seed"
  exit 0
fi

# ── run the bounded GEPA optimisation ─────────────────────────────────────────
TIMEOUT_BIN="$(command -v timeout || true)"
echo "→ running GEPA (target: classify_difficulty)…"
if [ -n "${TIMEOUT_BIN}" ]; then
  "${TIMEOUT_BIN}" "${DSPY_TIMEOUT}" env PYTHONPATH="${SCRIPT_DIR}" "${PY}" "${SCRIPT_DIR}/evolve.py"
else
  env PYTHONPATH="${SCRIPT_DIR}" "${PY}" "${SCRIPT_DIR}/evolve.py"
fi
echo "═══ done (rc=$?) ═══"
exit 0   # never hard-fail the cron
