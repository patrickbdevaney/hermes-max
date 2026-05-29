# dspy-evolution

A weekly cron wrapper around the **official** `hermes-agent-self-evolution`
module (DSPy + GEPA). It optimizes your most-used skills/prompts against
accumulated session history — the clean, sanctioned evolution path. No
MAP-Elites / ADAS / OMNI-EPIC archive process (that's Lane 3).

## Status in this install

`hermes-agent-self-evolution` ships as a **separate repo** and is **not bundled**
with Hermes v0.15.1. The wrapper detects this and **skips gracefully (exit 0)**
with install instructions, so the weekly schedule stays healthy until you
install the package:

```bash
git clone <hermes-agent-self-evolution repo>
python3 -m pip install -e <that repo>
```

Once installed, the wrapper autodetects it (CLI on PATH or importable module)
and runs it weekly. Tune via env vars documented at the top of
`run-evolution.sh` (`DSPY_PYTHON`, `DSPY_EVOLVE_CMD`, `DSPY_EVOLVE_ARGS`,
`DSPY_TIMEOUT`).

## Files

- `run-evolution.sh` — the worker. Loads `.env`, detects the package, runs it
  against `~/.hermes/sessions` + `~/.hermes/skills`, logs to
  `~/.hermes-max/dspy-evolution/`, and **never hard-fails the cron**.
- `register-cron.sh` — installs the worker into `~/.hermes/scripts/` and creates
  a weekly Hermes cron job (`--no-agent`, stdout delivered to the operator).
  Idempotent.

## Wire the weekly cron

```bash
./register-cron.sh          # default: Sundays 04:00, deliver=local
# or customize:
DSPY_SCHEDULE='0 4 * * 0' DSPY_DELIVER=telegram ./register-cron.sh
```

This uses Hermes's native cron (`hermes cron create`) — no Temporal, no
LangGraph, no external scheduler.
