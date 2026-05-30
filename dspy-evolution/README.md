# dspy-evolution

The **compounding loop**: weekly **GEPA** (reflective prompt evolution, via DSPy)
that reads accumulated traces and evolves the difficulty-classifier prompt (and,
over time, skill/critic prompts) on the operator's OWN tasks. Runs entirely on
the local model (`$VLLM_BASE_URL`) as **both** the task LM and the reflection LM —
no cloud, no API key. Bounded by `MAX_METRIC_CALLS` so a run is minutes-to-hours,
never days. No MAP-Elites / ADAS archive process.

## How it runs (env-isolated, out-of-process)

`hermes-agent-self-evolution` ships separately and isn't bundled with Hermes; we
do **not** pip into Hermes's env. Instead this dir owns its **own `.venv`** with
`dspy` + `gepa`, and the job runs out-of-process: it reads traces by FILE PATH
and writes evolved prompts by FILE PATH — it never imports Hermes.

## Target & metric

- **Target:** the difficulty classifier (`mcp-escalation.classify_difficulty`)
  gates search depth, verify depth, and escalation across the whole stack, so
  improving it lifts everything. The deployed classifier is rule-based; GEPA
  evolves an **LLM classifier prompt** to replicate/extend that policy where
  signals are fuzzy, emitting a versioned variant.
- **Metric:** `dspy.Prediction(score, feedback)` — score = label match;
  `feedback` = the actual reason it was wrong (GEPA's Actionable Side Info).

## Trace sources (`traces.py`)

1. `~/.hermes-max/escalation/outcomes.jsonl` — the **flywheel**: every time a task
   escalates and the higher tier solves it, `mcp-escalation.record_outcome`
   appends a labelled example, so each escalation becomes training signal and the
   local model handles more of the formerly-escalated band over time.
2. `~/.hermes/state.db` — the Hermes session store (assistant-turn effort proxy).
3. A built-in **seed** set — so the machinery runs and is demonstrable before real
   traces accumulate. `real_trace_count()` counts only (1)+(2) for honest gating.

## Output (never overwrites)

A new versioned variant under `~/.hermes/skills/hermes-max/classify-difficulty-prompt/`
(`evolved.v{N}.md` + `.json` audit with before/after/lift), A/B-able against the
prior version. Before/after Pareto scores are recorded to the KG (`gepa_run`
entity) and OTel spans (`gepa_run_started/completed`, `skill_evolved`).

## Run

```bash
bash run-evolution.sh           # scheduled mode: GATED on real traces
                                #   < MIN_REAL_TRACES (default 10) ⇒ graceful no-op
bash run-evolution.sh --seed    # force a demo run on the seed set
./register-cron.sh              # weekly Hermes-native cron (Sundays 04:00)
```

Config (all optional): `VLLM_BASE_URL`, `MAX_METRIC_CALLS` (default 50),
`MIN_REAL_TRACES` (default 10), `DSPY_TIMEOUT` (default 3600).
The cron job **never hard-fails**: missing dspy/gepa ⇒ install + exit 0.
