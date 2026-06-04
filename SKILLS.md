# SKILLS.md — Agent context for hermes-max

Read by the conductor (`conductor_core._load_skills_md`) and injected into every plan
for tasks run in this repo. Keep it short (first ~1500 chars are used).

## Architecture decisions already made (do NOT relitigate)
- The conductor/planner is `mcp-escalation/conductor_core.py:conductor_plan(task, cwd)`.
  There is NO `orchestration/`, `llm/`, or `tools/` package — do not assume them.
- The inference fabric is `lib/inference/` (`router.run_role`, `adapters`, `roles`,
  `config`). MCP servers reach models over HTTP only — never import torch/CUDA in a server.
- The executor is the native `hermes` binary calling the local vLLM via `~/.hermes/config.yaml`;
  it does NOT route through `lib/inference`. Conductor + MCP helpers DO use `lib/inference`.
- Repo awareness comes from `mcp-scopemap` (`get_repo_map` / `request_context`), already
  injected into planning. Don't build a second repomap.
- Thinking budget: the Qwen3 vLLM ignores the soft `thinking_budget`; cap via `max_tokens`
  (HTTP-gated in `lib/inference/router`). CoT spirals are caught by the conductor plugin.

## Protected files (do NOT modify without explicit instruction)
- The `hermes` binary and `~/.hermes/config.yaml` (the live native-agent config).
- `~/.hermes-max/` runtime state, `~/.hermes/plugins/conductor` (symlinked to `plugins/conductor`).

## Test commands
- Inference fabric: `python3 lib/inference/smoke_inference.py`
- Conductor plugin: `python3 plugins/conductor/smoke_enforce.py`
- Per-MCP smoke: `python3 mcp-<name>/smoke_test.py` (sourcing `.env` first)
- A server's health: `curl -s localhost:<port>/health`

## Key entry points
- CLI: `hm` (bash) → `hm run` / `hm native` → `scripts/preplan.py` → `conductor_plan`.
- Turn-1 planning is also forced in-process by `plugins/conductor/__init__.py:_pre_llm_call`.
