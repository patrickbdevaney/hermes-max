# hermes-max: The Intelligence Harness — Economic Options and Value Proposition
## A Dispassionate Comparison Against Commercial Coding-Agent Plans (May 2026)

---

## The thesis, stated plainly

hermes-max is a model-agnostic intelligence harness. Its value is not "local LLMs are better than
subscriptions." Its value is the compounding, verification, and escalation architecture it layers on
top of *any* model endpoint — local or API, cheap or frontier. The driver is a configuration choice.
The harness is the constant.

This reframing resolves the apparent conflict: a developer running DeepSeek V4-Flash as a pure API
driver with no local hardware gets most of the harness's structural advantages at near-zero cost. A
developer running a local Jetson or Mac Studio gets those same advantages plus sovereignty and
guaranteed throughput. Both configurations beat the structural limitations every managed subscription
shares: no cross-session memory, rolling-window caps, provider-side rate limits, and ToS-gated access.

Claude Code and the other subscriptions are genuinely good products. They are the right answer for
many developers. The harness does not compete with their per-turn smoothness; it competes with the
absence of compounding, unlimited volume, and sovereignty that all subscriptions share by design.

---

## The three economic configurations

### Configuration A — API-driven harness (the most accessible entry point)

**Setup:** A laptop or any machine running Python. DeepSeek V4-Flash as the primary driver via API.
DeepSeek V4-Pro as the synthesis tier. Groq/Cerebras free tiers for drafting. Optional sparing
Opus 4.8 escalation. The full hermes-max harness running against these endpoints.

**Cost:** ~$5-15/month all-in for heavy use, depending on synthesis frequency and whether Opus is
enabled. No capex. No hardware to manage.

**How the driver economics work:** V4-Flash at $0.14/M input, $0.28/M output — but with a stable
system prompt and repo context, cache-hit rates on input approach 90%+, making the effective input
cost ~$0.0028/M. A heavily cached agentic workload generating 3M driver tokens/day costs roughly
$5-10/month in driver tokens at realistic cache ratios. This is the "Claude Code near-Opus
performance for almost free" case: a 1M-context frontier model as the always-on driver at rounding-
error cost, wrapped in a harness that adds compounding memory and verified synthesis that no
subscription provides.

**What you get that subscriptions don't:**
- Cross-session compounding KG/RAG: the system accumulates architectural decisions and domain
  knowledge permanently across every project, every session
- Verify-gate reliability: the agent cannot declare done on a red test suite regardless of driver
  confidence
- Deep-research cascade: multi-source cited synthesis that compounds into the knowledge corpus
- No rolling-window fragmentation: long-horizon tasks run uninterrupted regardless of wall time
- Model portability: when V4-Flash is superseded by a cheaper/better model next month, swap one
  env var — the harness is unchanged
- No ToS exposure: no provider can revoke access for using the "wrong tool" or working at the
  "wrong company"

**The honest ceiling:** V4-Flash as a driver is not Opus. On genuinely frontier-novel problems
requiring novel architectural synthesis, the gap is real. The synthesis tier (V4-Pro) and the
sparing frontier escalation (Opus 4.8, ~$0.18/call, three-gated) close most of that gap. The
verify gate closes the reliability gap regardless of model tier. For the implementation-heavy
majority of real engineering work, this configuration performs at ~90-95% of realized Claude Code +
Opus capability. For the tail of frontier-novel problems without the Opus tier, the gap is real.
With it, it closes.

---

### Configuration B — Local sovereign inference

**Setup:** A dedicated inference device (Jetson Orin, Jetson Thor, DGX Spark, RTX desktop, Mac
Studio) running a local open-weight model via vLLM or llama.cpp/MLX, serving an OpenAI-compatible
endpoint. The harness points at that endpoint. The API tail (synthesis, steering, sparing Opus) is
identical to Configuration A.

**Cost:** Hardware amortization + electricity + API tail. See the full table in §5.

**What this adds over Configuration A:**
- Zero marginal token cost on the driver: 90-120M driver tokens/month at sustained round-the-clock
  agentic use, with no API meter running
- Guaranteed throughput: no rate limits, no 503s during peak demand, no provider repricing
- True data sovereignty: the majority of inference (the driver) never leaves the machine; code and
  intermediate reasoning stay local
- Long context without API cost: a 262K or 1M context window that doesn't meter by the token for
  every turn
- Operational independence: if every API provider goes down simultaneously, the local driver
  continues; only the synthesis/steering/frontier tiers degrade gracefully

**The honest ceiling:** Configuration B makes economic sense when the hardware is already justified
by other uses (robotics, local ML, privacy requirements), or when the volume genuinely saturates
what API pricing would cost at scale. The capex is real; at a realistic 7-year hardware lifespan
for well-maintained embedded compute, the math works out as described in §5. It does not make sense
as a pure cost play against V4-Flash API at low-to-moderate volume — the API is cheaper there.

**Verified throughput by device:**

| Device | Memory | Model tier | Tok/sec | Tokens/day (sustained) |
|---|---|---|---|---|
| Jetson Orin Nano 8GB | 8GB | Qwen3.6-7B Q4 | ~15-25 | ~0.3-0.6M |
| Jetson AGX Orin 32GB | 32GB | Qwen3 30B-A3B Q4 | ~40-50 | ~0.7-1.1M |
| Jetson AGX Orin 64GB | 64GB | Qwen3 30B-A3B | ~61 (verified) | ~1.0-1.5M |
| Jetson AGX Thor 128GB | 128GB | Qwen3.6-35B-A3B (MTP) | ~40-50 + bursts | ~1.5-3M+ |
| DGX Spark | 128GB | 35B-A3B / Nemotron-Super 49B | ~60-80 | ~2-4M |
| RTX 5090 system | 32GB | 35B-A3B Q4 | ~80-100 | ~2-4M (bursty) |
| Mac Studio M4 Max 128GB | 64-128GB | 35B-A3B MLX | ~40-60 | ~1-2M |
| Mac Studio M4 Ultra 192GB | 192GB | Nemotron-Super 49B MLX | ~50-70 | ~1.5-3M |

Daily tokens assume ~30-50% inference wall-time fraction (the rest is tool calls, verification,
research I/O, and think-time between sessions). Monthly: ~30-120M tokens depending on device and
utilization.

---

### Configuration C — Commercial subscription (the right answer for many developers)

Claude Code and its peers are excellent products that set the benchmark for per-turn smoothness and
managed experience. They are the correct choice when:
- You use a coding agent a few hours per day, not 24/7 unattended
- You don't need cross-session compounding memory (you start each project fresh anyway)
- You want peak Opus-on-every-turn quality without infrastructure management
- The $20-200/mo flat fee fits your workflow and budget better than API billing

The harness adds the most value over a subscription for users who have outgrown its structural
limits — not its per-turn quality, but its session-boundedness, its rolling-window caps, its
provider-side ToS constraints, and its inability to compound knowledge across projects. For a
developer who uses Claude Code a few hours a day and doesn't run long-horizon unattended tasks,
the subscription is the more rational purchase.

---

## The commercial plans (verified May 2026)

### Anthropic — Claude Code

| Plan | $/mo | Usage | Notes |
|---|---|---|---|
| Pro | $20 | ~44K tok/5hr rolling window | Focused sessions; large file attachments eat quota fast |
| Max 5x | $100 | 5× Pro | For users hitting Pro limits 2-3×/week |
| Max 20x | $200 | ~220K tok/5hr window | Equivalent API value ~$600-1,500/mo; rate limits non-issue for most full-day single-agent work |

Field notes: v2.1.89 and v2.1.100+ (March-May 2026) introduced token-inflation bugs (broken prompt
caching), with some Max 20x plans exhausted within ~70 minutes of reset. Anthropic's Jan 9, 2026
ToS blocks Claude via third-party CLIs, binding usage to Anthropic's own client. API billing is
entirely separate — a Max subscription grants no API credits.

### OpenAI — Codex
Bundled with ChatGPT Plus/Pro/Business. OpenAI supports OpenCode and third-party CLIs. Users with
$10 account balance opting into data training reportedly access several million free tokens/day.
Effective ceiling is opaque. Codex desktop app available on Windows.

### Google — Antigravity 2.0 / Gemini
Antigravity 2.0 launched May 19, 2026. Tiers: $20/mo Pro, $99.99/mo entry Ultra, $200/mo top
Ultra. After a generous launch, Google cut quotas and moved to an opaque credit system with
documented multi-day lockouts for daily users. Google has blocked third-party CLI auth. Gemini CLI
at ~1,000 free requests/day remains genuinely useful for suitable tasks.

### Z.ai — GLM Coding Plan
Anthropic-compatible flat-rate. Tiers: Lite ~$10/mo, Pro ~$30/mo (GLM-5, 77.8% SWE-bench), Max
~$80/mo — all quarterly-billed. $3/mo promo ended Feb 11, 2026. Works in Claude Code, Cursor,
Cline, OpenCode. A legitimate managed alternative to the synthesis tier for users who want a plan
rather than API keys.

### Moonshot — Kimi Code
Built on Kimi K2/K2.5/K2.6 (1T params, 1M context). ~90-95% of Sonnet 4.6 on coding benchmarks;
stronger on long-context tasks above 200K tokens. Works through Kimi CLI, Claude Code, Roo Code.
A strong Anthropic-compatible driver-API alternative to V4-Flash for users who want the K2
model family.

### DeepSeek
API-only — no subscription plan. V4-Flash $0.14/$0.28 (cache hit $0.0028/M), V4-Pro $0.435/$0.87
promo-as-standing (cache hit $0.0036/M), both 1M context, MIT weights. Used in this system as the
synthesis and steering tiers. Not a subscription competitor — a provider.

---

## Hardware amortization (realistic lifespan model)

Jetson and DGX hardware is designed for industrial embedded deployment with LTS SDK support.
Well-maintained compute hardware commonly operates 7-10 years. 24-month amortization is appropriate
for corporate depreciation schedules, not for a developer-owned inference server.

**Monthly capital cost by device and lifespan:**

| Device | Capex | 3yr/mo | 5yr/mo | 7yr/mo | 9yr/mo |
|---|---|---|---|---|---|
| Jetson AGX Orin 64GB | ~$1,000 | $27.8 | $16.7 | $11.9 | $9.3 |
| Jetson AGX Thor 128GB | ~$3,600 | $100 | $60 | $42.9 | $33.3 |
| DGX Spark | ~$3,750 | $104 | $62.5 | $44.6 | $34.7 |
| RTX 5090 system | ~$4,500 | $125 | $75 | $53.6 | $41.7 |
| Mac Studio M4 Max 128GB | ~$4,000 | $111 | $66.7 | $47.6 | $37.0 |
| Mac Studio M4 Ultra 192GB | ~$5,000 | $138.9 | $83.3 | $59.5 | $46.3 |

**Total monthly cost of ownership (capital + electricity + API tail):**

| Device | 3yr | 5yr | 7yr | 9yr | Notes |
|---|---|---|---|---|---|
| Jetson AGX Orin 64GB | ~$42-47 | ~$31-36 | ~$26-31 | ~$23-28 | ~$7/mo electricity |
| Jetson AGX Thor 128GB | ~$115-122 | ~$75-82 | ~$57-64 | ~$47-54 | ~$10-12/mo electricity |
| DGX Spark | ~$120-127 | ~$78-85 | ~$60-67 | ~$50-57 | ~$20-25/mo electricity |
| RTX 5090 system | ~$185-195 | ~$135-145 | ~$113-123 | ~$101-111 | ~$45-55/mo electricity |
| Mac Studio M4 Max 128GB | ~$128-135 | ~$83-90 | ~$64-71 | ~$54-61 | ~$10-12/mo electricity |

The RTX numbers are materially inflated by power draw. For always-on agentic operation,
unified-memory low-power hardware (Jetson, Mac Studio) wins on operating cost despite equivalent
or lower tok/s. RTX configurations make more sense for bursty high-throughput use with
machine-off periods between sessions.

**At 7-year amortization:** A Jetson AGX Orin 64GB costs ~$26-31/mo all-in. A Jetson AGX Thor
costs ~$57-64/mo. Both comfortably undercut Claude Code Max 20x ($200/mo) while producing
90-120M+ tokens/month with no rolling windows.

---

## The steelman — what subscriptions structurally cannot offer at any price

**1. The subscription-market reliability problem.**
Every major coding-agent subscription in 2026 has experienced quota reductions, token-inflation
bugs, pricing tier restructuring, opaque credit systems, multi-day lockouts, or ToS changes that
revoked access based on which tool or company the developer used. These are documented events, not
hypothetical risks. A local inference server running MIT-licensed open weights has none of these
properties by construction. The weights are permanently owned. The endpoint does not go down because
a provider is having a bad quarter. The ToS cannot be changed retroactively to revoke your access.
The context window cannot be reduced to cut costs. These are structural guarantees that no managed
subscription can offer — not because providers are bad actors, but because their incentives and the
user's operational continuity incentives are not aligned.

**2. The compounding flywheel.**
Every commercial coding agent resets each session. By month 3 of a project, hermes-max's knowledge
graph contains the full architectural history, the RAG index surfaces the right code on the first
query, the research corpus has accumulated domain-specific papers and techniques, and the difficulty
classifier has improved on your specific traces. The system demonstrably gets better at your
codebase over time. This is not a feature available at any price from any subscription — session
reset is a structural property of how they work.

**3. Long-horizon task integrity.**
The 5-hour rolling window is not merely a cost constraint — it fragments long-horizon agentic tasks
at the context boundary. A complex multi-file refactor spanning 8 hours, with a large codebase
context, mid-task research cascades, and verification cycles, does not fit in any subscription's
window without state loss. The local or API-harness configuration has no window. PLAN.md persists.
The KG remembers what was decided three hours ago. The task runs until it is done, not until a
counter resets.

**4. Model portability and the open-weights bazaar.**
The MIT-licensed open weights (DeepSeek, Qwen, Nemotron, Kimi K2) are permanently available and
provider-portable. When a better or cheaper model is released next month, the harness swaps one
env var. The compounding knowledge, the verified skills, the research corpus — none of it is tied
to any model or provider. A subscription user is locked to their provider's model roadmap. A harness
user routes around it instantly. This is the "bring your own model" value that makes the system
compounding-proof against model obsolescence.

---

## The honest segmentation

| Profile | Best option |
|---|---|
| Developer, few hours/day, no special hardware, wants simplicity | Claude Code Pro ($20) or Z.ai GLM Lite (~$10) |
| Full-day power user, wants peak smoothness, willing to pay | Claude Code Max 20x ($200) |
| Developer wanting frontier-ish capability through existing tools cheaply | Z.ai GLM ($10-30) or Kimi Code — Anthropic-compatible, low cost |
| Already in ChatGPT or Google ecosystem | Codex (bundled) or Antigravity/Gemini (note opaque credits) |
| Wants Claude Code-class capability at near-zero marginal cost, has a laptop | **hermes-max + V4-Flash API driver** — ~$5-15/mo, no capex, compounding harness, model-swappable |
| 24/7 unattended workloads, sovereignty required, OR hardware already owned for other reasons | **hermes-max + local LLM** — unlimited volume, $26-64/mo at 7yr amortization depending on device, full sovereignty |
| Wants the harness value regardless of driver | **hermes-max** — bring any API key or local endpoint; the harness adds compounding and verification on top of whatever model wins the cost/capability race this month |

---

---

## API-driven harness monthly opex — concrete BYOK configurations

The following are real monthly operating cost estimates for running hermes-max with no local hardware,
across the most compelling provider combinations as of May 2026. Each assumes a heavy round-the-clock
agentic workload: ~3M driver tokens/day, ~150 synthesis calls/month, ~500 steering calls/month,
best-of-N slop drafting via free tiers, and ≤15 sparing Opus calls/month in the frontier config.
Cache hit rates on stable system prompts assumed at ~80% for input tokens.

### The provider landscape for BYOK

| Provider | Best model for harness role | Cost/M tokens | Context | Rate limits (free) | Notes |
|---|---|---|---|---|---|
| **DeepSeek native** | V4-Flash (driver/steer), V4-Pro (synth) | Flash $0.14/$0.28 (cache $0.0028); Pro $0.435/$0.87 (cache $0.0036) | 1M | No hard rate limit; peak 503s | Cheapest at source; China-hosted; single-region risk |
| **DeepInfra** | V4-Flash, V4-Pro, Kimi K2, Nemotron, GLM-5 | V4-Flash $0.10/$0.20; V4-Pro $1.30/$2.60; gpt-oss-120B ~$0.08/M blended | 1M (V4) | Paid only | Widest open-weight catalog; US-hosted; no-train/no-disk |
| **Cerebras** | GPT-OSS-120B, Qwen3-235B (drafting/steer) | **Free: 1M tok/day**, 30 RPM, 60-100K TPM, 8K ctx cap on free | 8K (free) | 30 RPM, 1M/day | World-record throughput (2,000+ tok/s WSE); ideal for fast slop drafting |
| **Groq** | GPT-OSS-120B, Qwen3-32B, Llama-4-Scout (drafting) | Free: 6-8K TPM per model; paid available | 128K | 6-8K TPM per model | Fast; tight TPM; budget-cap briefs to ~3.5K tokens |
| **Fireworks** | DeepSeek V4-Pro, Qwen3 series | V4-Pro ~$0.90/$2.70; fast throughput | 1M | Paid | High throughput; good V4-Pro fallback |
| **Together.ai** | V4-Pro, Llama 4 Maverick | Competitive; check current | 128K-1M | Paid | Broad catalog; fine-tuning option |
| **Anthropic API** | Opus 4.8 (frontier escalation only) | $5/$25 regular; cache up to 90% off | 1M | Paid | Never the driver; three-gated escalation only |
| **Kimi (Moonshot)** | K2, K2.5, K2.6 | Via DeepInfra or direct | 1M | Paid | ~90-95% Sonnet 4.6; strong long-context |
| **NVIDIA NIM** | GLM-4.7, DeepSeek V3.2 685B, Devstral | Free endpoints available | varies | Free with limits | Broadest model category; worth checking for free-tier synthesis |
| **Gemini (Google AI Studio)** | Gemini 2.5 Flash | ~1,000 free req/day via CLI | 1M | ~1K RPD free | Last-resort steer; free but opaque |

### Monthly opex by configuration

**Config 1 — Minimum cost: free tiers only (`--free` mode)**

Driver: Cerebras GPT-OSS-120B or Qwen3-235B (1M tok/day free, 8K ctx cap — adequate for most turns)
Steer: Groq Qwen3-32B (free, budget briefs <3.5K)
Synth: Cerebras GPT-OSS-120B (free, within daily limit)
Draft pool: Groq + Cerebras free models
Frontier: disabled

Monthly cost: **$0** (within free tier limits)
Honest ceiling: 8K context cap on Cerebras free tier limits long-context tasks; ~1M tok/day Cerebras
limit constrains heavy workloads; synthesis quality below V4-Pro; no compounding research engine on
long contexts. Works well for focused coding sessions and moderate agentic use.

---

**Config 2 — Lean paid: V4-Flash driver, V4-Pro synth, free drafting (`--full` mode, native)**

Driver: DeepSeek V4-Flash native (3M tok/day × 30 days = 90M tok/mo; at 80% cache hit:
  18M cache-miss input × $0.14/M + 72M cached × $0.0028/M + 45M output × $0.28/M)
  = $2.52 + $0.20 + $12.60 = **~$15.30/mo driver**
Steer: V4-Flash (included above, ~500 calls negligible incremental)
Synth: V4-Pro native 150 calls × ~$0.016/call (with cache) = **~$2.40/mo**
Draft: Groq/Cerebras free = $0
Frontier: disabled

**Total: ~$18-20/mo** — this is the "Claude Code Pro equivalent" cost for unlimited volume with
compounding harness. No capex. A laptop + this config.

---

**Config 3 — Lean paid: DeepInfra-hosted for US data posture (`--full` mode, DeepInfra)**

Driver: V4-Flash via DeepInfra (~$0.10/$0.20): same calc → **~$11/mo driver** (slightly cheaper
  input but slightly higher than native at cache-miss rates)
Synth: V4-Pro via DeepInfra 150 calls × ~$0.047/call = **~$7/mo**
Steer: V4-Flash DeepInfra ~$0.30/mo
Draft: free tiers

**Total: ~$18-19/mo** — nearly identical to native, but US-hosted, no-train/no-disk posture,
better single-region resilience. The routing differential is ~$1-2/month at this volume.

---

**Config 4 — Full frontier: V4-Flash driver + V4-Pro synth + sparing Opus (`--frontier` mode)**

Driver + synth + steer: same as Config 2/3 (~$18-20/mo)
Opus 4.8: ≤15 calls × ~$0.18/call (compress-then-reason, ~15K in + 4K out, no cache) = **~$2.70/mo**
With prompt caching on stable brief prefix: **~$1.50-2/mo**

**Total: ~$20-22/mo** — this is the full frontier harness. Same price as Claude Code Pro, with:
- Unlimited driver volume (no 44K/5hr cap)
- Compounding KG/RAG across every session
- Deep-research engine with cited multi-source synthesis
- Verify-gated reliability regardless of model tier
- Sparing Opus 4.8 for genuine frontier-novel synthesis
- Model portability (swap V4-Flash for K2 or Qwen-480B when it's better/cheaper)

---

**Config 5 — Alternative driver: Kimi K2 via DeepInfra**

If K2's 1M context and stronger long-context performance is worth the slight premium over V4-Flash:
Driver: K2 via DeepInfra (check current rate; roughly $0.50-1.00/M at cache-miss rates)
At 80% cache hit and 90M tok/mo: ~$9-18/mo driver depending on cache hit rate
Synth: V4-Pro unchanged

**Total: ~$25-35/mo** — higher driver cost offset by K2's long-context quality advantage on complex
multi-file tasks. Worth considering for projects where 1M-context driver coherence matters more than
driver cost.

---

### The BYOK provider selection principle

The harness is designed around one configuration truth: **the best provider this month is not the
best provider next month.** V4-Flash is the current cheapest frontier-quality driver. Kimi K2.6 may
surpass it next quarter. Cerebras is adding models and raising context limits. Groq's catalog changes
with provider agreements. NVIDIA NIM is adding free models continuously.

The conductor's presence-gated provider chain means the user adds or rotates BYOK providers by
updating `.env` — one key per provider, no code changes. The harness routes to the best present
provider automatically, falls through on failure, and the compounding KG/RAG/research corpus is
untouched by any provider change. This is the open-weights bazaar in practice: the intelligence
accumulates in the harness; the model is a swappable commodity.

The monthly opex for the full frontier harness — $20-22/mo with native DeepSeek, $0 with free tiers
only for moderate use, $25-35/mo with K2 as driver — is the cost of the *model inference* alone.
The compounding memory, the verified synthesis, the research engine, the tool ecosystem: those are
the harness, and they are free.

---


---

## Parallel agents — local depth, API breadth, and the hybrid

### The 262K context is not optional for long-horizon sessions

The 262K window is the functional working minimum for serious engineering, not a ceiling the agent
occasionally touches. A real Hermes session accumulates context continuously: MEMORY.md (~2-4K),
PLAN.md (~3-8K), loaded workflow skills (~8-15K), Hermes scaffolding (~5-10K), and — critically —
the cumulative conversation history and tool results that grow across turns. By hour 2-3 of an
active session the context sits at 100-180K tokens and is still growing.

The 64K silent failure mode demonstrates the stakes: at 64K the model begins compressing history
and PLAN.md coherence degrades. The agent starts forgetting what it decided three subtasks ago.
262K is the minimum for multi-hour multi-subtask coherent work. The RAG and KG compound *across*
sessions to extend the effective memory horizon, but within a session the context window is what
keeps the plan coherent.

### What the 128GB KV budget actually supports

After OS, orchestration stack (10 MCP servers, datastores), embedding and reranker servers, and
page-cache headroom, ~95-98GB is available for KV cache after loading Qwen3.6-35B-A3B at NVFP4
(~17-20GB weights). At ~0.25MB KV per token, the total budget is **~380,000 KV tokens**.

But Hermes sessions are not static. They accumulate. A session that starts at 20K grows to 150K+
over a working day. The honest concurrent session ceiling for deep long-horizon work:

| Session type | KV fill (active) | Concurrent sessions in 380K budget |
|---|---|---|
| Deep long-horizon (2-8hr, growing to 150-250K) | 150-250K | **1-2** |
| Medium focused (1-2hr, 60-120K) | 60-120K | **2-4** |
| Short/fresh sessions (under 40K) | 16-40K | **6-10** |

**For the workload hermes-max is built for — deep long-horizon engineering — the local node
serves 1 primary session with a second viable during its tool-call gaps.** This is not a
limitation; it is the correct architecture. One undivided 262K session grinding 24/7 at zero
marginal token cost, compounding into the KG/RAG between sessions, produces ~750K-1.4M
tokens/day of useful engineering output on one project.

### Local node: one deep-focus project, maximum quality

**Single-session throughput on the local node:**

| Device | Tok/s (single session) | Wall-time fraction | Tokens/day | Tokens/month | Marginal cost |
|---|---|---|---|---|---|
| Jetson AGX Thor 128GB | ~40-50 | ~40% | ~1.4-1.7M | ~42-52M | $0 (electricity paid) |
| DGX Spark 128GB | ~60-80 | ~40% | ~2.1-2.8M | ~63-84M | $0 (electricity paid) |

The ~40% inference fraction reflects realistic agentic operation: the remainder is tool execution,
verification, RAG/KG queries, research I/O, and inter-turn think-time. The model is not generating
every second of the day.

### V4-Flash API: parallel projects at marginal token cost

For running multiple projects simultaneously — where the scaling argument lives — V4-Flash via API
is the right answer. Each additional Hermes terminal points at `VLLM_BASE_URL=deepseek/v1`, gets
a full independent 1M-context window on DeepSeek's infrastructure, and has no KV cache competition
with the local session or with other API sessions. The provider holds the context; you pay tokens.

At $0.14/M cache-miss input, $0.28/M output, with ~80% cache hit on stable system prompts
(effective blended input ~$0.035/M), each additional API-driven session costs roughly:

- Light agentic use (~300K driver tokens/day): ~$3-4/mo
- Moderate (~600K driver tokens/day): ~$6-8/mo  
- Heavy (~1M driver tokens/day): ~$10-14/mo

The MCP infrastructure (verify, KG, RAG, watchdog, research, checkpoint) serves all sessions
from one stack at no incremental cost. KG namespaces and RAG indices are isolated per project.

### The hybrid: local depth + API breadth

The optimal configuration combines both:

**Local (Thor/Spark): one primary deep-focus project** — full undivided KV budget, full tok/s,
zero marginal cost, maximum 262K coherence. The sovereignty and compounding case at its strongest.

**V4-Flash API: additional parallel projects** — each independently contexted, no local resource
competition, ~$3-14/mo per session depending on intensity.

| Configuration | Projects running | Monthly cost | vs Claude Code Max 20x |
|---|---|---|---|
| Local only | 1 deep-focus | ~$14-17/mo | vs $200/mo (1 project) |
| Local + 1 API session | 2 projects | ~$20-25/mo | vs $400/mo |
| Local + 2 API sessions | 3 projects | ~$28-35/mo | vs $600/mo |
| Local + 4 API sessions | 5 projects | ~$42-55/mo | vs $1,000/mo |
| API only (no hardware) | N projects | ~$20-22/mo + $7-14/mo per session | vs $200/mo per session |
| Pure API, 5 parallel sessions | 5 projects | ~$65-85/mo | vs $1,000/mo |

The synthesis/steering overhead (~$7-10/mo for V4-Pro across all sessions, Groq/Cerebras free
drafting) is shared across all sessions — it does not multiply per session.

### What the subscription model cannot do at this scale

Claude Code Max 20x prices parallelization linearly: each session is a separate $200/month
subscription with its own rolling 5-hour window. Two projects = $400/mo. Five projects = $1,000/mo.
Each subscription is stateless — no cross-session memory, no compounding KG, no accumulated
codebase knowledge. The tenth session is as expensive as the first and knows nothing the first
learned.

The harness at 5 parallel projects costs ~$42-85/mo depending on whether the local node is
included. Each session compounds into its own namespace. The MCP infrastructure amortizes across
all of them. The synthesis/steering/Opus escalation ladder is shared. And when a pattern learned
on project-alpha is relevant to project-beta, a human operator can query across KG namespaces —
the knowledge is in structured, portable, queryable storage, not locked inside a chat window that
resets.

That is the concrete value proposition: multi-project concurrent engineering at fixed-or-marginal
infrastructure cost, with compounding intelligence that grows across every session, versus linear
per-seat pricing for stateless sessions that know nothing between resets.


---

*Pricing sourced from official provider documentation and third-party trackers as of May 2026.
All figures are volatile; verify before committing. DeepSeek's promo-as-standing pricing has not
been formally confirmed post-May-31. Hardware lifespan estimates reflect reasonable expectations
for well-maintained embedded/workstation compute.*