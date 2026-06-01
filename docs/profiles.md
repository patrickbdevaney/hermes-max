# Profiles — the two ways to run hermes-max

You think in **two profiles**. They are the spine of the system. The six
fine-grained [modes](modes.md) are an advanced layer underneath; the profiles map
onto the two you'll actually start with.

| | **Profile A — Bring-Your-Own-GPU** | **Profile B — No-GPU** |
|---|---|---|
| For | DGX Spark, Jetson Thor, RTX 6000/5090/4090, Mac Studio | laptops, mini PCs, Mac minis, VPSes |
| Who drives (executes) | your **local model** (free, sovereign, big context) | **DeepSeek V4-Flash** over API |
| Who plans | **Kimi K2.6** (free, OpenRouter, 1M ctx) | **DeepSeek V4-Pro** over API |
| Marginal cost | ~$0 (electricity only) | ~**$17/month**, no rate limits |
| Command | `hm up --free` | `hm up --full` |
| Maps to mode | `free` | `full` |

---

## Profile A — Bring-Your-Own-GPU (`hm up --free`)

You own a capable accelerator, so the heavy, high-volume work — every execute
turn, the long-horizon grind, deep research distillation — runs **locally and
free**. A free OpenRouter model (Kimi K2.6, 1M context) does the expensive
*planning*, where a near-frontier model earns its keep.

- **Near-zero marginal cost.** The only spend is optional free-tier accelerators
  (Groq/Cerebras), which cost $0.
- **Sovereign.** Your code never leaves the box for execution.
- **Free uplift (optional).** Deposit $10 on OpenRouter once to unlock 1000 free
  requests/day per model, then `hm up --free --free-uplift` adds a per-file
  coherence check at $0. See [architecture.md](architecture.md) §18.
- **Step up cheaply.** Want stronger architectural judgment than Kimi-free?
  `hm mode full-local` swaps the planner to DeepSeek V4-Pro for ~$1.50/month while
  execution stays local and free.

Hardware floor and model choice: see [hardware.md](hardware.md). The honest
recommendation is a **24–32B-class local executor**; below that, lean on cloud
(Profile B) or heavier cloud uplift.

## Profile B — No-GPU (`hm up --full`)

No capable GPU? The economic API path drives and plans entirely over the network.
DeepSeek **V4-Flash** executes (hundredths of a cent per turn, 1M context, cache),
**V4-Pro** plans. Both run through the funded DeepInfra endpoint by default, with
direct DeepSeek as a configurable alternative.

- **~$17/month** for daily coding use, **no rate limits** — roughly 10% of a Claude
  Code Max subscription.
- **Runs on anything.** A Mac mini, a $300 mini PC, or a small VPS is enough; the
  embedding/rerank services are optional and RAG falls back to BM25 automatically
  (see [deployment.md](deployment.md)).
- The free accelerators (Groq/Cerebras/OpenRouter) still slot in for research
  fan-out and synthesis at $0.

---

## Switching live

Profiles aren't a one-time choice. `hm mode <name>` reassigns every role chain
without a restart, and `hm mode --list` shows all six postures. Start on the
profile that matches your hardware; move between modes as the task warrants
(e.g. `frontier` for one genuinely hard session, then back to `full-local`).

See [modes.md](modes.md) for every mode and its role chains, and
[cost.md](cost.md) for what each dollar buys.
