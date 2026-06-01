# hermes-max

A local-first agentic coding harness that wraps the **Hermes agent** with
verification, memory, research, and a cost-aware multi-provider brain —
Claude-Code-class engineering at a fraction of the cost.

It splits every task into an expensive **plan** and a cheap **execute**, gates
"done" behind real lint/typecheck/test runs the agent *cannot* bypass, and
compounds what it learns into a codebase index, a knowledge graph, and a
self-improving skill library — so each task starts already knowing your stack.
Underneath sits an **inference fabric**: your MCP servers ask for a *role*, the
fabric picks a provider from your config, and missing API keys simply drop out.
Bring a GPU and run near-free, or bring a couple of API keys and run on any
laptop.

---

## Quickstart

```bash
# 0. Prerequisite: install the Hermes agent — github.com/nousresearch/hermes-agent
# 1. Clone and enter
git clone https://github.com/patrickbdevaney/hermes-max && cd hermes-max

# 2. Bootstrap (idempotent; builds venvs, copies config, registers MCP servers)
./install.sh

# 3. Add the keys you have
cp .env.example .env      # then edit — you need EITHER a local endpoint OR a DeepSeek key

# 4. Start the stack in a profile, then build something
hm up --free              # own a GPU  → local drives, free planner   (Profile A)
hm up --full              # no GPU     → economic API drives + plans  (Profile B)
hermes                    # launch the agent and give it a task
```

That's the whole on-ramp. `hm down` stops everything · `hm status` shows what's
running, the active mode, and today's spend · `hm dev` opens the one-window
cockpit. See **[QUICKSTART.md](QUICKSTART.md)** for the annotated version.

---

## Pick your profile

You think in two profiles. (There are six fine-grained modes underneath, for
later — see [docs/modes.md](docs/modes.md).)

### 🟢 Profile A — Bring-Your-Own-GPU  ·  `hm up --free`
For owners of a **DGX Spark, Jetson Thor, RTX 6000/5090/4090, or Mac Studio**.
Your local model drives (big context, many turns, free but for electricity); a
free **OpenRouter** model (Kimi K2.6, 1M ctx) plans. Near-zero marginal cost.
Deposit $10 on OpenRouter for 1000 free requests/day and add `--free-uplift`.

### 🔵 Profile B — No-GPU  ·  `hm up --full`
For **laptops, mini PCs, Mac minis, VPSes** — anyone without a capable GPU.
**DeepSeek V4-Flash** drives, **V4-Pro** plans, both over API. About **$17/month**,
no rate limits — roughly 10% of a Claude Code Max subscription.

> Local driving is free but slower; API driving costs pennies but is faster.
> Pick the one that matches your hardware — you can switch live with `hm mode`.

---

## The mental model

```
  Hermes agent ── MCP servers ──▶ ask for a ROLE   (code_plan, code_execute, research…)
                                        │
                          inference fabric (lib/inference)
                                        │
                  pick the first provider in the role's chain
                  whose API key is present ── missing keys drop silently
                                        │
        local vLLM · DeepSeek · OpenRouter · Groq · Cerebras · Gemini · Anthropic
```

Providers are **config, not code**. One word — `hm mode <name>` — reassigns every
chain (who plans, who executes) and sets a spend ceiling. **Zero keys runs pure
local and free; nothing breaks when a provider is absent.** Full picture:
[docs/architecture.md](docs/architecture.md).

---

## What you need

- **The Hermes agent**, installed and on PATH (the harness wraps it; it is not bundled).
- **Python 3.10+**. **Docker** is optional (only for SearXNG / Crawl4AI / Phoenix containers).
- **At least one driver path:** a local OpenAI-compatible endpoint (`VLLM_BASE_URL`)
  **or** a paid key (DeepSeek / DeepInfra). Everything else is an optional accelerator.

Bring any subset of these — each is described by what it's **good for**:

| Provider | Good for | Tier |
|---|---|---|
| **Local endpoint** (vLLM / llama.cpp / MLX) | the always-on executor — private, sovereign, free | free (BYO GPU) |
| **OpenRouter** | the free planner — Kimi K2.6, 1M context | free* |
| **Groq** | the research fan-out workhorse — high requests/min, per-model buckets | free |
| **Cerebras** | a single very fast synthesis call | free |
| **Gemini** | a tracked last-resort steer | free |
| **DeepInfra** | the funded API driver & planner — DeepSeek V4-Flash / V4-Pro, US-hosted | paid |
| **DeepSeek** (direct) | the cheapest quality anchor — V4-Pro plans, V4-Flash drives | paid |
| **Anthropic** | the optional, rare frontier escalation — Opus 4.8 | paid |

\* OpenRouter's free models unlock 1000 requests/day after a one-time $10 deposit.

Full, honest cost/context/throughput table: **[docs/providers.md](docs/providers.md)**.

---

## Go deeper

| If you want to… | Read |
|---|---|
| Understand the design & the config trinity | [docs/architecture.md](docs/architecture.md) |
| Compare the two profiles in depth | [docs/profiles.md](docs/profiles.md) |
| See all six modes and their role chains | [docs/modes.md](docs/modes.md) |
| Choose a local model for your hardware | [docs/hardware.md](docs/hardware.md) |
| Deploy on a mini PC / laptop / desktop / DGX | [docs/deployment.md](docs/deployment.md) |
| Read the honest provider backend table | [docs/providers.md](docs/providers.md) |
| Understand what each $ buys | [docs/cost.md](docs/cost.md) |
| Keep model IDs current as providers change | [docs/roster.md](docs/roster.md) |
| Learn the deep-research engine | [docs/research-engine.md](docs/research-engine.md) |
| Reference the MCP servers | [docs/mcp-servers.md](docs/mcp-servers.md) |
| Reference the skill catalogue | [docs/skills.md](docs/skills.md) |
| Fix a common failure | [docs/troubleshooting.md](docs/troubleshooting.md) |

The `CLAUDE_*.md` build specs that produced this system are kept for provenance
in [archive/specs/](archive/specs/) — you do not need them to use hermes-max.

## License

No license is declared yet. Add a `LICENSE` file at the repo root to set terms.
