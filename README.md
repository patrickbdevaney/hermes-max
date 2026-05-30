# hermes-max — Lane 1 Sovereign Coder

A stock **Hermes Agent** (v0.15.1) running on **a local model you choose** —
served behind one OpenAI-compatible endpoint (`$VLLM_BASE_URL`) by vLLM (CUDA),
llama.cpp (any/GGUF), or MLX (Apple) — powered up into a maximally-capable,
long-horizon autonomous engineering harness. Map your hardware to a driver tier
in the [hardware template table](#hardware--local-driver-template-pick-your-tier)
below; the orchestration above the endpoint is identical on every platform. Built
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

## Quickstart — one frictionless command

```bash
bash bootstrap.sh        # the ONE command: no chmod, auto venv/dep/MCP detection
#   idempotent; detects OS/arch, Hermes, Docker; creates .env from .env.example;
#   discovers every server (manifest + filesystem scan), builds venvs, installs
#   deps, runs smoke tests, registers with Hermes + applies native deadlines.
bash bootstrap.sh --check   # dry-run audit (what's missing), changes nothing

# then bring the stack up and restart Hermes:
scripts/start-all.sh
hermes
```

Optional supporting containers (sovereign loop): `./phoenix.sh` (OTLP :4317, UI
:6006), `./searXNG.sh` (search :8080, JSON enabled), `./crawl4ai.sh` (extract
:11235). Local model roles: `./serve-embed.sh` (:8002) + `./serve-rerank.sh`
(:8003) turn RAG hybrid+reranked. Weekly self-evolution: `dspy-evolution/register-cron.sh`.

<details><summary>Manual / step-by-step (what bootstrap automates)</summary>

```bash
cp .env.example .env                 # edit VLLM_BASE_URL (the one port switch)
scripts/smoke-test.sh                # prove each server in isolation
scripts/apply-config-deadlines.sh    # native deadline/effort knobs (backs up config)
scripts/start-all.sh                 # start all MCP servers (independent processes)
scripts/stop-all.sh                  # stop everything (PID file, port fallback); confirm ports free
scripts/restart.sh [server|all]      # restart one server (e.g. `restart.sh research`) or all
scripts/status.sh                    # human view: UP/DOWN · port · PID · uptime · health per server
scripts/register-mcp.sh              # inject mcp_servers + install skills (--sync-model-url opt.)
scripts/healthcheck.sh               # confirm everything is live (pass/fail, for scripting)
scripts/watch.sh                     # live tool-call stream (Stage 3) · run-summary.sh for the table
scripts/sovereignty-test.sh          # assert the no-cloud-key property holds
```

### One ergonomic command — `hm` (Stage 8)

`hm` is a thin dispatch wrapper over the scripts above — sugar, **not** a
replacement: every `scripts/*.sh` still runs directly, unchanged. Install it onto
PATH with `./hm install` (or it's set up by `bootstrap.sh`); it also runs as `./hm`
from the repo root.

```bash
hm up                  # start all MCPs            (scripts/start-all.sh)
hm down                # stop everything           (scripts/stop-all.sh)
hm restart research    # restart one server or all (scripts/restart.sh)
hm status              # UP/DOWN · port · PID · uptime · health  (scripts/status.sh)
hm watch               # live tool-call stream     (scripts/watch.sh)
hm logs research       # tail a server's log
hm run "fix the test"  # launch hermes with a task (hermes)
hm snapshot baseline   # store snapshot/restore    (scripts/snapshot-stores.sh)
hm summary             # per-task summary + bottleneck split
hm dev                 # the tmux cockpit (below)
hm attach              # reattach to the cockpit
hm health              # pass/fail healthcheck     (scripts/healthcheck.sh)
hm eval [capability]   # prove every feature works in the REAL agent loop (scripts/eval-battery.sh)
```

**`hm dev` — the one-command tmux cockpit.** Brings the servers up backgrounded
and spawns a detachable `hermes-max` tmux session: a large **hermes** pane, a
**`watch.sh`** live-stream pane, and an auto-refreshing **`status.sh`** pane.
Detach with the usual tmux binding and the session + servers keep running (24/7
unattended grind); **reattach from anywhere** — e.g. laptop → inference host over
the network — with `hm attach`. If tmux isn't installed, `hm dev` prints clear
manual instructions instead of failing, and every individual script still works
without it.

**Process lifecycle (Stage 5)** is three reliable, manifest-driven commands:
`stop-all.sh` (kills each MCP by its PID file, falls back to a port-based kill,
confirms every MCP + embed/rerank port is free — idempotent), `restart.sh
[server|all]` (stop→start one named server or the whole stack, re-runs health),
and `status.sh` (the at-a-glance human table — distinct from `healthcheck.sh`,
which is the pass/fail scripting check). Adding a server is still one manifest
entry — none of these scripts need editing.

**Store snapshots (Stage 6).** The RAG index + KG db + on-disk corpus are
**permanent and compounding by default** (long-term accumulated knowledge — that
is unchanged; with no snapshot calls the stores just keep compounding). To
**isolate a test session** without losing the real stores:

```bash
scripts/snapshot-stores.sh baseline   # capture RAG+KG+corpus → ~/.hermes-max/snapshots/baseline
#   …run the eval; the stores compound…
scripts/list-snapshots.sh             # list snapshots with timestamps + sizes
scripts/restore-stores.sh baseline    # swap baseline back (current state auto-backed-up first)
```

`restore-stores.sh` snapshots the current state to `_pre-restore-<ts>` before
overwriting, so a restore is itself reversible.
</details>

Stop the servers with `kill $(cat ~/.hermes-max/run/*.pid)`. Logs are under
`~/.hermes-max/logs/`.

## The agent-level eval battery — *does every feature actually work?*

`smoke-test.sh` proves each server responds **in isolation**; `healthcheck.sh`
proves each is **live**. Neither proves the thing that actually matters: that the
**Hermes agent can use each feature in the real loop**. A server can answer `200`
on `/health` while the agent's call into it silently fails — exactly the class of
bug fixed in this work (a long sync tool blocked the event loop so `/health`
timed out → false DOWN; a bare `asyncio.run()` inside the live loop made every
MCP-to-MCP call return nothing → empty research). Isolation tests were green
throughout; only a **real agent turn** exposed it.

`scripts/eval-battery.sh` (`hm eval`) is the canonical *"is the system actually
working?"* check. It drives **each capability through a real `hermes -z … --yolo`
agent turn** and asserts the **real-world effect** — a file on disk, a row in the
KG db, a doc in the corpus, a git checkpoint commit, a line in `MEMORY.md`, a span
in the live log — **not just a 200**. A capability "works" only if the agent
invoking it produced the change it's supposed to.

```bash
hm eval                      # whole battery (real agent turns; ~tens of minutes)
hm eval knowledge-graph      # one capability (fast iteration)
hm eval --quick              # fast subset: core-memory, knowledge-graph, verify
scripts/eval-battery.sh --no-cloud   # skip conductor tests that need a key
```

Coverage: `verify` gate · `codebase-rag` · `knowledge-graph` · `checkpoint` ·
`watchdog` · `search` (best-of-N) · `docs` · `research` · `escalation` ·
`observability` · `core-memory`. It is **isolated** — RAG/KG/corpus are
snapshotted and restored, `MEMORY.md` is backed up and restored, and filesystem
tests run in their own temp project dir, so it never pollutes real state. Output
is a readable `eval_battery_report.md`: per-capability PASS/FAIL, the agent task
used, the tool that fired, the artifact effect verified, and for any failure the
**precise break-point** (tool-not-called / tool-errored / no-effect).

**Run it after any change, or on a fresh install** (`bootstrap.sh --verify-agent`
drives the quick subset through real agent turns) to confirm the agent can really
use every feature — not just that the servers are up.

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

## The ten MCP servers

Each has its own `README.md`, `requirements.txt`, `server.py`, `smoke_test.py`
and `healthcheck.sh`, and runs as an independent streamable-http process. The
single source of truth for the list is `mcp-manifest.yaml` — adding a server is
one line there; every script picks it up.

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
- **mcp-docs** *(:9109)* — the **sovereign documentation loop**: `search_docs`
  (SearXNG JSON), `fetch_clean` (Crawl4AI → markdown, trafilatura fallback),
  `ingest_doc` / `research_topic` (fetch → distil with the local model → store in
  the RAG `docs/<topic>` namespace + KG `framework→api`). Learn a novel framework
  on demand with **no external API**.
- **mcp-research** *(:9110)* — **SOTA local deep-research**: the four-stage loop
  `plan_research` → `develop_queries` → `explore` → `verify_claims` →
  `synthesize`, with `deep_research` as the bounded orchestrator. Built on the
  sovereign loop (SearXNG + Crawl4AI/trafilatura + chat model + RAG/KG), engineered
  against the four named failure modes — echo-chamber (query diversity + URL/n-gram
  dedup), source-quality bias (authority-aware ranking), planning hallucination
  (checkable PLAN + intermediate verify), overspawning (hard caps) — each a tested
  invariant. Citation-backed; compounds the brief + entities into RAG/KG. Runs on
  **both** deploy profiles.

mcp-knowledge-graph also gains **self-editing core memory** (`core_memory_get` /
`core_memory_append` / `core_memory_replace`) wired to Hermes's native MEMORY.md —
the always-in-context, size-bounded block the agent deliberately curates.

## Compounding, sovereignty & graceful degradation

**Compounding (Stages 1–3).** RAG is hybrid + cross-encoder **reranked**
(`serve-embed.sh` / `serve-rerank.sh`, local Qwen3-0.6B models; measured MRR
0.41→0.75). The sovereign **docs loop** self-seeds framework knowledge on demand.
**GEPA** (`dspy-evolution/`) evolves the difficulty-classifier prompt on
your own traces — and every escalation outcome (`record_outcome`) becomes a
labelled example, so the local model handles progressively more (the flywheel).

**Sovereignty assertion (the headline).** With **all external API keys unset** and
only the local stack running (vLLM + SearXNG + Crawl4AI), the full loop — search,
extract, distil, store, retrieve, verify, evolve — works. Prove it:
`scripts/sovereignty-test.sh`. The ONLY hard dependency is the local vLLM chat
model; every other capability has a local default or degrades cleanly.

**Graceful-degradation matrix** — every component continues with a warning, never
a hard fail:

| Component | Backend absent/down ⇒ |
|---|---|
| embeddings (`EMBED_BASE_URL`) | RAG runs **BM25 + graph** (no dense lane) |
| reranker (`RERANK_BASE_URL`) | RAG returns the **fused order** (no `+rerank`) |
| Crawl4AI | `fetch_clean` falls back to local **trafilatura** |
| SearXNG | `search_docs` reports unavailable (other tools unaffected) |
| local chat model | `ingest_doc` stores **raw** markdown (no distil) |
| RAG/KG (for docs) | note/entities not stored, reported (fetch/distil still work) |
| deep-research (`mcp-research`) | SearXNG/Crawl4AI down ⇒ fewer/no sources; reranker absent ⇒ **authority-only** ranking; chat model unset ⇒ **deterministic** plan/queries/synthesis (cited bullet brief) |
| dspy / gepa | `run-evolution.sh` is a no-op (exit 0) with install hint |
| escalation cloud tier | OFF by default; local tier tried first; never required |
| conductor role (steer/synth/escalate) | a role with **no present key** is OFF ⇒ driver proceeds local-only |
| conductor rung fails (429/5xx/cap) | **silently falls** to the next present rung (logged one-liner); none ⇒ `proceed_local` |
| parallel_draft pool | absent free keys aren't drafted; RPM/TPM-exhausted sources skipped; zero keys ⇒ **N=1-local** |
| Phoenix (OTLP) | spans dropped silently; servers run unaffected |

## The Conductor — optional, presence-gated cloud help (never a backend swap)

The conductor adds OPTIONAL cloud assistance **as stateless tools** on top of the
finished local harness. The local Qwen driver does **all** high-volume execution
and orchestration at **$0**; cloud models are invoked rarely, behind tools, for
cents-or-free, ONLY for work the local model can't do alone, and ONLY for the
providers you actually configured. **With zero cloud keys set, every rung
is OFF and the system is the bare local harness — nothing breaks.**

**Four layers (the separation that keeps it from being a mess).** (1) **BASE** —
the harness on local vLLM (`$VLLM_BASE_URL`, **never** touched by cloud roles).
(2) **ROLES** — `steer` (frequent cheap nudges), `synthesize` (rare deep
decomposition), `parallel_draft` (verifier-selected best-of-N on VERIFIABLE
subtasks), `escalate` (the rarest Opus kernel). A role is ACTIVE iff ≥1 provider in
its chain has a key. (3) **CHAINS/POOLS** — each role has an ordered provider chain
(unordered pool for parallel_draft); at call time skip absent rungs, use the first
present one, silently fall-with-log on failure. (4) **TOOL, NOT SWAP** — the Hermes
loop runs on local Qwen the whole time; cloud models are draft generators / advisors
behind MCP tools. The backend model is **never** hot-swapped.

**Presence-gating — as many or as few keys as you have.** Keys (`.env`) only ENABLE
rungs; they never set order. Order lives in the registry defaults or an optional
`conductor.yaml` (precedence: **hardcoded defaults < conductor.yaml**; env supplies
keys only). `conductor_status` shows which roles are active and the resolved chains.

**Default chains** — reliability-first by construction: US-hosted inference providers are
preferred as defaults due to uptime reliability and explicit data-handling guarantees;
direct provider endpoints are supported as opt-in alternatives via conductor.yaml.
- **synthesize**: DeepInfra → Fireworks → Together → DeepSeek → Kimi → Opus. US hosts
  sit ABOVE direct-provider-hosted DeepSeek-direct and SG-hosted Kimi *by design*, so a present
  DeepInfra key is always preferred. Default model **DeepSeek-V4-Pro**.
- **steer**: DeepSeek-V4-Flash@DeepInfra → Cerebras → Groq → Gemini-Flash —
  **cheap-reliable-first**: the paid V4-Flash (hundredths of a cent, 1M ctx, cache,
  reliable) BEFORE the fragile free tiers (corrected from real-world pricing).
- **parallel_draft pool** (unordered, for diversity): Cerebras GLM-4.7 + gpt-oss-120b,
  Groq gpt-oss-120b + qwen3-32b + llama-4-scout, + optional DeepInfra V4-Flash anchor.

**The division of labour:** *slop-draft the verifiable, synthesize the ambiguous,
escalate the frontier-novel.* parallel_draft fires ONLY when there's an objective
test oracle (the verifier, not a model, selects the winner); ambiguous decisions go
to synthesize (no oracle ⇒ can't select); Opus fires ONLY when synth fails verify
twice or two opinions disagree on a high-blast-radius change. Cloud directives are
**advisory** — `directive_verify` checks every assumption against real repo state,
confirms the APIs exist, and requires concrete tests before anything executes.

### ⚠ API brittleness — why the design is local-first with silent-fallback cloud

**No free tier is production-durable, and this is the whole point of the design.**
Free tiers and model availability are volatile and subjective; an endpoint vanishing
must degrade the system gracefully, never break it. Verified live (2026-05):

- **Groq** hollowed out its frontier catalog post-Nvidia-acquisition; what remains
  (gpt-oss-120B/20B, qwen3-32B, llama-4-scout) is useful only as draft diversity, and
  its free-tier **TPM is tiny and per-model** (gpt-oss-120B 8K, qwen3-32B 6K). In our
  own Stage-0 eval Groq **429'd after one full-brief call and 413'd qwen3-32B**. The
  conductor pre-flight-checks per-model TPM (header-fed) and **caps Groq draft input to
  ~3.5K tokens** so it stays usable — proven live: the same brief that 429'd now runs.
- **Gemini 2.5 Pro** left the free tier 2026-04; **2.5 Flash is as low as 20 RPD on free
  accounts (verify your own console)** — a tracked last-resort steer only.
- **Cerebras** is a real free asset (GLM-4.7 + gpt-oss-120B, ~30K TPM) but **preview**
  and can change. Stage-0: gpt-oss-120B at **1.4s**, both models **35/35** quality —
  the preferred free draft source.
- **DeepSeek-direct** is cheapest at source but direct provider endpoint; sits below
  US-hosted inference providers in the default chain as a hedge against single-region
  availability risk.
- **DeepInfra** is the paid default: DeepSeek **V4-Flash $0.10/$0.20**, **V4-Pro
  $1.30/$2.60** per 1M (cached far less), no-train, US. Stage-0: V4-Pro **$0.0035/brief**,
  V4-Flash **$0.00022/brief**.

The response to all of this is structural: a **local-viable foundation + presence-
gated optional cloud + silent fallback + this honest README**. Realistic heavy month
≈ **$3–8** (Opus dominates despite its rarity); parallel_draft can run on **free keys
alone**, and with **no keys at all** it's the bare local harness.

See `scripts/eval-synthesis.sh` (rank candidates on your own work),
`scripts/conductor-report.sh` (honest frequency + cost vs targets: synth ≤ ~15/
project, Opus ≤ ~3), the `workflow-conductor` skill (the invocation ladder), and
`conductor.yaml.example` (override chains/models).

## Deploy profiles — one codebase, two targets

`DEPLOY_PROFILE` (in `.env`) selects how the stack runs. `bootstrap.sh`
auto-detects and **suggests** a profile (CUDA + RAM + arch + endpoint), never
silently overriding an explicit `--profile` / `DEPLOY_PROFILE`. Pick by filename —
two **one-line wrappers** over the single engine, no code duplication:

```bash
bash bootstrap-gpu.sh     # DEFAULT, maximalist (gpu_local)
bash bootstrap-lean.sh    # CPU / Mac-mini / VPS  (lean_cloud)
```

The manifest gates which servers run per profile, so a future **gpu_local-only**
capability is one `profiles:` line and lean is unaffected — lean is a graceful
**subset**, never a ceiling on full.

| Capability | `gpu_local` (default) | `lean_cloud` (CPU/Mac/VPS) |
|---|---|---|
| Chat model | local vLLM **or** cloud via `$VLLM_BASE_URL` | cloud via `$VLLM_BASE_URL` (assumed) |
| RAG embeddings | local Qwen3-Embed-0.6B (CUDA) | optional cloud `EMBED_BASE_URL`, else **BM25+graph** |
| Reranker | local Qwen3-Reranker-0.6B (CUDA) | cloud if set, else fused-no-rerank |
| RAG graph (tree-sitter+PageRank) | full | **full** (pure-Python, CPU-fine) |
| Doc extract | Crawl4AI | Crawl4AI if Docker present, else **trafilatura** |
| Deep research (`mcp-research`) | full | **full** (uses the cloud chat endpoint) |
| GEPA self-evolution | full (local model) | optional (cloud, rate-limited) — off by default |
| verify / checkpoint / watchdog / KG | full | **full** (all pure-Python) |

**The lean guarantee:** **no** MCP server `requirements.txt` pulls torch/CUDA —
every server reaches models over HTTP. The only torch/CUDA touchpoints are the
optional, gpu_local-only `serve-embed.sh` / `serve-rerank.sh`. `bootstrap.sh`
asserts this (greps requirements), so a lean box never needs a GPU stack.

## Hardware → local-driver template (pick your tier)

You supply the inference server; hermes-max only talks to it over `$VLLM_BASE_URL`.
The rows below are **examples, not prescriptions** — map your machine to a VRAM/
compute tier and pick any model in that class. Smaller local driver → lean harder
on the conductor's cloud tiers (the presence-gated design makes this automatic).
The **Qwen3.6 series** is a sensible default family (GQA-friendly KV, edge-sized
weights); **Nemotron** and **Gemma-4** are good alternatives.

| Hardware tier (examples) | Approx VRAM | Suggested local driver tier (examples) |
|---|---|---|
| DGX Spark / Jetson Thor / RTX 6000 Pro | 96–128GB+ unified/VRAM | Large MoE driver (Qwen3.6 ~122B-A10B class, or Nemotron-Super) |
| RTX 5090 / 4090 | 24–32GB | Mid driver (Qwen3.6 ~35B-A3B, Nemotron, Gemma-4 ~27–31B) |
| RTX 3090 / 4080 | 16–24GB | Qwen3.6 ~35B-A3B quantized, or ~14–32B dense |
| M4 Max/Ultra Studio (MLX/GGUF) | 36–128GB unified | Qwen3.6 35B-A3B / larger MoE via MLX or llama.cpp |
| RTX 4060 Ti / 3060 / gaming laptop | 8–16GB | Smaller GGUF (~14B class) + lean on free/full cloud tiers |
| Jetson Orin / small edge | 8–32GB | Small driver + heavier cloud uplift |
| No GPU / VPS | — | Cloud-only driver (cheap model via conductor); `local` mode unavailable |

Inference server per platform — all expose an OpenAI-compatible endpoint, so the
orchestration is identical above it: **vLLM** (CUDA), **llama.cpp** (any/GGUF),
**MLX** (Apple). Point `$VLLM_BASE_URL` at whichever you run.

## Cloud-spend modes — `local` / `free` / `full` / `frontier`

The mode is a **hard spend-tier cap** (the *ceiling* on which cloud tiers may fire),
orthogonal to `DEPLOY_PROFILE` (the *hardware* lane). Select it with `hm up --MODE`
(default `--full`) — it persists to `CONDUCTOR_MODE` in `.env` and the conductor
reads it live:

```bash
hm up --local      # fully sovereign, $0 cloud
hm up --free       # + free draft/steer (Cerebras/Groq/Gemini)
hm up              # = --full (DEFAULT): + DeepSeek V4 synth/steer (~$10/mo)
hm up --frontier   # + SPARING Opus 4.8 escalation (~$12-15/mo) — needs ANTHROPIC_API_KEY
```

| Mode | Cloud tiers (ceiling) | Cost | Use |
|---|---|---|---|
| `local` | **none** — local vLLM only | $0, offline, fully sovereign | The guaranteed-correct base case. Present keys are ignored. |
| `free` | + **free** tiers (Cerebras/Groq/Gemini) | $0 | Real cloud uplift with no bill; live budget tracker keeps it inside free rate limits. |
| `full` | + **paid** DeepSeek V4 synth/steer (DeepInfra) | ~$10/mo | The recommended daily driver. **Does NOT include Opus.** |
| `frontier` | + **SPARING Opus 4.8** escalation (`claude-opus-4-8`) | ~$12-15/mo | Closes the last gap to Opus/Claude-Code on genuine blue-ocean frontier-novel work. |

Each mode falls back **through** the ones below it to the highest tier whose keys
are present: `frontier → full → free → local`. `hm up --frontier` without
`ANTHROPIC_API_KEY` warns and falls back to `--full` (Opus OFF) — it never silently
pretends frontier is active.

**The frontier (Opus 4.8) tier is deliberately RARE.** It is the top rung of the
escalate role and fires ONLY when **all three gates** trip: (1) `--frontier` mode +
`ANTHROPIC_API_KEY`; (2) the classifier flags the subtask **frontier-novel** (genuinely
blue-ocean — merely-HARD-but-known stays at V4-Pro); (3) V4-Pro synth has **already
failed verify twice** on the subtask (or two opinions disagree on a high-blast change).
When it fires it uses **compress-then-reason** — V4-Pro (the cheap model) compresses the
full situation into a dense ~12K brief, then Opus reasons on that brief (~$0.18/call,
→ ~$0.10 cached) — writes the result to a durable `FRONTIER_PLAN.md` + RAG/KG with
provenance, and passes it through `directive_verify` (advisory, not trusted-blind). A
hard frontier USD cap (`FRONTIER_USD_CAP_MONTHLY=10`, `_DAILY=2`) blocks and falls back
to V4-Pro when hit.

`hm cost` proves it stays sparing: month-to-date per-tier spend, the Opus call count +
cost vs the sparing target (≤15/mo, `FRONTIER_TARGET_CALLS_MONTHLY`), the frontier-mode
total vs Claude Code's flat $20/mo, and a **warning** if Opus drifts over the target
(which means the difficulty gate is too loose — or the work is genuinely blue-ocean and
Claude Code may fit better; reported honestly). `hm status` shows the active mode + live
tiers + (in frontier mode) Opus spend-vs-cap.

## Tier-2 workflow skills (`skills/`)

`workflow-task-start` (ground in RAG + KG), `workflow-task-finish` (verify gate
+ record to KG), `workflow-stuck` (loop-then-ping circuit breaker),
`workflow-escalate` (when/when-not to escalate), `workflow-plan` (decompose
large tasks), `workflow-deep-research` (drive `mcp-research`'s `deep_research` for
current/external knowledge — gate depth on scope, verify before asserting, cite
every claim). Installed into `~/.hermes/skills/hermes-max/` by `register-mcp.sh`.

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
`specs/CLAUDE_longhorizon.md` and `long-horizon-scaffolding.md`.

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

## Live observability — see what the agent is doing in real time (Stage 3)

Beyond the post-hoc Phoenix spans there is a **live, operator-facing tool-call
stream**. Run one command in a side terminal:

```bash
scripts/watch.sh        # live, colourised tail of every tool call
```

and watch the entire agent loop moment-to-moment — which tool is running, its
input, how long it ran, what it returned, every heartbeat, every fallback, and
every routing/kill **DECISION** with its reason:

```
[14:03:01] → TOOL index_repo (rag:9102) | input: {path=/repo} | est: ~98s
[14:03:46] ⟳ index_repo heartbeat | progress: 400/1240 (32%) | elapsed 45s
[14:04:33] ✓ index_repo OK | 92.3s (est ~98s) | returned: {files=1240, mode=hybrid}
[14:04:34] ✗ groq FAILED | reason: 429 | falling back to: deepinfra
[14:04:34] • DECISION route → synth (DeepInfra) | reason: generative, not steering
```

Verbosity is `.env`-controlled (`HERMES_MAX_VERBOSITY=quiet|normal|verbose|debug`,
**default `verbose`** so you always see what's happening). The stream is fed by
`lib/livelog.py` via every server's `otel_emit` — it emits **alongside** the
Phoenix/OTel spans, never replacing them, and degrades silently (a logging
failure never breaks a tool). At task end, a per-tool summary table:

```bash
scripts/run-summary.sh  # count · total time · failures · fallbacks · est-vs-actual, per tool
```

### tqdm progress + the bottleneck split (Stage 7)

`deep_research` and `index_repo` emit **tqdm-style empirical progress** — current
item N/total, per-item timing, a running ETA — so you can tell instantly whether
it's moving or stuck on one slow item:

```
⟳ index_repo [840/1240] 67% | 52s elapsed · ETA ~24s | 3 skipped (unparseable)
⟳ deep_research [4/12] 33% | arxiv.org/abs/2401.x | crawl 3.2s · distil 8.1s | elapsed 47s · ETA ~94s
```

Every per-task summary also prints the **bottleneck timing split** — where the
wall-clock actually went — so you can SEE whether the advanced features earn their
latency:

```
bottleneck split: inference 4m12s (58%) · tool-work 2m30s (35%) · artificial 0m31s (7%)
```

- **inference** — local model thinking (irreducible) · **tool-work** — real tool
  execution (crawl, tests, indexing) · **artificial** — rate-limit waits, 429/5xx
  backoffs, redundant sequential calls, MCP overhead. A large `artificial` fraction
  means a feature is wasting the agent's time — the summary names which.
- Research **distillation defaults to the local model** (the highest-volume step):
  gating it on a rate-limited cloud tier would force 429 backoffs — exactly the
  artificial bottleneck above. Cloud distill is opt-in (`RESEARCH_CLOUD_DISTILL`),
  warned as rate-limit-bound.

```bash
scripts/bottleneck-eval.sh   # run the SAME task FULL vs BARE; print both splits → bottleneck_report.md
```

## Validate the whole system — dry-run & rate-limit check

```bash
bash scripts/dry_run.sh --mode local   # base case: zero cloud, every cloud step skip-logged
bash scripts/dry_run.sh --mode free    # + real free cloud (Cerebras/Groq)
bash scripts/dry_run.sh --mode full    # + paid synth/steer (DeepInfra)
```

A **rapid real-inference smoke** (~15s) that fires every component once end-to-end
(driver → classifier → watchdog → steer → research → corpus → KG → RAG → synth →
verify → draft-pool → verifier-select → Banyan → checkpoint → escalation-DRY) and
writes a readable **`dry_run_trace.md`**: per step the component, provider/model
used (or skipped + why), latency, tokens/cost, PASS/FAIL, and the real I/O snippet.
The `local` run passes with **zero cloud keys** — the one hard dependency is the
local model at `$VLLM_BASE_URL`.

```bash
bash scripts/rate-limit-validation.sh   # prove the free-tier budget tracker ($0)
```
Drives the best-of-N draft pool until Groq's per-model TPM exhausts and shows the
live tracker **pre-flight-skipping** the over-limit call (never a 429/413 crash) →
`rate_limit_validation_trace.md`.

```bash
bash scripts/emergent_eval.sh           # hunt the interaction failure modes
```
A combinatorial eval that hunts the emergent failures isolated tests miss, with
EVIDENCE on three suspicion risks and each config remedy toggled A/B →
`emergent_eval_report.md`. **Honest findings:**

- **Banyan focus-thrash (confirmed).** UCB1 is a stationary-bandit explorer — net
  *positive* for research breadth, net *negative* for build-loop focus: unscoped, it
  scored a **thrash of 1.0** (abandoned incomplete work on every switch). **Remedy
  (shipped default):** `BANYAN_SCOPE=research_only` — UCB1 governs research namespaces
  only; the build loop uses finish-what-you-started / dependency-order, driving
  build-loop thrash to **0.0**.
- **Research-noise contamination.** Noisy/low-authority findings can poison a synth
  brief → confident wrong directives. **Remedy (default on):**
  `RESEARCH_RELEVANCE_FILTER` + authority/relevance floors drop the noise *before*
  ingestion (precision over recall).
- **Ladder cascade-escalation.** Each tier trigger is individually sane, but a hard
  subtask could cascade and burn budget. **Remedy (default on):** a **global
  per-subtask budget** (`CONDUCTOR_SUBTASK_USD_CAP` / `_MAX_TIERS`) stops + surfaces
  to the user regardless of per-tier triggers.

Empty-base correctness holds on **zero data**: UCB1 optimistic prior, saturation
disabled below 10 tasks/namespace, classifier escalate-when-uncertain. Coherence
holds: the verify gate kills a bad directive from any source, every component
degrades to local without crashing when its cloud is killed, and a task-1 finding
compounds into task 2.

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
