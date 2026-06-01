# The deep-research engine — design and honest tradeoffs

Deep research is a **parallel multi-bucket fan-out** with an honest cost ledger.
The principle the design is built on: **retrieval is cheap; per-sub-question model
reasoning is the wall-time.** So minimize big-model calls, parallelize the rote
work across independent free rate buckets, and synthesize once.

## The pipeline (each step names a ROLE, not a provider)

| Step | Role / mechanism | Calls | Where the time goes |
|---|---|---|---|
| 1. Decompose | `research_plan` | 1 model call | a synthesis-class call |
| 2. Retrieval fan-out | zero-LLM, parallel: SearXNG + arxiv + openalex + hn + stackexchange + github; capture trafilatura→Crawl4AI→Jina | 0 model | network-bound, parallel |
| 3. Field work | `research_fanout` — query-expand, relevance-filter, dedup, extract | MANY small fast calls, parallel across buckets | bounded by bucket RPM |
| 4. Corroboration | zero-LLM: local Qwen3-Embed/Reranker-0.6B + ≥2-source gate + KG triangulation | 0 model | local, fast |
| 5. Synthesize | `research_synth` | 1 model call | a synthesis-class call |
| 6. Compound | verified claims + citations → RAG (sqlite-vec) + KG; future queries hit `corpus_hit_check` first | 0 model | — |

Wall-time is dominated by the **two synthesis-class calls (plan + synth)** plus
parallel retrieval — **not** a sequential per-sub-question loop. The fan-out (step
3) issues calls concurrently up to each bucket's RPM, with the bucket tracker
(`lib/inference/buckets.py`) preventing 429s by pre-checking headroom.

## The honest tradeoffs (read before you choose a posture)

- **Local-only research = slow, sequential, deep.** One stream, no parallel
  buckets. Fine for an unattended overnight run; painful interactively.
- **Multi-model API fan-out = faster but still bounded** by RPM and by the two
  synthesis-class calls. Not instant. The win is *parallelism across free buckets*,
  not raw speed.
- **Cerebras** is fast (~30K tok/s) but **5 RPM** + 64K ctx → best as a single
  chunked-synthesis rung, never for fan-out.
- **Groq free = per-MODEL buckets on one key** → the fan-out workhorse
  (llama-3.1-8b at 14,400/day; Scout & gpt-oss-120b at 1,000/day each).
- **OpenRouter :free** (post one-time $10 deposit) = 20 RPM / 1,000 RPD per model,
  roster rotates → great for **Kimi-K2.6 synthesis (1M ctx)** + **R1 planning**,
  not for high-RPM fan-out.
- **DeepSeek / DeepInfra / Kimi paid** → when free buckets are exhausted or quality
  matters; cheap, but not free.

## Cost honesty

Every research call lands in the central ledger (`hm cost`): tokens + USD in
`$0.000000`, with a free-vs-paid split and the remaining daily free RPD per model,
so you can see exactly how much budget the free constellation has left today.

## Implementation status (honest)

The role-addressed pipeline above is the design the fabric supports today: the
research engine reaches the model exclusively through `research_plan` /
`research_fanout` / `research_synth` roles, so swapping which provider answers each
step is a `roles.yaml` / `hm mode` edit. The retrieval and corroboration stages are
already zero-LLM and parallel. The fan-out step's concurrency is bounded by the
bucket tracker; where the current engine still runs a step sequentially, that is a
wiring detail, not a design constraint — the roles and buckets are in place to
parallelize it without touching provider code. Measure a run with `hm cost` to see
the per-bucket call distribution and the two-synthesis-call wall-time profile.
