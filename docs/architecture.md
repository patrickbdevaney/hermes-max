# hermes-max — Architecture

A harness that wraps a **local Qwen/35B agent ("Hermes")** with a layer of independent
**MCP servers + skills + a presence-gated cloud conductor**, so a weak-but-free local model
produces senior-grade engineering output. Two invariants hold everywhere:

- **Degrade gracefully.** Any server can die, any cloud key can be absent — the bare local
  agent keeps running. No MCP server imports torch/CUDA; every model call goes over HTTP.
- **Anti-Frankenstein.** Extend the existing surfaces (MCP tools, markdown skills, one
  conductor); never add a framework or swap the backend model. Cloud help is *optional,
  stateless tools*, never a different brain. The local 35B is always the orchestrator.
- **Providers are config, not code.** MCP servers request a *role*; the inference fabric
  (`lib/inference/`, §9) chooses a provider from `inference.yaml`. Missing keys silently
  drop rungs; zero keys = fully local and free. No MCP server names a provider.

---

## 1. The three layers

| Layer | What it is | How it's invoked |
|---|---|---|
| **MCP servers** | 14 independent HTTP processes, each owning one capability. Registered in `~/.hermes/config.yaml`. | The model emits a tool call → Hermes routes it over streamable-HTTP. |
| **Skills** | ~33 `skills/<name>/SKILL.md` files (frontmatter: `name`, `description`/`trigger`). | Loaded into the system prompt as *triggered guidance* — they tell the model **when** to call which tool and **what discipline** to follow. Advice, not code. |
| **Conductor** | Cloud-routing logic inside `mcp-escalation`: presence-gated tiers + a stingy ladder. | Advisory tools (`conductor_plan`, `conductor_synthesize`…) the model calls only on hard subtasks. |

Skills are the *policy*, MCP tools the *mechanism*, the conductor the *escape hatch* to a bigger model.

---

## 2. MCP server inventory (14 servers)

Single source of truth: **`mcp-manifest.yaml`** — one flat entry per server
(`name, dir, port, port_env, health, register_as, profiles, requires, degrades_to`), parsed by
`scripts/manifest.py`. Adding a server = one entry + one env var. Profiles `gpu_local` (default)
/ `lean_cloud` let CPU-only deploys drop GPU pieces; only optional embed/rerank serves touch CUDA.

### Verification — `mcp-verify` (9101) · *pure-Python, both profiles*
The deterministic done-gate.
- `verify(path)` — lint → typecheck → unit tests; the hard pass/fail.
- `quick_check(path)` — lint+typecheck only, after each edit.
- `deep_verify(path, difficulty)` — base + difficulty-gated property/mutation/fuzz layers.
- `property_test` · `metamorphic_test` · `differential_test` · `mutation_test` — edge cases & test-gap finding when there's no oracle.
- `quality_check(path)` — **advisory** senior-review texture (annotations/docstrings/TODOs/bare-except); never gates.

### Retrieval & memory
- **`mcp-codebase-rag` (9102)** · *BM25+AST-graph when no embed/rerank* — `search_code`, `index_repo`/`scan_repo`, `get_symbol_context`, `find_similar`, `retrieve_related` (multi-hop), `repo_map`, `corpus_hit_check` (already-answered gate), `index_document`.
- **`mcp-knowledge-graph` (9103)** · *pure-Python sqlite triples* — `record_entity`, `record_relation`, `query_graph`, `recall_about`, `core_memory_get/append/replace` (always-in-context block).
- **`mcp-repomap` (9111)** · *static, no model* — `repo_map` (Aider PageRank over a tree-sitter/NetworkX symbol graph).
- **`mcp-lsp` (9112 + Serena backend 9113)** · *falls back to grep* — `lsp_find_symbol`, `lsp_find_references`, `lsp_go_to_definition`, `lsp_hover`, `lsp_rename`, `lsp_diagnostics`, `lsp_activate_project` (~50ms compiler-grade).
- **`mcp-codegraph` (9114)** · *pure AST graph* — `code_impact` (blast radius), `code_callers`/`code_callees`, `code_importers`, `code_dead_code`, `code_structural_search` (ast-grep), `index_codegraph`.

### Research (sovereign web)
- **`mcp-docs` (9109)** · *Crawl4AI→trafilatura ladder* — `search_docs` (self-hosted SearXNG), `fetch_clean`, `ingest_doc`, `research_topic` (learn a framework on demand).
- **`mcp-research` (9110)** · *requires docs; degrades to deterministic plan/synthesis without VLLM* — the bounded deep-research engine. Core loop `plan_research → develop_queries → explore → verify_claims (≥2 sources) → synthesize`, with `deep_research` end-to-end (compounds into RAG/KG) gated by `corpus_hit_check` + `note_lighter_tools_attempted`. Plus a wide keyless source fan-out (`multi_source_search`, `arxiv_search`, `semantic_scholar_*`, `github_search`, `hn_search`, `stackexchange_search`, EIP/RFC/ethresearch readers), KG landing (`kg_add_episode`, `kg_*_fact_edge`, `kg_mark_superseded`), and a **Banyan** self-direction layer (`banyan_next_action`/`select`/`update`/`detect_saturation`/`set_directive`/`write_skill`) for unattended UCB1-driven research cycles.

### Reliability & long-horizon
- **`mcp-checkpoint` (9106)** · *requires verify* — `checkpoint(label, verify=True)`, `revert_to_last_green`, `list_checkpoints`, `checkpoint_status`, `snapshot_state`/`restore_state` (PLAN.md + notes).
- **`mcp-watchdog` (9107)** · *falls back to native guardrails* — `check_spiral` (CoT loop), `check_stall`+`record_heartbeat` (hung tool), `tool_budget`/`estimate_duration`, `check_progress`, `start_task_budget`/`check_budget`. Out-of-band, not turn-based.
- **`mcp-observability` (9104)** · *no-op when Phoenix down* — `record_trace`/`record_metric`/`record_task_metrics`, `record_trajectory`+`localize_failure`+`list_trajectories`, `record_skill_fired`, `condense_context`.

### Search & cloud
- **`mcp-search` (9108)** · *requires verify; selector always on* — `generate_and_select` (verifier-guided best-of-N), `parallel_draft` (fan the conductor pool over a verifiable subtask; the *verifier*, not a model, picks the winner).
- **`mcp-escalation` (9105)** — **the conductor** (§4).

---

## 3. Skills — when the model reaches for what

Triggered discipline injected into the system prompt:

- **Task lifecycle** — `workflow-task-start` (ground in RAG/KG first), `workflow-subtask-loop`, `workflow-task-finish` (never declare done on red), `workflow-done-definition`, `workflow-effort-routing` (deep reasoning on planning, terse on execution), `workflow-tool-selection` (cheapest rung first).
- **Planning** — `workflow-plan-first`, `workflow-plan` (>5 files), `workflow-spec-driven` (SPEC/PLAN/TASKS), **`workflow-plan-contract`** + **`workflow-execute-from-plan`** (the plan/execute split, §5).
- **Retrieve / context** — `workflow-retrieve-before-act`, `workflow-repomap`, `workflow-codegraph` (blast radius before editing), `workflow-lsp`, `workflow-context-hygiene`, `workflow-memory-curation`, `workflow-filesystem-offload`, `workflow-context-condenser` (~80%), `workflow-cache-discipline` (stable prefix for vLLM cache).
- **Verification** — `workflow-verify-enhanced` (property/mutation on core logic), `workflow-critic` (red-team after green), **`workflow-quality-bar`**, `workflow-edit-format` (small diffs, verify each).
- **Execution / recovery** — `workflow-long-running-processes`, `workflow-deadline-discipline`, `skill-process-gotchas`, `workflow-stuck-detect-reset` (reset after 3× no-progress), `workflow-stuck` (stop, report, ping operator).
- **Cloud / research** — `workflow-conductor` (the ladder), `workflow-escalate`, `workflow-deep-research`, `workflow-learn-framework`.
- **Parallelization** — `workflow-subagent-isolation` (fan out read-only research).

---

## 4. The conductor — presence-gated tiered cloud help

Inside `mcp-escalation`, three pure pieces + a policy + a frontier flow. **Advisory tools, never a
backend swap.** Zero cloud keys → every rung OFF → fully local at $0.

**Registry** (`conductor_registry.py`) — provider data + optional `conductor.yaml` overlay. **Keys =
presence only** (a key enables a rung; order lives in the chains). Default chains:
- `synth` → DeepInfra **DeepSeek-V4-Pro** (deep decomposition / the expensive planner), then Fireworks/Together/DeepSeek/Moonshot. Opus deliberately excluded.
- `steer` → DeepInfra **DeepSeek-V4-Flash** (cheap mid-execution nudge), then free Cerebras/Groq/Gemini.
- `escalate` → **Opus 4.8** (frontier-only).
- `draft_pool` → Cerebras/Groq free models + a V4-Flash anchor, for best-of-N.
- Caps: USD daily $1 / monthly $5 (defaults), `draft_max_n` 5 — overridable by env then yaml.

**Resolver** (`conductor_resolver.py`) — pure presence check. `CONDUCTOR_MODE` is a **hard
spend-tier cap**, read live: `local`={} · `free`={free} · `full`={free,paid} *(default)* ·
`frontier`={free,paid,frontier}. A role with zero permitted+present rungs is OFF → proceed local.
A present paid key is *ignored* in `free` mode.

**Executor** (`conductor_core.py`) — `run_role()` walks the chain: pre-flight RPM/RPD/TPM budget
check (skip, never absorb a 429) → first present rung wins → on any failure/429/5xx/timeout/empty
**silently falls** to the next with a one-line trace → returns `{ok:False, proceed_local:True}` if all
fail. Cost hits a ledger only after success; over-cap paid rungs behave as absent. `draft_fanout()`
runs the pool concurrently. **Never raises into the agent loop.**

**Policy** (`conductor_policy.py`) — `plan_invocation()` is the stingy **ladder advisor**:
easy/medium→local; verifiable+hard→`parallel_draft`→`synthesize`; ambiguous+hard→`steer`→
`synthesize`; **Opus gate** (the only path to `escalate`) = synth-failed-twice OR opinions-disagree
on a high-blast change. A per-subtask budget ($0.50 / 4 tiers) caps cascades. Outcomes recorded to
the KG (the compounding flywheel — the difficulty classifier learns which subtasks needed which tier;
`frequency_report` flags synth>15 / Opus>3 as a brief-quality bottleneck).

**Frontier** (`frontier_core.py`) — `frontier_escalate` reaches Opus only through **three gates**
(mode+key · classifier says frontier-novel · V4-Pro failed twice / opinions disagree) via
**compress-then-reason** (V4-Pro compresses to a ~12K brief ~$0.02, Opus reasons ~$0.16). Output →
`FRONTIER_PLAN.md` + RAG/KG, then through `directive_verify` (advisory). Separate frontier USD caps.

Brief assembly is deterministic (`brief_assemble`): the weak local model writes only
`current_blocker`+`decision_needed`; goal/constraints/code come from PLAN.md + KG + RAG. Cloud output
is **gated** (`directive_verify` checks assumptions vs real repo, APIs exist, tests prescribed;
`compare_directives` for two-opinion agreement) before any edit.

---

## 5. The plan/execute split (expensive-plan → cheap-execute)

For a *substantive* build the local model drifts (loses cwd, re-derives structure, skips the
done-check). Fix: plan once on the expensive model, execute literally on the cheap one.

- **`classify_plan_need(task)`** (rule-based, no LLM) → NEEDS_PLAN vs NO_PLAN; `task_classification` span.
- **`plan_route(task, phase)`** → advises PLAN→`synth`/V4-Pro vs EXECUTE→local; `tier_routing` span.
- **PLAN phase:** `brief_assemble(full)` → `conductor_synthesize` (V4-Pro) writes **PLAN.md** to the
  **`workflow-plan-contract`** schema (TASK, absolute WORKING_DIRECTORY, FILES, a FILE SPEC per file
  with exact signatures + prose algorithm + edge cases, concrete DONE_CONDITION, RISKS).
- **`plan_lint(repo)`** — deterministic completeness gate over the PLAN.md *document* (distinct from
  `directive_verify`, which gates a JSON directive; recognizes nested `###` FILE-SPEC sub-headers).
  Bounces a thin plan back to the planner; bounded by `PLAN_LINT_MAX_ROUNDS`.
- **EXECUTE phase** (**`workflow-execute-from-plan`**): confirm cwd = WORKING_DIRECTORY → implement
  files in order → `verify` per file → checkpoint each green file → done only when DONE_CONDITION is
  *literally* met. On any gap the plan didn't answer, **`request_plan_revision`** routes the question
  to V4-Pro and appends the answer to PLAN.md (bounded) — **never invent**; if the planner is
  unavailable, fall to `workflow-stuck`.

---

## 6. Observability

`otel_emit.record(name, attrs, status)` — fire-and-forget OTel span → **Phoenix OTLP gRPC :4317**.
Each server ships its own self-contained `otel_emit.py` (no cross-server dependency); Phoenix down →
spans drop silently. Mirrored to a live stream (`lib/livelog`) for `hm watch`. Key spans:
`role_resolved`, `rung_fell`, `task_classification`, `tier_routing`, `plan_lint`,
`plan_revision_requested`, `quality_check`, `trajectory_recorded`.

---

## 7. Operator surface — the `hm` CLI

Dispatch sugar over `scripts/*.sh` (each verb maps to a standalone script).

| Verb | Does |
|---|---|
| `hm up [--MODE]` | start servers; `--local`/`--free`/`--full`/`--frontier` (conductor ceiling, presence-gated with fall-through; persisted to `~/.hermes-max/conductor/mode` + `.env`) |
| `hm down` · `restart [srv]` · `status` | lifecycle |
| `hm health` · `preflight` | scriptable health / pre-task validation (auto-fix, BLOCKING gate) |
| `hm watch` · `observe` | live tool-call stream / wall-time waterfall |
| `hm run "task"` | launch the hermes agent on a task |
| `hm smoke` · `regression` · `eval` · `bottleneck` | end-to-end proof / baseline-gated probes / capability battery / timing |
| `hm cost` | conductor spend + Opus-sparing proof |
| `hm dev` · `attach` | tmux cockpit (hermes + watch + status) |

Mode is the *ceiling*; the conductor's per-subtask gating decides actual use. `_resolve_mode` walks
down the tiers if a tier's key is absent (`--frontier`→`--full`→`--free`→`--local`).

---

## 8. End-to-end engineering loop (user invocation → done)

`hm run "Implement X with tests"` (or type into the `hm dev` cockpit):

```
USER TASK
  │
  ├─ preflight/health green (hm)                          ← stack up, conductor mode set
  │
  ├─ workflow-task-start → retrieve-before-act            ← RAG search_code, KG recall_about,
  │                                                          repo_map / codegraph / lsp orientation
  │
  ├─ classify_plan_need(task)                             ← rule-based, no LLM
  │     ├─ NO_PLAN  → stay local, go to edit loop
  │     └─ NEEDS_PLAN ↓
  │
  ├─ PLAN PHASE  (plan_route → tier=synth/V4-Pro)         ← span: tier_routing{phase:plan}
  │     brief_assemble(full) → conductor_synthesize       ← V4-Pro writes PLAN.md (plan-contract)
  │     plan_lint(repo)  → incomplete? back to synth      ← bounded revisions
  │
  ├─ EXECUTE PHASE  (local 35B, workflow-execute-from-plan)
  │     for each file in PLAN.md, in order:
  │        edit  →  quick_check  (lint+type per edit)     ← workflow-edit-format
  │        plan silent on a decision? → request_plan_revision (V4-Pro)   ← never invent
  │        verify(file) + deep_verify / property_test     ← workflow-verify-enhanced
  │        quality_check(file)  (advisory texture)        ← workflow-quality-bar
  │        checkpoint(label, verify=True)                 ← green, recoverable
  │
  ├─ WATCHDOG (out of band): spiral? stall? budget blown? ← check_spiral / check_stall / check_budget
  │        → workflow-stuck-detect-reset
  │
  ├─ HARD subtask? conductor ladder (workflow-conductor): ← stingy, classifier-gated
  │     conductor_plan(signals) →
  │        verifiable+hard → parallel_draft (verifier picks the winner)
  │        ambiguous+hard  → conductor_steer / conductor_synthesize → directive_verify (gate)
  │        frontier-novel + synth-failed×2 → frontier_escalate (Opus, 3-gated, compress-then-reason)
  │
  ├─ DONE check: every DONE_CONDITION clause met & verified, KG decision recorded   ← not model opinion
  │
  └─ record_trajectory (success/fail)                     ← localize_failure on fail;
        → weekly self-improve / dream-cycle compound         the compounding flywheel
```

Every step degrades: no cloud key → conductor rungs OFF, the local model grinds it out free; a dead
server → that tool is "unavailable" and the agent routes around it. The expensive model is paid only
for the *plan* (one rich turn + bounded revisions); the local 35B does all token-heavy implementation.
**Compress-then-reason, inverted: buy the strong model's judgment once, up front, as a brief the cheap
model executes without drift.**

---

## 9. The inference fabric — providers are config, not code

Everything in §§1–8 asks for a **role**; one isolated library (`lib/inference/`)
turns that role into an actual provider call. It is the SOLE seam between the
harness and the model backends. The mental model in one paragraph:

> An MCP server (or the conductor) calls `run_role("code_plan", messages)`. The
> fabric looks up the active **mode**'s chain for that role, walks it, and returns
> the first rung that is **present** (its key is set), **under the spend ceiling**,
> and **has rate-bucket headroom**. A missing key silently drops its rung. With
> nothing but the local vLLM block present, every role resolves to the local model
> — fully sovereign, $0. The harness never names a provider.

### The config trinity (edit these, never the code)

| File | Answers | Loaded by |
|---|---|---|
| **`inference.yaml`** | *What backends exist* — base URLs, model ids, costs, rate limits, per provider. A block with an unset `api_key_env` is skipped. | `lib/inference/config.py` |
| **`roles.yaml`** | *Which backend serves which job* — each role → an ordered `provider.model` chain. | `lib/inference/roles.py` |
| **`modes.yaml`** | *The cost/quality posture* — six named presets that override the coding/research chains and set a spend ceiling. `hm mode <name>` swaps them live. | `lib/inference/roles.py` |

`INFERENCE_MODE` (the posture name, default `free`) is the spend ceiling +
chain-swap; it's persisted to `~/.hermes-max/mode` by `hm mode` and read live.

### The library (one seam, ~7 small files)

```
config.py    inference.yaml loader: providers, models, tiers (local/free/paid/frontier), cost math
roles.py     roles.yaml + modes.yaml: role→chain, mode override, ceiling, satisfiability
buckets.py   rate-bucket tracker: has_headroom() PRE-checks (never absorb a 429), header parser
ledger.py    central cost ledger: every call, $0.000000, free-vs-paid split, remaining free RPD
adapters.py  the ONLY wire calls: openai_compatible + anthropic (pure httpx, no SDKs)
router.py    run_role(): walk the chain, first present+under-ceiling+has-headroom rung; never raises
```

`grep -rl "api.deepseek\|api.groq\|openrouter.ai\|api.cerebras\|api.anthropic" --include=*.py`
finds provider URLs in exactly one place: `inference.yaml` (data) and the conductor
registry being migrated out (see [migration.md](migration.md)). A new provider of an existing
`kind` is a YAML edit (copy the commented `example_custom` block + one env var) — no
Python changes. That's the open-bazaar intent.

**Two refinements worth knowing:**
- **Local model auto-discovery.** `local_vllm` sets `base_url_env: VLLM_BASE_URL`
  (never a hardcoded IP) and `discover_model: true`; the served model id is fetched
  once from `GET ${VLLM_BASE_URL}/v1/models` (`models[0].id`) and cached. The local
  tier is present **only** when `VLLM_BASE_URL` is set AND the endpoint answers;
  unreachable → silently absent (the harness keeps its own local fallback). There is
  no `VLLM_MODEL_ID` to set.
- **The default gateway (catch-all).** A top-level `default_gateway` block (OpenRouter)
  is the fallthrough: when **every** named rung in a role's chain is absent or
  exhausted, the router calls the gateway's `default_model` (Kimi-K2.6:free). So a
  user with **only** `OPENROUTER_API_KEY` set has a fully working cloud system. The
  gateway is free-tier, so it's skipped under the `local` ceiling (proceed_local).
  Endpoints are env-driven and the fabric reads keys from `.env` as well as the live
  environment.

---

## 10. Backend honesty table

What each rung is actually good for (fold-in from live results; verify prices live):

| Backend | Good for | Cost (in/out per M) | Context | Throughput | Free? | Honest caveat |
|---|---|---|---|---|---|---|
| **local vLLM** (Qwen3.6-35B-A3B) | the always-present executor; private, sovereign | $0 / $0 | 262K | ~50 tok/s single-stream | yes | slow, sequential — deep research is overnight-grade, not interactive |
| **DeepSeek-direct** V4-Pro / V4-Flash | the cheap quality anchor: V4-Pro planning, V4-Flash driving | Pro $0.435/$0.87 · Flash $0.14/$0.28 (cache-hit $0.0036/$0.0028) | 1M | fast | no | cheap, not free; direct-provider terms |
| **DeepInfra** V4-Pro/Flash | US-hosted fallback for DeepSeek-direct | ~$1.30/$2.60 (verify live) | 1M | fast | no | list price higher than direct; fallback only |
| **OpenRouter :free** | the free planner/synth: Kimi-K2.6 (1M ctx), R1, Qwen3-Coder, gpt-oss-20b | $0 | up to 1M | varies | yes | 20 RPM, 1000/day per model **after a one-time $10 deposit**; roster rotates |
| **Groq** (8B / Scout / gpt-oss-120b) | the fan-out workhorse — PER-MODEL buckets on one key | $0 | 131K | 8B ~560 tok/s | yes | buckets are per-model: 8B 14,400/day, Scout & 120b 1,000/day |
| **Cerebras** (gpt-oss-120b / GLM-4.7) | a single fast chunked-synthesis rung | $0 | 64K | ~30K tok/s | yes | **5 RPM** + 64K ctx → great for one synth call, useless for fan-out |
| **Anthropic** Opus 4.8 | the spare frontier rung | $5 / $25 (cache $0.50) | 1M | — | no | triple-gated; ~$0.08–1.25/call; a genuinely-hard-problem rung only |

---

## 11. Modes — the cost/quality posture toggle

One word reassigns the coding role chains. `hm mode <name>` switches live;
`hm mode --list` prints the table; the active mode + today's spend show in the
`hm dev` cockpit. Ordered by appeal (the default first):

```
MODE            COST/MO   GPU?  POSTURE
free            $0.00     yes   Kimi-K2.6-free plans, local executes. DEFAULT. Best with Thor/Spark.
full-local      ~$1.50    yes   V4-Pro plans, local executes. V4-Pro judgment over Kimi-free.
full            ~$17      no    V4-Pro plans, V4-Flash executes. No GPU. ~10% of Code Max.
frontier-local  ~$45      yes   Opus plans, local executes. Sovereign + true frontier planning.
frontier        ~$60      no    Opus plans, V4-Flash executes. Closest to Claude Code. Hard sessions.
local           $0.00     yes   Pure local, no API. Air-gapped floor.
```

**Honest framing:**
- **free and full-local are the headline value** — near-frontier planning (Kimi-free
  or V4-Pro) + free/sovereign local execution, $0–1.50/month. The whole point of
  owning the GPU.
- **full** is the no-GPU on-ramp — anyone can run it, ~$17/mo, ~10% of Claude Code Max.
- **frontier / frontier-local** are real but the value narrows vs a Claude Code
  subscription; offered for unlimited-usage / no-rate-limit / private-execution
  reasons, not pure cost. Use `frontier` for a genuinely hard session, then drop
  back to `full-local`.

Safety: `free` (and any `requires_gpu` posture) **warns** rather than silently
falling back when no local vLLM is up — the user must explicitly choose to pay
(`hm mode full`). If `OPENROUTER_API_KEY` is absent in `free`, the plan chain falls
through to local planning automatically — no error, no cost.

**Agent-loop backend swap (Option A).** A posture also decides which model the
Hermes *loop itself* runs on (distinct from the conductor's per-role routing).
`hm mode <name>` runs `scripts/set_mode.sh`, which resolves the posture's executor
(the first present, under-ceiling rung of `code_execute`) and atomically rewrites
the `model:` block of `~/.hermes/config.yaml`: local-executor postures (free /
full-local / frontier-local / local) → local vLLM (`$VLLM_BASE_URL`, no key);
remote-executor postures (full / frontier) → DeepSeek-V4-Flash via the funded
DeepInfra endpoint, key resolved from `.env`. It backs up to `config.yaml.bak` and
captures the original `model:` block once to `config.model.orig.yaml`, so the swap
is fully reversible (`hm mode free` rewrites the loop back to local). Endpoints are
env-driven (`VLLM_BASE_URL`, `DEEPINFRA_BASE_URL`, …) — never hardcoded — and the
fabric reads keys from `.env` as well as the live environment. The funded provider
leads: `--full*` chains put `deepinfra.{planner,driver}` first, with `deepseek_direct`
a cheaper alternative the operator can promote in `roles.yaml` with no code change.
Skip the live swap with `HM_NO_HERMES_SWAP=1`.

### The default-mode evaluation (operator)

The proof task (Bloom filter → Groth16) is run under `free` then `full-local`
back-to-back, **identical local executor**, to isolate the planning-quality delta
between Kimi-K2.6-free and V4-Pro — i.e. is Kimi-free a good enough permanent
default, or does V4-Pro's architectural judgment justify the ~$1.50/mo? Both
trajectories are recorded so the GEPA loop and `hm cost` capture the evidence.

---

## 12. Three worked configs

**(a) Zero-key pure-local.** No `.env` keys at all. Every role resolves to
`local_vllm.driver`. `hm mode local` (or `free` with a GPU). $0, air-gapped.

**(b) Lean — one cheap paid key + free tiers.** Set `DEEPSEEK_API_KEY` +
`GROQ_API_KEY` + `OPENROUTER_API_KEY`. `hm mode full-local`: V4-Pro plans (~$0.05/day),
local executes free, Groq/OpenRouter handle steer/repair/fanout. ~$1.50/mo.

**(c) Maximalist constellation.** All keys present (DeepSeek, DeepInfra, OpenRouter,
Groq, Cerebras, Anthropic). Pick any posture; `full-local` for daily driving,
`frontier-local` for a hard session. The shipped `config/inference.example.yaml` IS this
constellation — copy it, fill what you have, missing keys drop their rungs.

The roles.yaml diff between configs is *nothing* — the chains are the same; which
rungs are **present** changes. That's the design: the topology is fixed, the keys
you hold decide which rung actually answers.

---

## 13. The deep-research truth

Retrieval is cheap; per-sub-question model reasoning is the wall-time. So the design
minimizes big-model calls and parallelizes the rote work across independent free
buckets:

1. **research_plan** — 1 call: decompose into sub-questions + a saturation criterion.
2. **Retrieval fan-out** — zero-LLM, parallel (SearXNG + arxiv + openalex + hn +
   stackexchange + github), markdown via trafilatura→Crawl4AI→Jina.
3. **research_fanout** — many small fast calls (query-expand, filter, dedup, extract)
   load-balanced across **Groq buckets** (8B at 14,400/day is the workhorse).
4. **Corroboration** — zero-LLM: local Qwen3-Embed/Reranker + ≥2-source gate + KG.
5. **research_synth** — 1 call over the fixed-size evidence set: Kimi-K2.6-free
   (1M ctx, no chunking) → Cerebras/Groq gpt-oss-120b.
6. **Compound** — verified claims + citations into RAG (sqlite-vec) + KG.

Honest tradeoffs (see [research-engine.md](research-engine.md)): **local-only research is slow & sequential &
deep** — fine overnight, painful interactively. **API fan-out is parallel & faster
but still bounded** by RPM and by the 2 synthesis-class calls — the win is
parallelism across free buckets, not raw speed. Cerebras is fast but 5 RPM (synth
rung, not fan-out). Groq's per-model buckets make it the fan-out workhorse.
OpenRouter :free is great for Kimi synthesis + R1 planning, not high-RPM fan-out.

---

## 14. Cost transparency — `hm cost`

The ledger records **every** call: tokens + USD in `$0.000000` (six decimals —
fan-out costs live in the 4th–6th). Free providers record real token counts at
`$0.000000`, so you see volume even at zero cost. `hm cost` shows:
- today/week/month totals, broken down by provider, model, and role;
- a **free-vs-paid split** (tokens served at $0 vs tokens that cost money) — how much
  the free constellation is saving you;
- **remaining daily free budget** per free model (from the bucket tracker), so you
  know how much fan-out budget is left today.

The bucket tracker parses Groq / OpenRouter / Cerebras rate-limit headers into a
unified remaining-RPM/TPM/RPD view; the router **pre-checks** it so it skips a rung
*before* sending — we never absorb a 429.

---

## 15. What closes the gap to Opus — and what doesn't (be honest)

**Closes the gap (ordered by ROI):**
1. **V4-Pro planning with a gap-free PLAN.md** — the dominant lever. Exact signatures
   + prose algorithms + edge cases + a concrete DONE_CONDITION mean the executor
   transcribes, never designs. ~80% of the Opus-vs-35B gap on planned tasks is a
   *planning* gap.
2. **Test-first contracts** — ship failing tests with each FILE SPEC; "done" is
   mechanical, drift impossible.
3. **LSP-diagnostic repair loop** — on verify-fail, feed the exact compiler diagnostic
   + symbol context back for a targeted fix (~50ms) before any regen.
4. **Verifier-guided best-of-N** — sample N drafts, select by verify/property results
   (not voting); parallelize drafts on free-tier models in `code_draft_pool`.
5. **Iterative web-search-on-error** — search the exact error/stack signature, retry;
   cheaper than escalation, fixes most "unknown library API" failures.
6. **Property + metamorphic testing in verify** — gate core-logic files.

**Does NOT close the gap (don't pretend it does):**
- A bigger *local executor*. With a gap-free plan, 35B executes reliably; a bigger
  model helps debugging, not clean-path execution.
- More research fan-out. Helps novel unknown APIs, not "implement what the plan says."
- More providers. Provider count is not quality — **plan quality is quality.**

**The genuine ceiling (honest about Opus):** on genuinely novel hard problems — a
tricky concurrency bug, a subtle architectural call with no known pattern — Opus's
raw reasoning advantage is real and not fully closed by harness engineering. The
triple-gated `code_frontier` rung exists exactly for this: budget one Opus call per
genuinely hard task when verify refuses to go green. At $0.08–1.25/call it's cheap
relative to the time otherwise lost.

---

## 16. The maintainability promise

The free/cheap LLM landscape is an open bazaar that rotates monthly. That's why
providers live in **YAML** (swap a departed `:free` model in 30 seconds), why the
inference layer is **one isolated lib** with a single role API, and why the repo
stays out of the Frankenstein swamp: one seam, three config files, an honest ledger.
After this fabric the repo is *smaller and cleaner* — provider knowledge consolidated,
not scattered. Adding or removing a backend, or changing your whole cost posture, is
a config edit (`inference.yaml` / `hm mode`), never a code change.

---

## 17. Keeping the model roster current

The free/cheap LLM bazaar rotates monthly, so model ids drift. `lib/inference/roster.py`
validates every configured id at `hm up` (warn-only) and `hm health` (full ROSTER
section). For each `provider.slot`:

1. Check `KNOWN_DEPRECATED` (a dict you populate as models retire).
2. Probe the provider's `/models` where available (cached 1h, never slows a task):
   openai-compatible → `GET {base}/models`; `local_vllm` → reuse the discovery
   result; `anthropic`/`cerebras` have no `/models` → `unconfirmed` (rely on
   `KNOWN_DEPRECATED` + a first-call 404).

`hm health` prints one line per slot:

```
  ✓ openrouter.synth_free   moonshotai/kimi-k2.6:free   confirmed
  ✗ groq.synth_oss          openai/gpt-oss-120b         missing
      → NOT in provider /models — update id in inference.yaml
```

A deprecated/missing slot is a **warning, not an error** — the system starts anyway.
Every model slot in `inference.yaml` carries a `# verified: YYYY-MM-DD` comment.
When one is flagged: find the replacement, change the id in `inference.yaml` (one
line), update the date, `hm health` re-confirms. **No code change. Ever.**

## 18. Free uplift (optional plugin)

`plugins/free_uplift/` is a proactive coherence checkpoint: after a file passes
verify (before checkpoint), spend **one Kimi-K2.6:free** call to confirm the
implementation matches its FILE SPEC and the already-completed interfaces. Catches
drift at $0.

**It is a plugin, not core.** `conductor_policy.py`, `mcp-escalation`, and
`mcp-research` have zero knowledge of it. The conductor exposes exactly one generic
extension point — `register_post_verify_hook(fn)` / `run_post_verify_hooks(...)` —
and `plugins/load_plugins.py` (run at `hm up`) registers the plugin against it **only
if** all hold: `INFERENCE_MODE_FREE_UPLIFT=true`, `OPENROUTER_API_KEY` present,
Kimi-K2.6:free not deprecated, and daily free-RPD headroom > 200. Otherwise it logs
`not registered` and the core loop runs unchanged.

Guardrails: ≤2 calls/file, ≤10/task; skips silently when the rate bucket is tight;
never blocks the loop on error (a failed call counts as CLEAN). Toggle with
`hm up --free-uplift` / `--no-free-uplift`; `hm mode` shows `[free-uplift: ON/OFF]`;
`hm cost` shows a dedicated `free_uplift` line. When Kimi-K2.6:free is deprecated the
plugin stops registering automatically — update the id in `inference.yaml` and it
returns on the next `hm up`. No other file changes. This is the isolation contract:
an optional capability is one directory + one config flag, and its absence is
invisible to the core.
