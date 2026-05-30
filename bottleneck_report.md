# bottleneck_report.md — do the advanced features earn their latency?

_Offline demonstration (task = reliability dry-run). Set `HMX_BENCH_CMD` to a model-driven task for a real comparison; the same full-vs-bare split applies._

## Wall-clock + 3-bucket timing split

- **FULL** — wall 0.54s · inference 0.0s (0%) · tool-work 0.4s (100%) · artificial 0.0s (0%) · 7 tool-calls
- **BARE** — wall 0.45s · inference 0.0s (0%) · tool-work 0.3s (100%) · artificial 0.0s (0%) · 3 tool-calls

## Verdict

- ✓ FULL has low artificial cost (0%) — its extra latency is real work (inference + tool-work), not rate-limit waiting.
- FULL is 1.2x the bare wall-clock. If the result quality is meaningfully better, that is justified; if not, the advanced features are not earning their latency.

## How to read this

- **inference** — local model thinking/generation (irreducible real work).
- **tool-work** — tool execution doing real work (crawl, tests, indexing, retrieval).
- **artificial** — waiting on rate-limited APIs, 429/5xx backoff+retries, redundant sequential calls, MCP overhead. A large artificial fraction means a specific feature is wasting the agent's time — the line above names which.
