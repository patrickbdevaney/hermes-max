# CLAUDE_conductor.md — The Hybrid Conductor: Local-First Driver + Opt-In Cloud Synthesis, Steering & Parallel-Draft

You are adding an OPTIONAL, fully-presence-gated "conductor" capability to the completed `hermes-max`
harness. The local Qwen driver does ALL high-volume execution and orchestration at $0. Cloud models are
invoked — rarely, behind tools, for cents-or-free — ONLY for work the local model can't do alone, and
ONLY for whichever providers the operator has actually configured. With zero cloud keys the system runs
entirely on the local harness + its own model. Work in STAGES, in order; each independently committed,
smoke-tested, validated. Read the whole spec first. Report after each stage.

## THE CORE PRINCIPLE: ADDITIVE, PRESENCE-GATED, NEVER REQUIRED, NEVER A BACKEND SWAP
The conductor uses AS MANY OR AS FEW provider keys as the operator has. Cleanly separated layers (this
separation is what keeps it from being a mess — implement each as a distinct, independently-testable
piece):
1. **BASE (zero keys):** the harness runs on local vLLM (`$VLLM_BASE_URL`) or a default OpenAI-compat
   endpoint. No conductor. This must always work.
2. **ROLES (what kind of help):** `steer` (frequent cheap nudges), `synthesize` (rare deep
   decomposition / novel architecture), `parallel_draft` (verifier-selected best-of-N on VERIFIABLE
   subtasks), `escalate` (the rarest hardest kernel). A role is ACTIVE iff ≥1 provider in its chain has
   a key present.
3. **CHAINS / POOLS (who serves a role, in order):** each role has an ordered provider chain (or, for
   parallel_draft, an unordered POOL). At call time, SKIP any rung whose key is absent; use the first
   present rung; on failure/429/5xx/deprecation **silently fall to the next present rung and log it**
   (one-line trace entry, no user-facing interruption, never raise to the core loop).
4. **EVERYTHING IS A STATELESS TOOL, NOT A MODEL SWAP.** The Hermes loop runs on the local Qwen the
   entire time. Cloud models are draft generators / advisors behind MCP tools. The local model stays
   the orchestrator + integrator. NO hot-swapping the Hermes backend, ever (this is the anti-Frankenstein
   line that the operator's prior browser-use core-loop modification taught the hard way).

If a role has NO keys present, it is OFF and the driver proceeds local-only for that need. If ALL roles
are off, the system is the bare harness. Nothing breaks; nothing is required.

## THE HONEST ECONOMIC + BRITTLENESS FOOTING (verified May-2026; the README must state this plainly)
- **No free tier is production-durable, and the README must say so.** Groq hollowed out its frontier
  catalog post-Nvidia-acquisition (Kimi/Maverick deprecated; only GPT-OSS-120B/20B, Qwen3-32B,
  Llama-4-Scout remain — mediocre vs the local Qwen, useful only as draft diversity). Gemini 2.5 Pro
  left the free tier 2026-04-01; **Gemini 2.5 Flash is 20 RPD on the operator's console**. DeepSeek's
  5M grant is one-shot/new-account/expired for the operator. NVIDIA NIM's own model card calls it a
  "trial service." Cerebras is a real free asset (free-account console: **GLM-4.7 @ 64K ctx and
  GPT-OSS-120B @ 65,536 ctx**, both 5 RPM / 150 RPH / 2,400 RPD / 1M TPD — preview, can change).
  THE DESIGN'S RESPONSE TO THIS BRITTLENESS IS THE WHOLE POINT: local-viable foundation + presence-
  gated optional cloud + silent fallback + honest README. A model/endpoint vanishing degrades the
  system gracefully; it never breaks it.
- **Verified DeepInfra pricing (operator's paid default):** DeepSeek **V4-Flash $0.10 in / $0.20 out,
  $0.02 cached** per 1M, 1M ctx, fp4. DeepSeek **V4-Pro $1.30 in / $2.60 out, $0.10 cached** per 1M,
  1M ctx. So a steer call (~6K in + 1K out) ≈ **~$0.0008 uncached → ~$0.0003 cached** (hundredths of a
  cent); a synth call (~30K in + 3K out on V4-Pro) ≈ **~$0.047 uncached, far less cached**. Opus
  escalation ≈ ~25¢/call. Realistic heavy month ≈ **$3-8** (Opus dominates despite rarity).
- **STEER DEFAULT CORRECTED:** the steer default is **DeepSeek V4-Flash on DeepInfra**, NOT Cerebras.
  Reason from the operator's own numbers: V4-Flash costs hundredths of a cent, has 1M-ctx headroom,
  cache pricing, the reliability of the already-paid key, and is a stronger model than GLM-4.7 (which
  benched mid-tier with structural coding bugs). The few-hundredths-of-a-cent savings from free Cerebras
  is not worth its rate-limit fragility / preview-deprecation risk AS THE DEFAULT. Cerebras stays a
  deprioritized free FALLBACK rung (and a parallel_draft pool member, where free + diverse is exactly
  what you want).
- **The frontier model is a STATELESS SYNTHESIZER, not the agent.** Local Qwen runs the whole horizon;
  cloud gets a brief, returns a directive. So long-horizon-agentic model strengths (e.g. Kimi's tool-call
  coherence) are largely irrelevant here — per-call reasoning on a compressed brief is what matters,
  which is why DeepSeek V4 leads the synth chain and Kimi is a later config rung.

## NON-NEGOTIABLE DISCIPLINE (anti-Frankenstein)
Extend via native surfaces only; never modify Hermes's core loop; extend the existing `mcp-escalation`
+ `mcp-search` servers and add a brief-assembler, all exposed as MCP tools the driver MAY call; the
local model's `$VLLM_BASE_URL` is NEVER touched (cloud roles get their OWN endpoints); provider-agnostic
via config; back up config before edits; commit per stage; graceful degradation tested for every piece.

## EXISTING STACK
hermes-max: mcp-escalation (difficulty classifier, tiered routing, USD cap, surgical handoff),
mcp-search (verifier-guided best-of-N — THIS is where parallel_draft extends), mcp-verify (quick_check
lint→type→tests — THE selector for best-of-N), codebase-rag (graph+rerank), knowledge-graph
(core-memory), checkpoint, watchdog (stuck-summaries), observability. PLAN.md externalization. The
conductor reuses ALL of these.

---

## THE DEFAULT CHAINS / POOLS (ship exactly these; YAML-overridable per Stage 1)

### synthesize role (deep, ambiguous, no cheap oracle) — ordered chain, first present wins, fall through:
1. **DeepInfra** (paid default; cheapest DeepSeek/Kimi, cache pricing, no-train/no-disk, US, SOC2/ISO27001) — DEFAULT FIRST; default model `deepseek-ai/DeepSeek-V4-Pro`
2. **Fireworks** (US, SOC2 Type II + HIPAA, no-log open models)
3. **Together** (US, ZDR)
4. **DeepSeek-direct** (cheapest at source BUT direct-provider-hosted, direct provider terms, capacity freezes — sits below US hosts BY DESIGN; opt-in)
5. **Kimi via Moonshot** (Singapore entity; long-horizon strength wasted on stateless synth — later rung)
6. **Claude Opus** (Anthropic; = the escalate role, top of ladder)

### steer role (frequent cheap nudges) — ordered chain, CHEAP-RELIABLE-FIRST (corrected):
1. **DeepSeek V4-Flash via DeepInfra** (default; hundredths of a cent, 1M ctx, cache, reliable) — DEFAULT FIRST
2. **Cerebras** (free; GLM-4.7 or GPT-OSS-120B @ 64K ctx; deprioritized fallback — try when present & V4-Flash absent/over-budget; fall through on 5 RPM / 2,400 RPD / preview-deprecation)
3. **Groq** (free; Qwen3-32B / GPT-OSS-120B; last cheap fallback; 30-60 RPM / 1K RPD)
4. **Gemini 2.5 Flash** (20 RPD on free-account console — tracked last-resort asset)
- Steer is ON automatically iff ≥1 steer key present; else OFF and the driver self-recovers locally.

### parallel_draft POOL (verifier-selected best-of-N on VERIFIABLE subtasks) — UNORDERED, fan out across ALL present free/cheap sources for cross-family DIVERSITY:
- **Cerebras GLM-4.7** (free, 64K ctx)
- **Cerebras GPT-OSS-120B** (free, 65,536 ctx)
- **Groq GPT-OSS-120B** (free, 30 RPM / 1K RPD)
- **Groq Qwen3-32B** (free, 60 RPM / 1K RPD)
- **Groq Llama-4-Scout** (free, 30 RPM / 1K RPD)
- **DeepSeek V4-Flash via DeepInfra** (cheap quality anchor; one paid candidate raises the pool's ceiling)
- Pool membership is presence-gated; absent keys are simply not drafted from. N = number of present
  sources (cap configurable, default ≤5). The VALUE is cross-family diversity (5 different training
  distributions), NOT temperature sampling one model.

### escalate role (rarest hardest kernel) — fired ONLY by Stage-4 verify-gate logic:
1. **Claude Opus** (Anthropic) — only when synth fails the verify gate twice or two synth opinions
   disagree on a high-blast-radius decision. USD-capped.

---

## STAGE 0 — EVALUATION (use one-shot free/trial grants HERE, not in production)
`scripts/eval-synthesis.sh`: assemble 5-10 real briefs from harness state (Stage 2); send each to the
candidates the operator has access to (DeepInfra paid; Cerebras/Groq free; NIM trial / OpenRouter
`:free` flagged data-share, nothing sensitive). Score directive quality: passes verify first-try?,
`assumptions` correct?, executes without thrash? Output a ranked quality+cost table.
**Stage-0 DoD:** runs real briefs vs ≥2 candidates; ranked table; confirms DeepInfra V4-Pro as synth
default (or recommends another if it wins on the operator's work). Committed.

---

## STAGE 1 — THE PRESENCE-GATED ROUTER (three separated pieces — "not a mess" is won here)
Extend `mcp-escalation` with:
- **(a) Provider registry** — pure data dict: `{provider_id: {base_url, env_key_name, openai_compatible,
  models:{steer,synth,draft}, max_ctx, rpm, rpd, tpd, billing_region, trains_on_data}}`. Adding a
  provider = one entry + one env var. No logic.
- **(b) Presence resolver** — given a chain/pool, return only rungs whose `env_key_name` is set. Pure
  function, unit-tested across {0,1,several} keys. This is what makes "use as many or as few keys as
  you have" true.
- **(c) Role executor** — for ordered roles (steer/synth/escalate): walk present rungs; call first; on
  failure/429/5xx/timeout/deprecation **silently advance + log one-liner**; if none succeed → return a
  graceful "proceed local" signal (NEVER raise). For the parallel_draft POOL: fan out concurrently to
  all present pool members (respecting each provider's live RPM/RPD budget from the registry; skip
  exhausted; degrade to fewer candidates or N=1 local rather than failing).
- **Config precedence:** hardcoded sensible defaults < optional `conductor.yaml` (overrides per-role
  ORDER and per-rung MODEL strings; absent = defaults). Env vars supply KEYS (presence) ONLY, never
  order. Document this precedence explicitly.
- **USD cap** (reuse escalation cap): per-day + per-month; when hit, paid rungs return the local/free
  signal. Default low (e.g. $5/mo).
- **US-hosted-first is structural:** US hosts sit above DeepSeek-direct/Kimi in the synth order
  by default, so a present DeepInfra key is always preferred.
- **Stage-1 DoD:** ONLY DEEPINFRA_API_KEY set → steer+synth resolve to DeepInfra and work; unset all →
  roles OFF, local-only runs clean; bad DeepInfra + good Fireworks → synth silently falls with a logged
  one-liner; presence resolver unit-tested {0,1,several}; conductor.yaml reorders a chain; USD cap
  blocks→local; parallel_draft pool fans out across present free keys and respects RPM budget. Committed.

---

## STAGE 2 — THE BRIEF-ASSEMBLER (deterministic, not hand-written by the weak model)
`brief_assemble(task_id, blocker)` deterministically pulls: `goal`/`done_so_far` ← PLAN.md + checkpoint
log; `original_directives` ← user constraints verbatim; `architecture_state` + `failed_approaches` ← KG
+ watchdog stuck-summaries (prevents re-suggestion); `code_excerpts` ← codebase-rag graph/rerank,
token-budgeted; `constraints` + `success_criteria` ← PLAN.md + verify DoD. Local model writes ONLY
`current_blocker` + `decision_needed`.
- STRUCTURED schema (JSON/YAML), profiles: **compact** (≤8K, for steer), **full** (~15-30K, for synth)
  with `request_more` progressive disclosure. Cloud returns STRUCTURED DIRECTIVE: `ordered_steps`,
  `files_to_touch`, `apis_to_use`, `tests_to_write`, `pitfalls`, `confidence` (per-step), `assumptions`.
- **Draft brief variant:** for parallel_draft, a tight task-spec brief = the verifiable subtask + its
  acceptance tests + minimal context (each draft source gets the SAME brief; the verifier judges
  outputs, so the brief must contain the objective oracle).
**Stage-2 DoD:** builds compact/full/draft briefs from real state with local model writing only the
blocker; valid schemas; request_more works; sizes within budget. Committed.

---

## STAGE 3 — ADVISORY-WITH-VERIFY-GATE AUTHORITY (blind-genius + sighted-executor)
Cloud is smarter but BLIND; local is weaker but SIGHTED. Directives are ADVISORY, gated before commit.
`directive_verify(directive)`: (1) **assumption check** — verify each `assumptions` entry vs ACTUAL repo
state; false → reject/re-brief (append to `failed_approaches`); (2) **static gate** — compile/type/lint,
APIs exist (verify.quick_check); (3) **test gate** — write `tests_to_write` first, run; (4) **confidence
escalation** — low-confidence + high-blast-radius → second synth opinion (next present rung); disagree →
escalate or surface to human. Only after passing does the driver execute + checkpoint.
**Stage-3 DoD:** injected wrong assumption (nonexistent function) caught + NOT executed; static+test
gates run; low-confidence high-blast-radius triggers second opinion; verified directives execute +
checkpoint. Committed.

---

## STAGE 4 — PARALLEL-DRAFT (verifier-selected best-of-N; the optimal slop usage) — extend mcp-search
This is the optimal way to turn mediocre free models into an asset: **don't trust any one; sample many,
let the deterministic verifier pick.** Extend mcp-search (already verifier-guided best-of-N) to fan out
across the parallel_draft POOL.
- `parallel_draft(draft_brief, n)` tool:
  1. **GATE — only on VERIFIABLE subtasks.** Fires ONLY when the subtask has an objective oracle
     (implement-fn-so-tests-pass, fix-failing-test, well-specified-module). NEVER on ambiguous
     architecture (that's synthesize — no cheap oracle, can't select). The clean division:
     **slop-draft the verifiable, synthesize the ambiguous, escalate the frontier-novel.**
  2. **FAN OUT for DIVERSITY across families** — one draft from each present pool source (Cerebras
     GLM-4.7, Cerebras GPT-OSS-120B, Groq GPT-OSS-120B, Groq Qwen3-32B, Groq Llama-4-Scout, +optional
     DeepSeek V4-Flash anchor). Cross-family diversity > temperature sampling one model. Concurrent;
     respect each provider's RPM/RPD budget; skip exhausted sources.
  3. **SELECT by verifier, not by a model** — run every candidate diff through mcp-verify
     (lint→type→tests). Keep the one that passes (or passes most + fewest regressions). Tie-break by
     simplicity/diff-size. If NONE pass → fall back to the synthesize role (the subtask was harder than
     "verifiable-slop" assumed) or local.
  4. **Local model integrates** the winning diff (it stays the orchestrator; it reviews + commits via
     the normal checkpoint path). The slop models never touch the repo directly.
- **Why this rivals Opus on these subtasks:** well-specified codegen with a test oracle is exactly the
  regime where best-of-N across 5 families ≈ "the best idea any of 5 distributions had," selected
  objectively — at free-or-cents cost, no backend swap, no added per-turn latency on the critical path
  (drafts are concurrent).
- **Stage-4 DoD:** parallel_draft fires ONLY on a verifiable subtask (proven: an ambiguous subtask
  routes to synthesize instead); fans out across present free pool members concurrently; respects RPM
  budget (skips an exhausted Groq model cleanly); verifier selects the passing candidate; none-pass
  falls back to synth/local; winning diff integrated + checkpointed by the local model; works with only
  free keys (zero paid) AND degrades to N=1-local with zero keys. Committed.

---

## STAGE 5 — INVOCATION POLICY (stingy, classifier-gated; the full ladder)
Wire triggers to the existing difficulty classifier. `workflow-conductor` skill: driver handles
everything locally; reaches up only on classifier=HARD/novel, watchdog genuine-stuck after local
recovery failed, or a major architectural fork.
- **The ladder, by subtask type:**
  - *verifiable + hard* → **parallel_draft** (best-of-N free/cheap) → if none pass → synthesize
  - *ambiguous + hard* → **steer** (cheap nudge if active) → **synthesize** (deep brief→directive→verify)
  - *frontier-novel / synth-failed* → **escalate** (Opus) ONLY if synth fails verify twice or two
    opinions disagree on high-blast-radius. Log every Opus call; >3/project ⇒ brief quality is the
    bottleneck, fix the assembler.
- **Compounding:** record every directive/draft + outcome to the KG so the classifier learns which
  subtasks needed which tier (existing flywheel) and repeated blockers reuse prior directives.
- **Stage-5 DoD:** trace shows routine subtasks stay LOCAL; verifiable-hard routes to parallel_draft,
  ambiguous-hard to synth, frontier-novel to Opus; presence-gating skips inactive roles cleanly; USD cap
  respected; outcomes in KG; honest invocation-frequency + cost report for a real project (targets:
  synth ≤ ~15/project, Opus ≤ ~3, parallel_draft on verifiable subtasks, cost ≤ ~$0.40/session).
  Committed.

---

## .env.example (ship ALL providers, commented; keys = presence only, order lives in conductor.yaml)
```
# ============================================================================
# CONDUCTOR — fully optional. Set as many or as few as you have.
# Zero keys  → local vLLM / default endpoint only (bare harness). Nothing required.
# Order is controlled by conductor.yaml (optional); these vars only ENABLE rungs.
# ============================================================================

# --- BASE (always works) ---
VLLM_BASE_URL=http://localhost:8001/v1          # local model; NEVER overridden by cloud roles
# OPENAI_BASE_URL= / OPENAI_API_KEY=             # optional default OpenAI-compat endpoint

# --- SYNTHESIZE (deep). Default order: DeepInfra→Fireworks→Together→DeepSeek→Kimi→Opus ---
DEEPINFRA_API_KEY=                  # paid default — runs first (cheapest, no-train, US)
# FIREWORKS_API_KEY=                # fallback (US, no-log)
# TOGETHER_API_KEY=                 # fallback (US, ZDR)
# DEEPSEEK_API_KEY=                 # opt-in: direct-provider-hosted, direct provider terms (sits BELOW US hosts by design)
# MOONSHOT_API_KEY=                 # opt-in: Kimi (Singapore)

# --- STEER (cheap nudges). Default order: DeepSeek-V4-Flash@DeepInfra→Cerebras→Groq→Gemini-Flash ---
#   (V4-Flash steer needs NO extra key — served via the DeepInfra synth key)
# CEREBRAS_API_KEY=                 # free GLM-4.7 / GPT-OSS-120B @ 64K ctx; deprioritized fallback
# GROQ_API_KEY=                     # free Qwen3-32B / GPT-OSS-120B; cheap fallback
# GEMINI_API_KEY=                   # AI Studio; 20 RPD on this account — last-resort tracked steer

# --- PARALLEL_DRAFT POOL (verifier-selected best-of-N; fans out across ALL present free/cheap below) ---
#   Uses CEREBRAS_API_KEY + GROQ_API_KEY (above) + optional DeepInfra V4-Flash anchor. No new keys.
# CONDUCTOR_DRAFT_MAX_N=5           # cap on parallel candidates

# --- ESCALATE (rarest hardest kernel) ---
# ANTHROPIC_API_KEY=                # Claude Opus; only when synth fails verify twice / opinions disagree

# --- Model + cap overrides (optional; defaults in registry / conductor.yaml) ---
# CONDUCTOR_SYNTH_MODEL=deepseek-ai/DeepSeek-V4-Pro
# CONDUCTOR_STEER_MODEL=deepseek-ai/DeepSeek-V4-Flash
# CONDUCTOR_ESCALATE_MODEL=claude-opus-4-8
# CONDUCTOR_USD_CAP_MONTHLY=5
# CONDUCTOR_USD_CAP_DAILY=1
```
Eval-only providers (NIM trial, OpenRouter `:free`) live in the Stage-0 script with durability flagged
unverified — never a production rung.

---

## CROSS-CUTTING
- OTel spans: brief_assembled, role_resolved (role+provider), rung_fell (from→to+reason), draft_fanout
  (n sources, n passed), draft_selected (winner+verifier result), synth_called (model+cost),
  directive_verified/rejected, second_opinion, opus_escalated, usd_cap_hit, degraded_to_local.
- Cost ledger: per-call + cumulative per provider/role; enforces cap; feeds the Stage-5 report.
- **README (write it honestly):** (1) the four-layer model (base/roles/chains/tool-not-swap); (2)
  presence-gating + "as many or as few keys as you have"; (3) **a candid "API Brittleness" section** —
  state plainly that free tiers and model availability are volatile and subjective (Groq hollowed out,
  Gemini Pro pulled, Flash at 20 RPD, NIM is a trial, Cerebras is preview), that this is WHY the design
  is local-first with optional silent-fallback cloud, and that the system degrades gracefully when any
  endpoint vanishes; (4) default chain orders + why US-hosted-first is structural; (5)
  conductor.yaml override precedence; (6) the slop-draft division (verifiable→draft, ambiguous→synth,
  frontier→escalate); (7) that with all cloud keys unset it's the bare local harness.

## OUT OF SCOPE
- No free tier as a production REQUIREMENT (free = fallback/draft-pool/eval only).
- No core-loop modification; no touching `$VLLM_BASE_URL`; NO hot-swapping the Hermes backend model.
- No multi-key free-tier rotation / hidden iterators / ToS-evasion.
- No parallel_draft on ambiguous subtasks (no oracle = can't select = wasted slop).
- No long-lived cloud sub-agents (stateless tools only).
- No secrets in any brief, any route, ever.
- Order never in env vars (keys = presence only); order in conductor.yaml / registry defaults.

## REPORT (per stage)
What landed as {registry/resolver/executor/config/tool/skill/script}; Stage-0 ranking; native-vs-built;
smoke + validation PASS/FAIL per assertion (failures are signal); presence-gating tests (0/1/several
keys); silent-fall-with-log test; parallel_draft fan-out + verifier-select + RPM-budget + none-pass-
fallback tests; degrade-to-local + USD-cap tests; airtight cost/frequency numbers; git SHA.

## DEFINITION OF DONE
An optional, fully-presence-gated conductor extends mcp-escalation + mcp-search: a clean three-piece
router (registry / presence-resolver / role-executor) walks per-role chains and fans out the
parallel_draft pool, uses the first PRESENT rung, and silently falls-with-log on failure. Default synth
order DeepInfra→Fireworks→Together→DeepSeek→Kimi→Opus; default steer order
DeepSeek-V4-Flash@DeepInfra→Cerebras→Groq→Gemini-Flash (cheap-reliable-first, corrected from the
operator's own pricing); parallel_draft fans verifier-selected best-of-N across present free/cheap
families on VERIFIABLE subtasks only; US-hosted-first by construction; all override-able via
optional conductor.yaml. A deterministic brief-assembler (Stage 2) feeds an advisory-with-verify-gate
authority model (Stage 3). Invocation is stingy, classifier-gated, with the
parallel_draft / steer→synth / escalate ladder by subtask type and KG compounding (Stage 5). Evaluated
on the operator's real work first (Stage 0). The README is candid about API/model brittleness and frames
local-first-with-optional-cloud as the response to it. With ALL cloud keys unset the system is the bare
local harness and nothing breaks. The Hermes backend model is NEVER swapped — all cloud help is
stateless tools. Realistic cost ≤ ~$3-8/month; parallel_draft can run on free keys alone. Nothing
out-of-scope built; core loop untouched; config backed up; each stage committed; failures reported
honestly.