# hermes-max — Lane 1 Sovereign Coder

A stock **Hermes Agent** (v0.15.1, on local **Qwen3.6-35B-A3B** via vLLM) powered
up into a maximally-capable, long-horizon autonomous engineering harness — built
to beat Claude Code + Opus on the axes a free, always-on, compounding local agent
structurally can:

- **Persistence / compounding** — a codebase RAG index + a knowledge graph + the
  self-improving skill library mean each task starts already knowing your stack.
- **Unlimited unattended time at $0 marginal cost** — 24/7 local grinding.
- **Deterministic verification gates** — the agent *cannot* declare done on red.
- **Total sovereignty + a superset tool suite** — five MCP servers on top of
  Hermes's 90 native skills.

It does this **without forking Hermes** — every capability is added through
native surfaces only: **MCP servers** (config), **skills** (markdown), **hooks**,
and **native settings**. Every new capability is an **independent process** with
its own healthcheck and standalone smoke test; killing any one degrades exactly
one tool and never crashes the agent (the anti-Frankenstein gate).

## Repo layout

```
hermes-max/
  .env.example            # VLLM_BASE_URL + ports/paths — the one swap point
  hermes-config/          # Tier-0 reference config + optional hooks (no Hermes source)
  skills/                 # Tier-2 markdown workflow skills
  mcp-verify/             # deterministic lint→typecheck→tests gate   (:9101)
  mcp-codebase-rag/       # hybrid BM25+dense retrieval over your repos (:9102)
  mcp-knowledge-graph/    # embedded SQLite triples store             (:9103)
  mcp-observability/      # OpenTelemetry → Phoenix                    (:9104)
  mcp-escalation/         # tiered router: classifier + local + cloud  (:9105)
  mcp-checkpoint/         # verified-green git checkpoint/revert+state  (:9106)
  mcp-watchdog/           # spiral/stall/progress/budget detection      (:9107)
  mcp-search/             # verifier-guided best-of-N selection         (:9108)
  dspy-evolution/         # weekly cron wrapper for self-evolution
  scripts/                # start-all / healthcheck / smoke-test / register-mcp
  phoenix.sh  searXNG.sh  # supporting containers (already provided)
```

## Quickstart

```bash
# 0. supporting containers (once)
./phoenix.sh        # Phoenix: OTLP :4317, UI :6006
./searXNG.sh        # SearXNG: :8080

# 1. config — the single port switch lives here
cp .env.example .env
#   edit VLLM_BASE_URL (dev = Tailscale IP, prod = localhost)

# 2. prove each server in isolation (creates venvs on first run)
scripts/smoke-test.sh

# 3. apply native deadline/effort config (backs up ~/.hermes/config.yaml first)
scripts/apply-config-deadlines.sh   # terminal.timeout=120, reasoning_effort=medium

# 4. start all eight MCP servers (independent processes)
scripts/start-all.sh

# 4. register with Hermes (injects mcp_servers, installs Tier-2 skills)
scripts/register-mcp.sh           # add --sync-model-url to also set model.base_url
#   then restart Hermes so it loads them:  hermes

# 5. confirm everything is live
scripts/healthcheck.sh

# 6. (optional) weekly self-evolution cron
dspy-evolution/register-cron.sh
```

Stop the servers with `kill $(cat ~/.hermes-max/run/*.pid)`. Logs are under
`~/.hermes-max/logs/`.

## Config contract (`.env`)

| Variable | Meaning |
|----------|---------|
| `VLLM_BASE_URL` | **The one port switch.** Dev=`http://YOUR_TAILSCALE_IP:8001/v1`, prod=`http://localhost:8001/v1`. Never hardcoded anywhere. |
| `EMBED_BASE_URL` | Optional OpenAI-compatible `/embeddings` for RAG dense mode. Blank ⇒ BM25-only. |
| `MCP_VERIFY_PORT` … `MCP_SEARCH_PORT` | `9101`–`9108`, bound to `127.0.0.1`. |
| `WATCHDOG_TOOL_BUDGET_S` | Per-tool wall-clock budget; over it without a heartbeat ⇒ hung (default `120`). |
| `SEARCH_DEFAULT_N` / `SEARCH_MAX_N` | Bounded best-of-N (default `3`, cap `6`) — competes for the one GPU. |
| `ESCALATION_LOCAL_BASE_URL` | Optional **free** local escalation tier (bigger local model); tried before any cloud tier. |
| `MONITOR_ENABLED` / `MONITOR_BASE_URL` | Optional fast critic model on a 2nd endpoint (**off** by default). |
| `RAG_INDEX_PATH`, `KG_DB_PATH` | SQLite stores; both start **empty**. |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://localhost:4317` (OTLP gRPC). |
| `ESCALATION_ENABLED` | **`false` by default.** |
| `ESCALATION_DAILY_USD_CAP` | Hard daily cap enforced **in the server** (default `$1.00`). |

### Port to your inference host — one variable

The MCP servers and Hermes are co-located, so all MCP URLs are `localhost` on
both machines. The *only* thing that changes between laptop and your inference host is the
model endpoint:

```bash
# on your inference host:
sed -i 's#^VLLM_BASE_URL=.*#VLLM_BASE_URL=http://localhost:8001/v1#' .env
scripts/start-all.sh && scripts/register-mcp.sh --sync-model-url && scripts/healthcheck.sh
```

## The eight MCP servers

Each has its own `README.md`, `requirements.txt`, `server.py`, `smoke_test.py`
and `healthcheck.sh`, and runs as an independent streamable-http process.

- **mcp-verify** — `verify(path)` runs **exactly** lint → typecheck → tests
  (Python/TS/Rust). `quick_check` (lint+type, fast per-edit) and `deep_verify`
  (difficulty-gated property/mutation/fuzz) close the silent-wrong gap.
- **mcp-codebase-rag** — `index_repo` / `search_code` / `get_symbol_context` /
  `find_similar`, **plus** graph/AST retrieval: `retrieve_related(symbol)`
  (multi-hop callers/callees/imports) and `repo_map` (PageRank, token-budgeted).
  Hybrid BM25 + dense + graph-rank; degrades to BM25 if the graph build fails.
- **mcp-knowledge-graph** — `record_entity` / `record_relation` / `query_graph`
  / `recall_about`. One embedded SQLite triples store.
- **mcp-observability** — `record_trace` / `record_metric` /
  `record_task_metrics` → OpenTelemetry to Phoenix. New servers emit
  `spiral_detected` / `poll_hang_caught` / `budget_exceeded` / `search_selected`
  / `escalated` spans.
- **mcp-escalation** — `classify_difficulty` (the **shared** difficulty signal),
  `should_escalate` (auto-triggers), `route` (hard kernel → **free local tier**
  first, cloud only if local fails), `escalate(task, tier, context)` with a
  surgical handoff. Cloud OFF by default + hard USD cap; Tier-3 rejected.
- **mcp-checkpoint** — `checkpoint(label)` / `revert_to_last_green()` /
  `list_checkpoints` / `checkpoint_status`, **plus** `snapshot_state` /
  `restore_state` so a revert restores the PLAN + notes, not just the git tree.
  Commits **only from a verified-green state**. The stuck-reset primitive.
- **mcp-watchdog** *(:9107)* — the non-turn-based detection layer:
  `check_spiral` (CoT-loop), `check_stall` (hung vs legitimately-waiting — never
  false-kills a heartbeating process), `check_progress`, `start_task_budget` /
  `check_budget`. Closes the two field-observed within-a-turn failures.
- **mcp-search** *(:9108)* — `generate_and_select`: bounded best-of-N selected by
  **execution** through mcp-verify (lossless; never returns a red patch).
  Default-low N, capped; HARD subtasks only.

## Tier-2 workflow skills (`skills/`)

`workflow-task-start` (ground in RAG + KG), `workflow-task-finish` (verify gate
+ record to KG), `workflow-stuck` (loop-then-ping circuit breaker),
`workflow-escalate` (when/when-not to escalate), `workflow-plan` (decompose
large tasks). Installed into `~/.hermes/skills/hermes-max/` by `register-mcp.sh`.

### Long-horizon scaffolding skills

Seven externalized-executive-function skills make the 35B-A3B complete full
projects without losing the plan or hanging on a forever-process:
`workflow-plan-first` (plan + pre-mortem to PLAN.md before any code),
`workflow-subtask-loop` (one bounded subtask → verify → record → **checkpoint**),
`workflow-long-running-processes` (a running server is success, not a hang —
start backgrounded, test ONCE with a timeout, never poll), `skill-process-gotchas`
(the world-knowledge a fast small model misses), `workflow-stuck-detect-reset`
(STUCK → summarize → **revert_to_last_green** → reset context → try different →
ping), `workflow-done-definition` (done = verify green, not the model's opinion),
and `workflow-context-hygiene` (PLAN.md is the source of truth). See
`CLAUDE_longhorizon.md` and `long-horizon-scaffolding.md`.

### Two-axis upgrade skills (robustness + capability)

Five skills wire the new servers into the loop: `workflow-deadline-discipline`
(≤3-4-sentence turns; background + `check_stall` once; `check_progress`/budget;
on any watchdog flag → revert + replan), `workflow-edit-format` (small diff edits
+ `quick_check` after each), `workflow-effort-routing` (HIGH effort on
planning/hard, LOW on reads/mechanical — caps spirals), `workflow-critic` (after
a hard subtask goes green, one bounded reviewer red-teams the diff, grounded in
`deep_verify`), and `workflow-subagent-isolation` (fan-out read-only
localization; keep the edit thread single and linear). All gate on the one
shared difficulty signal from `classify_difficulty`.

## Long-horizon prerequisite — the full context window

Long-horizon work needs the full ~262K window; on a 65K window the model
compresses constantly and loses the plan. The vLLM serve script's `production`
mode serves only 65536 tokens — for long projects the inference server must be launched in
**longctx** mode:

```bash
./serve-qwen36-production.sh longctx                       # MAX_LEN=262144
curl -s "$VLLM_BASE_URL/models" | python3 -m json.tool      # confirm max_model_len: 262144
```

Hermes auto-detects 262K from the live endpoint — do **not** pin `context_length`.
`scripts/healthcheck.sh` reads the served `max_model_len` and **warns if it is
< 200000**, since the long-horizon skills assume the big window. (The model
endpoint is always reached via `$VLLM_BASE_URL`; no host is hardcoded.)

## Anti-Frankenstein gate (demonstrated)

Killing any single MCP server leaves the others healthy and Hermes running — the
tool simply reports unavailable. Verify:

```bash
kill $(cat ~/.hermes-max/run/kg.pid)   # take down the knowledge graph
scripts/healthcheck.sh                 # kg shows DOWN, others ✓, exit 1
scripts/start-all.sh                   # restarts only the dead one
```

## Acceptance test

Give Hermes an unattended task: *"Implement feature X across ≥5 files in `<repo>`
with tests, following existing patterns."* Expect it to (1) `search_code` +
`recall_about` at start, (2) end with `verify` green and refuse to report done
while red, (3) record ≥1 decision/entity to the KG and distill ≥1 skill, (4)
either finish or cleanly hit an approval gate and ping via Telegram.
**Compounding proof:** run a second related task and confirm it starts
faster/better by reusing the skill + KG entries from the first.

## Explicitly out of scope (deferred to Lane 3)

Per the spec's prime directive, these were deliberately **not** built; each can
later attach as one more independent MCP server without touching this build:

- Neo4j + Graphiti + Cognee (KG uses one embedded SQLite store instead).
- Letta as a separate memory service (Hermes native memory + the KG cover it).
- 8-stage RAG (HyDE / RAG-Fusion / ColBERT / Self-RAG / HippoRAG). A reranker is
  the only sanctioned future addition, and only if eval shows precision is the
  bottleneck.
- Temporal / LangGraph outer scheduler (Hermes cron + the DSPy module cover it).
- MAP-Elites / ADAS / OMNI-EPIC archive processes.
- 10-stage verification ladder (mutation / fuzz / Lean4 / debate). Add mutation
  testing per-repo only if needed.
- HSM signing / Merkle audit / Vault netns (Lane-1 uses non-root user +
  sandboxed workdir + Hermes allow-lists + native `tirith`).
- Custom multi-agent debate framework (Hermes native delegation covers it).

## Notes on the local environment

- The chat vLLM does **not** serve `/embeddings`, so RAG runs **BM25-only** until
  you point `EMBED_BASE_URL` at a dedicated embedding model. Both modes are
  tested; hybrid is proven correct.
- `hermes-agent-self-evolution` is a **separate repo, not bundled** with
  v0.15.1; `dspy-evolution` detects this and skips gracefully (exit 0) with
  install instructions, so the weekly cron stays healthy until it's installed.
