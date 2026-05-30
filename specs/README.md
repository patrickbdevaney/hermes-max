# specs/ — hermes-max build specs (for the record)

The `CLAUDE_*.md` files here are the staged build/upgrade specifications each
implemented as its own pass against the harness. They are kept for provenance and
context — they are **not** runtime config and nothing in the stack reads them.

| spec | pass |
|------|------|
| `CLAUDE_hermes_max.md` | the base maximally-capable harness |
| `CLAUDE_harness_max.md` / `CLAUDE_harness_compounding.md` | harness + compounding-knowledge layer |
| `CLAUDE_longhorizon.md` | long-horizon autonomy scaffolding |
| `CLAUDE_conductor.md` | the tiered conductor (steer/synth/escalate) |
| `CLAUDE_research_engine.md` | the deep-research engine |
| `CLAUDE_bifurcate_search.md` | verifier-guided best-of-N search |
| `CLAUDE_system_validation.md` | system validation + emergent-behavior eval |
| `CLAUDE_finalize.md` | finalize fixes |
| `CLAUDE_reliability_observability.md` | per-tool budgets, robust index init, live observability, lifecycle, snapshots, bottleneck analysis, the `hm` launcher |
