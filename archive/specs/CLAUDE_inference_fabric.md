# CLAUDE_inference_fabric.md — Modular Inference Backend Fabric + Research Fan-Out + Cost Ledger

## What this spec does

Three coupled goals, one clean architecture:

1. **Separate the inference-client layer from the MCP/harness layer.** Today model calls are
   scattered. Consolidate ALL backend calls (local vLLM, DeepSeek-direct, DeepInfra, OpenRouter,
   Groq, Cerebras, Anthropic) behind one small client library the MCP servers import. The MCPs
   stop knowing which provider answered; they ask for a *role* and get a result.

2. **Make the backend landscape a YAML the user edits, not code.** The set of desirable free/cheap
   LLM endpoints is an open bazaar that changes monthly. A user must be able to run with zero keys
   (pure local), or one key, or the full constellation, by editing one config — never by touching
   Python. Graceful fallback everywhere: a missing key or dead endpoint silently drops that rung.

3. **Redesign deep research as a parallel multi-bucket fan-out** with an honest cost ledger that
   reads provider rate-limit/usage headers and records tokens + USD in `$0.000000` format in one
   central place.

Hard constraints: anti-Frankenstein (one client lib, one ledger, one router — not a framework);
degrade gracefully (zero keys still runs local); the inference layer imports no MCP code and the
MCP layer imports no provider SDKs directly — they meet only at the client lib's role API. After
this spec the repo must be cleaner than before, not more tangled. Work in stages, commit each,
keep hm smoke green.

=================================================================================================
## STAGE 0 — Repo hygiene precondition (do this FIRST, it is the point)
=================================================================================================

Before adding anything, establish the separation so new code lands clean:

- Create `lib/inference/` as the ONLY place provider SDKs/HTTP clients live. Nothing else in the
  repo imports `openai`, `anthropic`, `groq`, provider base-URLs, or model-id strings directly.
- Grep the repo for direct provider calls (base URLs, `api.deepseek.com`, `api.groq.com`,
  `openrouter.ai`, `anthropic`, hardcoded model ids) currently embedded in mcp-escalation,
  mcp-research, mcp-search, etc. Inventory them in a short `MIGRATION.md` (what calls what today).
- Each MCP server that makes model calls must, by end of spec, call `lib/inference` via the role
  API only. No exceptions. This is the de-Frankensteining: one seam between harness and brains.
- Add a one-line architectural rule to the repo README and to `ARCHITECTURE.md`: "MCP servers
  request roles; the inference fabric chooses providers. Providers are config, not code."

**Stage-0 DoD:** `lib/inference/` exists; MIGRATION.md inventories every current direct provider
call; the no-direct-SDK rule is documented. Committed.

=================================================================================================
## STAGE 1 — The backend fabric config (one YAML, the open bazaar)
=================================================================================================

Single source of truth: `~/.hermes-max/inference.yaml` (with a checked-in
`inference.example.yaml` documenting every field). The user edits this; code never hardcodes a
provider. Structure:

```yaml
# inference.yaml — edit this, not the code. Any provider block can be deleted = that rung is off.
providers:
  local_vllm:
    kind: openai_compatible
    base_url: http://127.0.0.1:8001/v1      # the Thor vLLM endpoint already running
    api_key_env: null                        # local needs none
    models:
      driver:  { id: "qwen3.6-35b-a3b", ctx: 262144 }
    cost: { in_per_mtok: 0.0, out_per_mtok: 0.0 }   # local = free
    limits: { rpm: null, tpm: null }                # local = unmetered
    privacy: local

  deepseek_direct:
    kind: openai_compatible
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    models:
      planner: { id: "deepseek-v4-pro",   ctx: 1000000 }
      driver:  { id: "deepseek-v4-flash", ctx: 1000000 }
    cost:   { in_per_mtok: 0.435, out_per_mtok: 0.87, cache_hit_in_per_mtok: 0.003625 }  # V4-Pro
    cost_flash: { in_per_mtok: 0.14, out_per_mtok: 0.28, cache_hit_in_per_mtok: 0.0028 }
    limits: { rpm: 1000 }

  deepinfra:
    kind: openai_compatible
    base_url: https://api.deepinfra.com/v1/openai
    api_key_env: DEEPINFRA_API_KEY
    models:
      planner: { id: "deepseek-ai/DeepSeek-V4-Pro",   ctx: 1000000 }
      driver:  { id: "deepseek-ai/DeepSeek-V4-Flash", ctx: 1000000 }
    cost: { in_per_mtok: 1.30, out_per_mtok: 2.60 }   # DeepInfra V4-Pro list (verify live)

  openrouter:
    kind: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    models:
      synth_free:    { id: "moonshotai/kimi-k2:free",        ctx: 1000000 }
      plan_free:     { id: "deepseek/deepseek-r1-0528:free", ctx: 163840 }
      code_free:     { id: "qwen/qwen3-coder:free",          ctx: 262144 }
      extract_free:  { id: "openai/gpt-oss-20b:free",        ctx: 131072 }
    cost: { in_per_mtok: 0.0, out_per_mtok: 0.0 }     # :free models = $0 (rate-limited)
    limits: { rpm: 20, rpd: 1000 }                    # 1000/day requires the one-time $10 deposit
    notes: ":free roster rotates; ids are config so you can swap in 30s when a model leaves"

  groq:
    kind: openai_compatible
    base_url: https://api.groq.com/openai/v1
    api_key_env: GROQ_API_KEY
    models:
      fast_filter: { id: "llama-3.1-8b-instant",                    ctx: 131072 }
      fast_mid:    { id: "meta-llama/llama-4-scout-17b-16e-instruct", ctx: 131072 }
      synth_oss:   { id: "openai/gpt-oss-120b",                     ctx: 131072 }
    cost: { in_per_mtok: 0.0, out_per_mtok: 0.0 }     # free tier; per-MODEL buckets below
    limits_per_model:                                  # Groq buckets are PER-MODEL, not per-key
      llama-3.1-8b-instant:                    { rpm: 30, rpd: 14400, tpm: 6000 }
      meta-llama/llama-4-scout-17b-16e-instruct:{ rpm: 30, rpd: 1000,  tpm: 30000 }
      openai/gpt-oss-120b:                     { rpm: 30, rpd: 1000,  tpm: 8000 }

  cerebras:
    kind: openai_compatible
    base_url: https://api.cerebras.ai/v1
    api_key_env: CEREBRAS_API_KEY
    models:
      synth_fast: { id: "gpt-oss-120b", ctx: 65536 }
      synth_glm:  { id: "zai-glm-4.7",  ctx: 64000 }
    cost: { in_per_mtok: 0.0, out_per_mtok: 0.0 }
    limits_per_model:
      gpt-oss-120b: { rpm: 5, rph: 150, rpd: 2400, tpm: 30000, tpd: 1000000 }
      zai-glm-4.7:  { rpm: 5, rph: 150, rpd: 2400, tpm: 30000, tpd: 1000000 }

  anthropic:
    kind: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    models:
      frontier: { id: "claude-opus-4-8", ctx: 1000000 }
    cost: { in_per_mtok: 5.0, out_per_mtok: 25.0, cache_hit_in_per_mtok: 0.50 }
    notes: "spare frontier rung only; triple-gated in the conductor"
```

Rules the loader enforces:
- A provider block whose `api_key_env` is unset in the environment is **silently skipped** (not an
  error). Zero providers present except `local_vllm` → the system is fully local and free.
- `kind` selects the client adapter (`openai_compatible` or `anthropic`); adding a new provider of
  an existing kind is a YAML edit, no code.
- Model ids are strings in YAML precisely because the free bazaar rotates; swapping a departed
  `:free` model is a one-line change.

**Stage-1 DoD:** inference.yaml schema + example loaded by `lib/inference/config.py`; missing-key
blocks skip silently; a zero-key run resolves to local-only. Validate: unset all keys → only
local_vllm present; set each key → that provider appears. Committed.

=================================================================================================
## STAGE 2 — The role router (roles map to provider chains, presence-gated)
=================================================================================================

The harness never names a provider. It asks for a ROLE. `~/.hermes-max/roles.yaml` maps each role
to an ordered chain of `provider.model` rungs; the router walks the chain, skipping absent/over-cap
rungs, and returns the first success. This is the existing conductor philosophy, generalized and
moved into the fabric.

```yaml
# roles.yaml — ordered preference; first present + under-budget rung wins; all-fail => proceed_local
roles:
  plan:        # high-level planning / spec writing (expensive, infrequent)
    - openrouter.plan_free        # DeepSeek R1 free first — $0, strong reasoning
    - deepseek_direct.planner     # V4-Pro paid, the quality anchor
    - deepinfra.planner
    - local_vllm.driver           # last resort: local plans too (slower, weaker)

  drive:       # execution / implementation (high token volume)
    - local_vllm.driver           # default: slow-but-steady, free, private
    - deepseek_direct.driver      # V4-Flash: substitute for faster cloud driving
    - openrouter.code_free        # Qwen3-Coder free fallback

  steer:       # mid-execution nudge when the driver is stuck (infrequent, cheap)
    - groq.fast_mid               # Llama-4-Scout, fast + free
    - openrouter.extract_free
    - deepseek_direct.driver

  research_plan:    # decompose a research question into sub-questions
    - openrouter.plan_free        # DeepSeek R1 free
    - groq.fast_mid
    - local_vllm.driver

  research_fanout:  # HIGH-VOLUME rote work: query-gen, filter, dedup, extract
    - groq.fast_filter            # Llama-3.1-8B, 560 tok/s, 14,400 rpd — the workhorse
    - groq.fast_mid               # Scout, 1,000 rpd
    - openrouter.extract_free     # gpt-oss-20b free
    # local is intentionally NOT here: fanout wants many fast parallel calls, not one slow stream

  research_synth:   # final synthesis over filtered, corroborated evidence (1 call)
    - openrouter.synth_free       # Kimi K2 free, 1M ctx — no chunking needed
    - cerebras.synth_fast         # gpt-oss-120b, 30K tok/s, 64K ctx
    - groq.synth_oss              # gpt-oss-120b on Groq
    - local_vllm.driver           # degrade path

  frontier:    # the sparing Opus rung, triple-gated by the conductor (unchanged gates)
    - anthropic.frontier
```

Router contract (`lib/inference/router.py`):
- `run_role(role, messages, **opts) -> {ok, text, provider, model, usage, cost_usd, proceed_local}`
- Walks the chain; for each rung: skip if provider absent, skip if the cost-tier ceiling
  (`INFERENCE_MODE`: local/free/full/frontier — same semantics as today) excludes it, skip if the
  rate-bucket tracker says no headroom (never absorb a 429 — pre-check then fall through).
- On provider error/429/5xx/timeout/empty → one-line trace, fall to next rung.
- All rungs exhausted → `{ok:False, proceed_local:True}`. Never raises into the harness.
- Records every successful call to the ledger (Stage 4).

**Stage-2 DoD:** roles.yaml drives a working router; absent rungs skip; mode ceiling respected;
all-fail returns proceed_local without raising. Validate: with only GROQ_API_KEY set,
research_fanout resolves to Groq and research_synth falls through to whatever's present (or local).
Committed.

=================================================================================================
## STAGE 3 — Deep research as a parallel multi-bucket fan-out
=================================================================================================

Rebuild mcp-research's engine around the principle confirmed by the research: retrieval is cheap,
per-sub-question model reasoning is the wall-time. So minimize big-model calls, parallelize the
rote work across independent rate buckets, and synthesize once.

Pipeline (each step names a ROLE, not a provider):
1. **research_plan (1 call):** decompose into N sub-questions + a DONE/saturation criterion.
2. **Retrieval fan-out (zero-LLM, parallel):** SearXNG + arxiv + openalex + hn + stackexchange +
   github, fired concurrently. Markdown capture chain trafilatura→Crawl4AI→Jina-Reader.
3. **research_fanout (parallel, multi-bucket):** for each sub-question/snippet batch, run
   query-expansion + relevance-filter + dedup + field-extraction as MANY small fast calls,
   load-balanced across Groq buckets (8B 14,400/day is the workhorse; Scout 1,000/day; OpenRouter
   extract_free 1,000/day) by the bucket tracker. This is where wall-time is won: dozens of small
   calls in parallel across independent buckets instead of one slow sequential local loop.
4. **Corroboration (zero-LLM):** Qwen3-Embed/Reranker-0.6B local + ≥2-source gate + KG triangulation.
5. **research_synth (1 call):** single synthesis over the corroborated, fixed-size evidence set —
   Kimi K2 free (1M ctx, no chunking) first, Cerebras/Groq gpt-oss-120b fallback.
6. **Compound:** write verified claims + citations into RAG (sqlite-vec) + KG; future queries hit
   corpus_hit_check first (already built).

Parallelism + wall-time: the fan-out step issues calls concurrently up to each bucket's RPM, with
the bucket tracker (Stage 4) preventing 429s. Expected: minutes → tens of seconds, dominated by the
2 big-model calls (plan + synth) plus parallel retrieval, not a sequential per-sub-question loop.

Honesty the spec must encode in skill text + RESEARCH.md:
- **Local-only research = slow, sequential, deep.** One stream, no parallel buckets; fine for
  unattended overnight runs, painful interactively.
- **Multi-model API fan-out = faster but still bounded by RPM and by the 2 synthesis-class calls;**
  not instant. The win is parallelism across free buckets, not raw speed.
- **Cerebras** is fast (30K tok/s) but 5 RPM + 64K ctx → best as a single chunked-synthesis rung,
  not for fan-out.
- **Groq** free is per-MODEL buckets on one key → the fan-out workhorse (8B at 14,400/day).
- **OpenRouter :free** (post-$10) is 20 RPM / 1,000 RPD per model, roster rotates → great for Kimi
  synthesis + R1 planning, not for high-RPM fan-out.
- **DeepSeek/DeepInfra/Kimi paid** → when free buckets are exhausted or quality matters; cheap but
  not free.

**Stage-3 DoD:** mcp-research uses the role API for plan/fanout/synth; fan-out runs concurrently
across buckets; one synthesis call; compounds to RAG/KG; RESEARCH.md documents the honest
tradeoffs. Validate: a research run shows parallel fanout calls hitting ≥2 distinct Groq buckets
and a single synth call, with wall-time materially below the old sequential design. Committed.

=================================================================================================
## STAGE 4 — Central cost ledger + rate-bucket tracker (read the headers)
=================================================================================================

One place records every inference call: tokens, provider, model, role, and USD in `$0.000000`.

`lib/inference/ledger.py`:
- Every `run_role` success appends a row: `{ts, role, provider, model, in_tok, out_tok,
  cached_tok, cost_usd, wall_ms, rate_headers}`.
- Cost computed from the provider's `cost`/`cost_flash` block in inference.yaml; cached input
  priced at `cache_hit_in_per_mtok` when the provider reports cached tokens. Free providers record
  `cost_usd: 0.000000` but STILL record token counts (so you see volume even at $0).
- Persist to `~/.hermes-max/ledger.jsonl` + a rolled-up SQLite (`ledger.db`) for queries.
- USD always formatted `$0.000000` (six decimals — research fan-out costs live in the 4th-6th).

`lib/inference/buckets.py` (the 429-avoidance brain):
- Parses provider rate headers into a unified shape:
  - Groq: `x-ratelimit-remaining-requests` (RPD), `x-ratelimit-remaining-tokens` (TPM),
    `x-ratelimit-reset-*`, `retry-after`.
  - OpenRouter: its limit headers + the 20 RPM / 1,000 RPD account rule.
  - Cerebras: per-model rpm/rph/rpd/tpm/tpd from config + any returned headers.
  - DeepSeek/DeepInfra/Anthropic: token usage from the response body; rpm from config.
- Maintains a live per-(provider,model) bucket: remaining RPM/TPM/RPD with reset timestamps.
- `has_headroom(provider, model, est_tokens) -> bool` is what the router pre-checks so it skips a
  rung BEFORE sending (never absorb a 429). On an actual 429, honor `retry-after` and mark the
  bucket exhausted until reset.

`hm cost` (extend the existing verb):
- Today/this-week/this-month totals in `$0.000000`, broken down by provider, model, and role.
- A "free vs paid" split (tokens served at $0 vs tokens that cost money) so you can SEE how much the
  free constellation is saving you.
- Bucket status: remaining RPD per free model, so you know how much fan-out budget is left today.

**Stage-4 DoD:** ledger records every call with six-decimal USD and token counts (free calls show
$0.000000 + real tokens); bucket tracker parses Groq/OpenRouter/Cerebras headers and powers the
router's pre-check; `hm cost` shows provider/model/role breakdown + free-vs-paid split + remaining
free RPD. Validate: run a research fan-out, confirm ledger rows for each bucket, confirm `hm cost`
shows the split and the remaining daily free budget. Committed.

=================================================================================================
## STAGE 5 — Wire the harness to roles, prove the separation, clean up
=================================================================================================

- Migrate mcp-escalation (conductor), mcp-research, mcp-search to call `lib/inference` by ROLE
  only. The conductor's tiers (synth/steer/escalate/draft_pool) become role-chain entries in
  roles.yaml; the Opus triple-gate stays in the conductor logic, the *routing* moves to the fabric.
- Delete the now-dead direct-provider code paths inventoried in MIGRATION.md. The repo should SHRINK.
- Add `ARCHITECTURE.md` (user-facing, demystifying — see Stage 6) and confirm the seam: grep proves
  no MCP server imports a provider SDK or base URL directly; only `lib/inference` does.
- `hm preflight` gains an inference-fabric check: which providers are present, which roles are
  satisfiable, and a warning for any role whose entire chain is absent (so the user knows a
  capability is off).

**Stage-5 DoD:** all model-calling MCPs go through the role API; dead direct-call code deleted;
grep confirms the single seam; hm preflight reports provider/role coverage; hm smoke green.
Committed.

=================================================================================================
## STAGE 6 — ARCHITECTURE.md (demystify; agnostic + power-user honest)
=================================================================================================

Write `ARCHITECTURE.md` for a new user, plain and honest. It must contain:
- **The one-paragraph mental model:** MCP servers request roles; the inference fabric (config, not
  code) picks a provider; missing keys silently drop rungs; zero keys = fully local and free.
- **The config trinity:** `inference.yaml` (what backends exist), `roles.yaml` (which backend serves
  which job), `INFERENCE_MODE` (the spend ceiling: local/free/full/frontier).
- **Backend honesty table** — for each of local-vLLM / DeepSeek-direct / DeepInfra / OpenRouter /
  Groq / Cerebras / Anthropic: what it's good for, cost, context, throughput, rate limits, free-vs-
  paid, and the honest caveat (local = slow sequential deep research; Cerebras = fast but 5 RPM;
  Groq = per-model buckets, great for fanout; OpenRouter :free = rotating roster, 20 RPM, needs $10
  for 1,000/day; DeepSeek/Kimi paid = cheap not free; Anthropic = spare frontier only).
- **Three worked configs:** (a) zero-key pure-local; (b) lean — one cheap paid key (DeepSeek) +
  free Groq/OpenRouter; (c) the maximalist constellation (all keys, full fan-out). Show the
  roles.yaml diff for each.
- **The deep-research truth:** local = slow/sequential/deep; API fan-out = parallel/faster/bounded;
  what each provider contributes; where the wall-time actually goes (the 2 synthesis-class calls).
- **Cost transparency:** how `hm cost` reads headers and reports `$0.000000` with a free-vs-paid
  split.
- **The maintainability promise:** providers are an open bazaar that rotates; that's why they live
  in YAML; that's why the inference layer is one isolated lib; that's how the repo stays out of the
  Frankenstein swamp.

**Stage-6 DoD:** ARCHITECTURE.md exists, covers the mental model + config trinity + honest backend
table + three worked configs + deep-research truth + cost transparency + maintainability promise.
Committed.

=================================================================================================
## DEFINITION OF DONE
=================================================================================================
- One isolated `lib/inference/` (config, router, buckets, ledger, adapters) is the SOLE provider
  seam; no MCP server imports a provider SDK or URL directly (grep-proven).
- `inference.yaml` + `roles.yaml` let a user run zero-key-local → one-key-lean → full-constellation
  by editing config, never code; missing keys drop rungs silently.
- Deep research is a parallel multi-bucket fan-out (Groq workhorse + OpenRouter/Cerebras synth),
  one plan call + one synth call, compounding to RAG/KG, with honest documented tradeoffs.
- A central ledger records every call's tokens + `$0.000000` USD (free calls show real tokens at
  $0), powered by a header-parsing bucket tracker that prevents 429s; `hm cost` shows the
  provider/model/role breakdown, free-vs-paid split, and remaining daily free budget.
- ARCHITECTURE.md demystifies the whole thing for any user while supporting the maximalist config.
- The repo is SMALLER and cleaner than before: dead direct-call paths deleted, one seam, three
  config files, honest docs. No new framework. Anti-Frankenstein upheld.

## Explicitly rejected (anti-Frankenstein)
- No per-provider logic leaking into MCP servers (the whole point is one seam).
- No multi-key-per-provider rotation to dodge ToS (one key per provider; respect per-model buckets).
- No new orchestration framework; the router is ~one file, the ledger ~one file.
- No hardcoded model ids anywhere outside inference.yaml (the bazaar rotates; config absorbs it).

## Notes captured from current results (fold into ARCHITECTURE.md backend table)
- DeepSeek V4-Pro permanent: $0.435 in / $0.87 out / $0.003625 cache-hit, 1M ctx.
- DeepSeek V4-Flash: $0.14 in / $0.28 out / $0.0028 cache-hit, 1M ctx.
- DeepInfra V4-Pro list higher ($1.30/$2.60-class) — verify live; direct DeepSeek usually cheaper.
- OpenRouter :free post-$10 deposit = 1,000 req/day per model, 20 RPM, roster rotates. Confirmed
  from OpenRouter FAQ. Free standouts: moonshotai/kimi-k2:free (1M ctx), deepseek/deepseek-r1-0528
  :free, qwen/qwen3-coder:free (262K), openai/gpt-oss-20b:free.
- Groq free = per-MODEL buckets on one key: llama-3.1-8b-instant 14,400 rpd / 560 tok/s (fanout
  workhorse), llama-4-scout 1,000 rpd / 750 tok/s, gpt-oss-120b 1,000 rpd / 500 tok/s.
- Cerebras free = gpt-oss-120b / zai-glm-4.7, 5 rpm / 2,400 rpd / 30K tpm / 64K ctx, ~30K tok/s →
  single chunked-synthesis rung.
- Anthropic Opus 4.8 = $5/$25 (cache $0.50); spare frontier only, triple-gated.
- Local Qwen3.6-35B-A3B on Thor ~50 tok/s single-stream (MTP); free, private, slow-but-steady;
  the always-present degrade floor for every role.

=================================================================================================
## STAGE 7 — Default inference.yaml (your constellation, shipped as the default)
=================================================================================================

The checked-in `inference.example.yaml` doubles as the recommended default. It should reflect
the maximalist power-user constellation so that any user who copies it and fills in their keys
gets the optimal topology immediately, with no research required. The YAML already written in
Stage 1 IS this default — but the install/setup path must wire it correctly:

- `scripts/setup.sh` (or `hm up --setup`) checks whether `~/.hermes-max/inference.yaml` exists.
  If not, it copies `inference.example.yaml` to that path and prints a one-line prompt per
  provider: "Set DEEPSEEK_API_KEY in .env to enable DeepSeek planning (V4-Pro, $0.435/M)."
- The example ships with your exact constellation pre-configured in roles.yaml:
  - plan → openrouter.plan_free (DeepSeek R1:free) → deepseek_direct.planner → local_vllm.driver
  - drive → local_vllm.driver → deepseek_direct.driver
  - research_fanout → groq.fast_filter → groq.fast_mid → openrouter.extract_free
  - research_synth → openrouter.synth_free (Kimi K2:free) → cerebras.synth_fast → local_vllm.driver
  - frontier → anthropic.frontier (triple-gated)
- Every provider block has a comment: "# Remove this block or unset the env var to disable."
  No key → silent skip. User does not need to edit anything to use a subset.
- INFERENCE_MODE defaults to `full` in .env.example (free + paid, no frontier) — safe default.
  User can drop to `free` to use only $0 providers, or raise to `frontier` to unlock Opus.

**Stage-7 DoD:** inference.example.yaml is the fully-populated constellation; setup.sh copies it
on first run with per-provider prompts; roles.yaml default reflects the optimal topology below.
Committed.

=================================================================================================
## STAGE 8 — Conductor/executor topology for maximum coding performance
=================================================================================================

This is the missing piece: given the infrastructure now exists, wire the conductor's planning and
execution roles to maximize coding quality approaching Claude Code + Opus 4.8. Grounded in the
research findings: (1) V4-Pro at $0.435/$0.87 is ~11.5x cheaper than Opus on input and ~29x
cheaper on output, within a few points of Opus on planned tasks; (2) the plan/execute split is
already live — V4-Pro writing an incontrovertible plan is the single highest-ROI quality lever;
(3) first-call quality gaps can be closed iteratively by the verify gate + LSP repair loop +
best-of-N, without paying Opus prices on every token.

### The optimal role-to-model mapping for coding (extend roles.yaml)

Add these coding-specific roles to roles.yaml:

```yaml
roles:
  # --- coding performance topology ---

  code_plan:         # the incontrovertible PLAN.md (expensive, once per task, dominates quality)
    - deepseek_direct.planner     # V4-Pro: flagship coding quality, permanent $0.435/$0.87
    - openrouter.plan_free        # DeepSeek R1:free fallback (strong reasoning, $0)
    - deepinfra.planner           # DeepInfra V4-Pro if direct is down
    - local_vllm.driver           # last resort: local plans (weaker but free)
    # Note: a well-written V4-Pro plan closes ~80% of the Opus quality gap on implementation tasks

  code_execute:      # implements the plan literally (high-volume, cheap, most tokens spent here)
    - local_vllm.driver           # default: Qwen3.6-35B-A3B ~50 tok/s, free, private
    - deepseek_direct.driver      # V4-Flash: substitute for faster cloud execution
    # Note: with an incontrovertible plan, 35B executes reliably; this is transcription not design

  code_steer:        # mid-execution nudge when executor is stuck or verify fails once
    - groq.fast_mid               # Llama-4-Scout: fast, free, good enough for a directional nudge
    - openrouter.extract_free     # gpt-oss-20b free fallback
    - deepseek_direct.driver      # V4-Flash: cheap paid fallback

  code_repair:       # targeted repair after verify-fail (LSP diagnostic + error context, 1 call)
    - groq.fast_mid               # quick, cheap, 30K TPM — fast repair loop
    - deepseek_direct.driver      # V4-Flash if quality matters more than speed

  code_frontier:     # the Opus rung — triple-gated only (synth-failed-twice + large blast-radius)
    - anthropic.frontier          # Opus 4.8: $5/$25; one call is ~$0.08-1.25 depending on tokens
    # Gates in conductor_policy.py (unchanged): mode=frontier AND synth-failed-twice AND
    # opinions-disagree AND blast-radius-large. Most tasks NEVER reach this rung.

  code_draft_pool:   # best-of-N fan-out pool (verifier picks, not voting)
    - groq.fast_mid               # Llama-4-Scout: fast drafts
    - openrouter.code_free        # Qwen3-Coder free: strong coding
    - openrouter.extract_free     # gpt-oss-20b: lighter tasks
    - deepseek_direct.driver      # V4-Flash paid anchor for quality
```

### The execution loop that closes the gap iteratively

Encode this in `workflow-execute-from-plan.md` skill (extend, do not replace) and in
`conductor_policy.py`:

```
For each file in PLAN.md order:
  1. Execute from plan (code_execute role)
  2. quick_check immediately after each edit (lint+typecheck, ~1s)
     → if fails: lsp_diagnostics → targeted repair call (code_repair role, 1 call)
     → if still fails after 2 repairs: request_plan_revision → V4-Pro fills the gap
  3. verify(file) after all edits pass quick_check
     → if fails: run property_test → get the minimal counterexample
     → pass counterexample to code_repair (targeted, not full regen)
     → if still fails after 2 repair attempts: escalate to code_steer (V4-Flash/Scout nudge)
     → if still fails after steer: route to code_plan for a partial re-plan of the failing file
  4. quality_check advisory (docstrings, annotations, TODOs)
  5. checkpoint on green
  6. DONE only when every DONE_CONDITION clause is literally met

Rung spend order (cheapest first, most expensive last):
  LSP repair (~$0) → code_repair/Groq-Scout (~$0) → code_steer/V4-Flash (~$0.001)
  → code_plan re-plan (~$0.01) → code_frontier/Opus (~$0.08-1.25, triple-gated only)
```

### What closes the gap to Opus and what does not

Encode this in ARCHITECTURE.md (the honest section):

CLOSES THE GAP (implement these, ordered by ROI):
1. **V4-Pro planning with gap-free PLAN.md** — the dominant lever. A plan with exact signatures +
   prose algorithms + edge cases + concrete DONE_CONDITION means the executor never designs,
   only transcribes. ~80% of the Opus-vs-35B quality gap on planned tasks is a planning gap.
2. **Test-first contracts** — ship failing tests with every FILE SPEC in the plan. "Done" is
   mechanically defined; drift is impossible. Spec-Kit TDD ordering evidence.
3. **LSP-diagnostic repair loop** — on verify-fail, feed the exact compiler diagnostic + symbol
   context back to the executor for a targeted fix before re-gen. ~50ms symbol intelligence;
   dramatically cheaper than a full regen.
4. **Verifier-guided best-of-N** — sample N drafts, select by verify/property results (not voting).
   Best-of-N with a real verifier lifts first-call pass rates. Already in mcp-search; tune N and
   parallelize drafts on free-tier models in code_draft_pool.
5. **Iterative web-search-on-error** — on a weak solution, search the exact error/stack signature,
   retry. Cheaper than escalation; works on most "unknown library API" failures.
6. **Property + metamorphic testing in verify** — already built; gate with ENABLE_PROPERTY_TEST=true
   on core logic files.

DOES NOT CLOSE THE GAP (be honest with the user):
- Swapping the executor to a bigger local model. With an incontrovertible plan, 35B executes
  reliably; bigger model helps debugging and test-failure diagnosis but not clean-path execution.
- More research fan-out. Wide research helps with novel unknown APIs; it does not help with
  "implement what the plan specifies" tasks.
- Adding more providers to the constellation. Provider count is not quality; plan quality is quality.

GENUINE CEILING (honest about Opus):
- On genuinely novel hard problems — a tricky concurrency bug, a subtle architectural decision with
  no known pattern — Opus's raw reasoning advantage is real and not fully closed by harness
  engineering. The triple-gated Opus rung exists precisely for this case. Budget one Opus call
  per genuinely hard task when the verify gate refuses to go green; at $0.08-1.25/call it's still
  cheap relative to time spent.

### Conductor policy update (extend conductor_policy.py, not rewrite)

Add a `code_quality_signals` dict to the per-subtask policy, tracking:
- `plan_rounds`: how many plan revisions were needed (>1 = thin brief, flag it)
- `repair_rounds`: how many repair calls were needed per file (>2 = executor can't do this alone)
- `steer_calls`: how many steer nudges (>3/task = conductor is too cheap, escalate the ceiling)

These feed back into `hm cost` and into the GEPA self-improvement loop (which already captures
trajectories): tasks that needed many repair rounds get flagged in the review queue, suggesting
the planner brief needs to be more specific for this class of task.

**Stage-8 DoD:** code_plan/execute/steer/repair/frontier/draft_pool roles in roles.yaml; the
iterative repair loop documented in workflow-execute-from-plan.md; quality-signal tracking in
conductor_policy.py; ARCHITECTURE.md honest section on what closes and does not close the gap;
hm smoke green. Committed.

=================================================================================================
## COMPLETE DEFINITION OF DONE (all 8 stages)
=================================================================================================

The repo has exactly one seam between the harness and the model backends (lib/inference/).
The backend landscape is entirely in YAML (inference.yaml + roles.yaml); no code changes needed
to add/remove a provider or swap a free model when the bazaar rotates.
Your constellation (DeepSeek-direct, DeepInfra, OpenRouter with R1+Kimi free, Groq fan-out buckets,
Cerebras synth, Anthropic spare) ships as the default inference.example.yaml / roles.yaml.
The deep research engine runs a parallel multi-bucket fan-out with zero-LLM retrieval, one plan
call, one synth call, compounding to RAG/KG, with documented honest tradeoffs.
The coding performance topology places V4-Pro planning as the dominant quality lever, 35B/V4-Flash
execution as the cheap-token workhorse, and Opus as the triple-gated spare.
A central ledger tracks every call's tokens + $0.000000 USD with free-vs-paid split and remaining
free RPD per model.
ARCHITECTURE.md demystifies the whole system including the honest ceiling on Opus-parity.
The repo is SMALLER and cleaner than before spec execution: dead direct-call code deleted, one
seam, documented, grep-proven.

=================================================================================================
## STAGE 9 — Arg-based MODES: the ergonomic toggle (the user-facing heart of the system)
=================================================================================================

The whole value proposition is reachable only if switching the cost/quality posture is trivial.
A mode is one word that reassigns the coding role chains. The user should never edit roles.yaml to
change posture — they pass `--mode` (or `hm up --free` / `--full-local` / `--full` / etc.) and the
fabric swaps chains. roles.yaml holds the chains; modes.yaml holds the named presets; the active
mode is persisted to `~/.hermes-max/mode` and echoed in `hm status`, `hm cost`, and the cockpit.

### The modes, ordered by appeal (default first), with honest GPU/cost/quality framing

```yaml
# modes.yaml — one word reassigns the coding role chains. Default = full-local.
# Each mode declares: requires_gpu, monthly_cost_estimate, one-line posture.

modes:

  free:                           # DEFAULT — Kimi K2 free plans, local executes
    requires_gpu: true            # needs a local executor (Thor/Spark) to be compelling
    monthly_cost: "$0.00"
    posture: "Kimi K2 (free, OpenRouter, 1M ctx) plans, local model executes. Zero cost.
              THE DEFAULT. Most compelling with a Thor/Spark — sovereign + free + near-frontier
              planning via Kimi K2 which replenishes daily. Falls back to R1:free if Kimi
              unavailable, then local plans."
    chains:
      code_plan:    [openrouter.synth_free, openrouter.plan_free, local_vllm.driver]
      # Kimi K2:free (1M ctx) → DeepSeek R1:free → local 35B last resort
      code_execute: [local_vllm.driver]
      code_steer:   [groq.fast_mid, openrouter.extract_free, local_vllm.driver]
      code_repair:  [groq.fast_mid, local_vllm.driver]
      research_fanout: [groq.fast_filter, groq.fast_mid, openrouter.extract_free]
      research_synth:  [openrouter.synth_free, cerebras.synth_fast, local_vllm.driver]
      code_frontier: []           # no frontier in free mode

  full-local:                     # 2nd — V4-Pro plans, local executes (~$1.50/mo)
    requires_gpu: true
    monthly_cost: "~$1.50"
    posture: "DeepSeek V4-Pro plans (~$0.05/day), local model executes (free). Near-frontier
              planning quality at minimal cost. Needs a GPU. Step up from free when you want
              V4-Pro's stronger architectural judgment over Kimi-free."
    chains:
      code_plan:    [deepseek_direct.planner, openrouter.synth_free, local_vllm.driver]
      # V4-Pro direct → Kimi K2:free fallback → local last resort
      code_execute: [local_vllm.driver]
      code_steer:   [groq.fast_mid, deepseek_direct.driver]
      code_repair:  [groq.fast_mid, deepseek_direct.driver]
      research_fanout: [groq.fast_filter, groq.fast_mid, openrouter.extract_free]
      research_synth:  [openrouter.synth_free, cerebras.synth_fast, local_vllm.driver]
      code_frontier: []

  full:                           # 3rd — V4-Pro plans, V4-Flash executes (~$17/mo, no GPU)
    requires_gpu: false
    monthly_cost: "~$17"
    posture: "DeepSeek V4-Pro plans, V4-Flash executes — both API, NO GPU required. ~10% of
              Claude Code Max, no rate limits. The no-hardware path: anyone can run this."
    chains:
      code_plan:    [deepseek_direct.planner, openrouter.synth_free]
      # V4-Pro direct → Kimi K2:free fallback
      code_execute: [deepseek_direct.driver, local_vllm.driver]
      # V4-Flash primary → local fallback if vLLM is up
      code_steer:   [groq.fast_mid, deepseek_direct.driver]
      code_repair:  [groq.fast_mid, deepseek_direct.driver]
      research_fanout: [groq.fast_filter, groq.fast_mid, openrouter.extract_free]
      research_synth:  [openrouter.synth_free, cerebras.synth_fast]
      code_frontier: []

  frontier-local:                 # 4th — Opus 4.8 plans, local executes (~$45/mo)
    requires_gpu: true
    monthly_cost: "~$45"
    posture: "Opus 4.8 plans (frontier reasoning), local model executes. Sovereign execution +
              true frontier planning. Economics tighten vs Claude Code subscription but justified
              for unlimited 14hr/day use, no rate limits, and private local execution."
    chains:
      code_plan:    [anthropic.frontier, deepseek_direct.planner, openrouter.synth_free]
      # Opus → V4-Pro fallback → Kimi-free last fallback
      code_execute: [local_vllm.driver]
      code_steer:   [groq.fast_mid, local_vllm.driver]
      code_repair:  [groq.fast_mid, local_vllm.driver]
      research_fanout: [groq.fast_filter, groq.fast_mid, openrouter.extract_free]
      research_synth:  [openrouter.synth_free, cerebras.synth_fast, local_vllm.driver]
      code_frontier: [anthropic.frontier]

  frontier:                       # 5th — Opus 4.8 plans, V4-Flash executes (~$60/mo, no GPU)
    requires_gpu: false
    monthly_cost: "~$60"
    posture: "Opus 4.8 plans, V4-Flash executes — both API, no GPU. Closest to Claude Code+Opus.
              Cheaper than Max 20x with no rate limits, but appeal narrows vs the subscription.
              Use for a genuinely hard session; not a daily driver."
    chains:
      code_plan:    [anthropic.frontier, deepseek_direct.planner]
      # Opus → V4-Pro fallback
      code_execute: [deepseek_direct.driver, local_vllm.driver]
      code_steer:   [groq.fast_mid, deepseek_direct.driver]
      code_repair:  [groq.fast_mid, deepseek_direct.driver]
      research_fanout: [groq.fast_filter, groq.fast_mid, openrouter.extract_free]
      research_synth:  [openrouter.synth_free, cerebras.synth_fast]
      code_frontier: [anthropic.frontier]

  local:                          # 6th — pure local, no API, air-gapped floor ($0)
    requires_gpu: true
    monthly_cost: "$0.00"
    posture: "Pure local, no API at all. Fully sovereign, limited to local model quality.
              The air-gapped / zero-dependency floor."
    chains:
      code_plan:    [local_vllm.driver]
      code_execute: [local_vllm.driver]
      code_steer:   [local_vllm.driver]
      code_repair:  [local_vllm.driver]
      research_fanout: [local_vllm.driver]
      research_synth:  [local_vllm.driver]
      code_frontier: []
```

### The toggle ergonomics (this is the part that must feel effortless)

- **`hm mode`** (no arg) → prints the current mode, its posture line, monthly_cost, requires_gpu,
  and which providers are present/absent for that mode's chains. One screen, instantly understood.
- **`hm mode <name>`** → switches mode live (re-resolves role chains, persists to ~/.hermes-max/mode,
  no restart needed for the next task). Validates the mode is satisfiable (warns if requires_gpu but
  no local_vllm reachable, or if a chain's providers are all absent) but still switches.
- **`hm up --<mode>`** → start in a mode (--free / --full-local / --full / --frontier-local /
  --frontier / --local). --full-local is the default if no flag given.
- **`hm mode --list`** → a clean table: mode | cost/mo | needs GPU | one-line posture, ordered by
  appeal (free, full-local, full, frontier-local, frontier, local). The user sees the whole
  landscape in one glance and picks.
- The cockpit (`hm dev`) shows the active mode + live spend-so-far-today in the status pane, so the
  cost consequence of the current mode is always visible.

### Mode selection guidance baked into `hm mode --list` output and ARCHITECTURE.md

```
MODE            COST/MO   GPU?  POSTURE
free            $0.00     yes   Kimi-K2-free plans, local executes. DEFAULT. Best with Thor/Spark.
full-local      ~$1.50    yes   V4-Pro plans, local executes. V4-Pro quality bump over Kimi-free.
full            ~$17      no    V4-Pro plans, V4-Flash executes. No GPU needed. ~10% of Code Max.
frontier-local  ~$45      yes   Opus plans, local executes. Sovereign + true frontier planning.
frontier        ~$60      no    Opus plans, V4-Flash executes. Closest to Claude Code. Hard sessions.
local           $0.00     yes   Pure local, no API. Air-gapped floor.
```

The honest framing the user must see (in ARCHITECTURE.md):
- **free and full-local are the headline value** — near-frontier planning (Kimi-free or V4-Pro) +
  free/sovereign local execution. $0–1.50/month. The whole point of owning the GPU.
- **full** is the no-GPU on-ramp — anyone can run it, ~$17/month, ~10% of Claude Code Max.
- **frontier / frontier-local** are real but the value proposition narrows vs a Claude Code
  subscription; offered for unlimited-usage / no-rate-limit / private-execution reasons, not pure
  cost. Use frontier for a genuinely hard session, then drop back to full-local.

### Default and persistence

- Default mode = **free** (Kimi K2-free planner + local executor). Set in .env.example as
  `INFERENCE_MODE=free`.
- If free is selected but no local_vllm endpoint is reachable (no GPU), `hm up` prints a clear
  warning: "free mode requires a local vLLM endpoint. Set vLLM up or switch to --full (no GPU
  needed)." It does NOT silently fall back to full — the user must explicitly choose to pay.
- If free is selected but OPENROUTER_API_KEY is absent, the code_plan chain falls through to
  local_vllm.driver automatically — pure-local planning, no error, no cost.
- If full-local is selected but DEEPSEEK_API_KEY is absent, the code_plan chain falls through to
  Kimi-K2-free automatically — user gets free-planner quality without hitting an error.

**Stage-9 DoD:** modes.yaml with all six modes in appeal order (free DEFAULT, full-local,
full, frontier-local, frontier, local) each declaring requires_gpu + monthly_cost + posture +
chains; `hm mode` / `hm mode <name>` / `hm mode --list` / `hm up --<mode>` all working with live
switching and clear output; default is free with explicit GPU-absent warning (not silent fallback)
and graceful OPENROUTER_API_KEY-absent → local planning; cockpit shows active mode + today's spend;
ARCHITECTURE.md mode-selection section with the honest cost/GPU/appeal framing. Committed.

### Note on the default-mode evaluation the operator wants to run

The operator intends to empirically compare all modes in appeal order:
  1. free          (Kimi-K2-free planner + local executor)   — THE DEFAULT; is Kimi-free enough?
  2. full-local    (V4-Pro planner + local executor)         — pays ~$1.50/mo for V4-Pro judgment
  3. full          (V4-Pro planner + V4-Flash executor)      — no-GPU path at ~$17/mo
  4. frontier-local (Opus 4.8 planner + local executor)     — ~$45/mo, economics tighten
  5. frontier      (Opus 4.8 planner + V4-Flash executor)   — ~$60/mo, narrowing vs Claude Code
The plan/execute proof task (Bloom filter → Groth16) should be run under `free` then `full-local`
back-to-back, identical local executor, to measure the planning-quality delta between Kimi-K2-free
and V4-Pro. This isolates the question: is Kimi-free good enough to be the permanent default, or
does V4-Pro's stronger architectural judgment on hard tasks justify the ~$1.50/month upgrade?
Record both runs' trajectories so the GEPA loop and `hm cost` capture the evidence.