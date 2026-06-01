# archive/specs/ — the build specs that produced hermes-max

These `CLAUDE_*.md` files are the staged build/upgrade specifications, each
implemented as its own pass against the harness. They are kept for **provenance
and context only**.

> You do **not** need to read any of these to use hermes-max. Start with the
> [README](../../README.md) and [QUICKSTART](../../QUICKSTART.md). Nothing in the
> running stack reads these files.

They are listed roughly in build order:

| spec | pass |
|------|------|
| `CLAUDE_hermes_max.md` | the base maximally-capable harness |
| `CLAUDE_harness_max.md` / `CLAUDE_harness_compounding.md` | harness + compounding-knowledge layer |
| `CLAUDE_longhorizon.md` | long-horizon autonomy scaffolding |
| `CLAUDE_conductor.md` | the tiered conductor (steer / synth / escalate) |
| `CLAUDE_research_engine.md` | the deep-research engine |
| `CLAUDE_bifurcate_search.md` | verifier-guided best-of-N search |
| `CLAUDE_system_validation.md` | system validation + emergent-behavior eval |
| `CLAUDE_finalize.md` | finalize fixes |
| `CLAUDE_reliability_observability.md` | per-tool budgets, robust index init, live observability, lifecycle, snapshots, bottleneck analysis, the `hm` launcher |
| `CLAUDE_plan_execute.md` | the expensive-plan → cheap-execute split |
| `CLAUDE_inference_fabric.md` | the role→provider seam, config trinity, `hm mode` |
| `CLAUDE_frontier_tier.md` | the optional Opus frontier escalation tier |
| `CLAUDE_research_health_and_eval_battery.md` | research rationing, health checks, the agent eval battery |
| `CLAUDE_repo_elegance.md` | this repo/docs/ergonomics refactor |

The current system on disk — the `hm` CLI, the MCP servers, the skills, the
inference fabric — is the only source of truth for what exists today. These specs
describe how it got there.
