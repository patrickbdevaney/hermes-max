# archive/traces/ — captured validation artifacts

Point-in-time outputs from development validation runs, kept for the record.
They are **not** live documentation and may reference earlier model rosters or
configs. Regenerate current equivalents with the `hm` verbs:

| trace | regenerate with |
|-------|-----------------|
| `dry_run_trace.md` | `hm preflight` / the dry-run harness |
| `rate_limit_validation_trace.md` | `hm health` (rate-limit probe) |
| `bottleneck_report.md` | `hm bottleneck` |
| `deep_research_trace.md` | the `deep-research` skill / `hm eval research` |
| `emergent_eval_report.md` | `hm eval` (emergent-behavior battery) |
| `eval_battery_report.md` | `hm eval` |
