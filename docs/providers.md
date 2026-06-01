# Providers — the honest backend table

Providers are **config, not code**. Each is described by what it is *good for*; no
provider is better or worse in the abstract — they occupy different niches in the
chains. A provider whose `api_key_env` is unset is treated as absent and silently
skipped. With nothing but a local endpoint present, the system is fully local and
free.

> **Verify prices and limits against each provider's own console** — the free/cheap
> LLM landscape rotates monthly. The numbers below were accurate at the last
> verification pass; keeping them current is a config edit, never a code change
> (see [roster.md](roster.md)).

## What each backend is good for

| Backend | Good for | Cost (in/out per M) | Context | Throughput | Free? | Honest caveat |
|---|---|---|---|---|---|---|
| **local vLLM** (e.g. Qwen3.6-35B-A3B) | the always-present executor; private, sovereign | $0 / $0 | up to 262K | ~50 tok/s single-stream | yes | sequential — deep research is overnight-grade, not interactive |
| **DeepSeek-direct** V4-Pro / V4-Flash | the cheap quality anchor: V4-Pro planning, V4-Flash driving | Pro $0.435/$0.87 · Flash $0.14/$0.28 (cache-hit far less) | 1M | fast | no | cheap, not free; direct-provider terms |
| **DeepInfra** V4-Pro / Flash | US-hosted, funded path for DeepSeek | ~$1.30/$2.60 (verify live) | 1M | fast | no | list price above direct; the default funded host |
| **OpenRouter :free** | the free planner/synth — Kimi K2.6 (1M ctx), R1, Qwen3-Coder | $0 | up to 1M | varies | yes | 20 RPM, 1000/day per model after a one-time $10 deposit; roster rotates |
| **Groq** (8B / Scout / gpt-oss-120b) | the **research fan-out workhorse** — per-model buckets on one key | $0 | 131K | 8B ~560 tok/s | yes | per-model buckets: 8B 14,400/day, Scout & 120b 1,000/day; small per-model TPM |
| **Cerebras** (gpt-oss-120b / GLM-4.7) | a single very fast chunked-synthesis call | $0 | 64K | ~30K tok/s | yes | 5 RPM + 64K ctx — great for one synth call, not for fan-out |
| **Gemini** (2.5 Flash) | a tracked last-resort steer | $0 | large | — | yes | low free RPD; verify your own console |
| **Anthropic** Opus 4.8 | the optional, rare frontier escalation rung | $5 / $25 (cache $0.50) | 1M | — | no | triple-gated; ~$0.08–1.25/call; genuinely-hard-problems only |

## Why local-first with silent-fallback cloud

No free tier is production-durable. Free tiers and model availability are volatile;
an endpoint vanishing must **degrade the system gracefully, never break it**. That
is the whole point of the design:

- **Groq** is the fan-out workhorse: its strength is high requests/min across
  per-model buckets, ideal for the many small parallel research calls. Its per-model
  TPM is small, so the conductor caps draft input (~3.5K tokens) to stay inside it.
- **Cerebras** is a genuine free asset for a single fast synthesis call, but at 5
  RPM it's a synth rung, not a fan-out source.
- **OpenRouter :free** shines for Kimi-K2.6 synthesis and R1 planning (huge
  context), not for high-RPM fan-out.
- **DeepSeek-direct** is cheapest at source; **DeepInfra** is the US-hosted funded
  default, preferred in chains for uptime reliability.
- **Anthropic Opus** is the spare frontier rung — see [modes.md](modes.md) and
  [cost.md](cost.md) for how rarely it fires.

The structural response to all of this: a local-viable foundation + presence-gated
optional cloud + silent fallback + this honest table. With **no keys at all** it's
the bare local harness, and nothing breaks.

## Adding or swapping a provider

Edit [`config/inference.example.yaml`](../config/inference.example.yaml) (or your
`~/.hermes-max/inference.yaml` copy): each model slot carries a
`# verified: YYYY-MM-DD` comment. When `hm health` flags a slot as missing, find
the replacement, change the id, update the date — one line, no code change. See
[roster.md](roster.md).
