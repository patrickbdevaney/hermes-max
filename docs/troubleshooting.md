# Troubleshooting & deep telemetry

Three commands cover ~95% of operational needs:

- **`hm status`** — is everything OK and what's it costing? (rolls up health +
  active mode + spend + roster warnings + remaining free RPD)
- **`hm dev`** — the single live operational view (cockpit).
- **`hm cost`** — the spend view.

Everything below is for when you are specifically debugging.

## Graceful degradation — what each absence looks like

Every component continues with a warning, never a hard fail:

| Component | Backend absent/down ⇒ |
|---|---|
| embeddings (`EMBED_BASE_URL`) | RAG runs **BM25 + graph** (no dense lane) |
| reranker (`RERANK_BASE_URL`) | RAG returns the **fused order** (no `+rerank`) |
| Crawl4AI | `fetch_clean` falls back to local **trafilatura** |
| SearXNG | `search_docs` reports unavailable (other tools unaffected) |
| local chat model | `ingest_doc` stores **raw** markdown (no distil) |
| RAG/KG (for docs) | note/entities not stored, reported (fetch/distil still work) |
| deep-research | SearXNG/Crawl4AI down ⇒ fewer/no sources; reranker absent ⇒ authority-only ranking; chat model unset ⇒ **deterministic** plan/queries/synthesis |
| dspy / gepa | self-evolution is a no-op (exit 0) with an install hint |
| escalation cloud tier | OFF by default; local tier tried first; never required |
| conductor role (steer/synth/escalate) | a role with **no present key** is OFF ⇒ driver proceeds local-only |
| conductor rung fails (429/5xx/cap) | **silently falls** to the next present rung (logged one-liner); none ⇒ `proceed_local` |
| parallel_draft pool | absent free keys aren't drafted; exhausted sources skipped; zero keys ⇒ N=1-local |
| Phoenix (OTLP) | spans dropped silently; servers run unaffected |

## Common failures

- **`hm up --free` warns about no local endpoint.** You're on a no-GPU box. Use
  `hm up --full` (Profile B), or point `VLLM_BASE_URL` at a cloud chat endpoint.
  See [deployment.md](deployment.md).
- **A model slot shows `missing` in `hm health`.** The provider renamed/retired the
  id. Update one line in `config/inference.example.yaml` (or your
  `~/.hermes-max/inference.yaml`). See [roster.md](roster.md).
- **Research returns thin / no sources.** SearXNG or Crawl4AI isn't up — start
  `./searXNG.sh` / `./crawl4ai.sh`, or accept the trafilatura/authority-only path.
- **A 429 from a free tier.** The bucket tracker should pre-skip over-limit rungs;
  if you see one, the per-model TPM/RPD may have changed — `hm health` shows
  remaining budget. Groq's per-model buckets are small by design (see
  [providers.md](providers.md)).
- **The agent "hangs" on a long-running server.** That's success, not a hang — the
  `workflow-long-running-processes` skill and the watchdog handle this; never poll a
  process that never ends.
- **One MCP server is DOWN.** The others stay healthy and Hermes keeps running — the
  tool simply reports unavailable. Restart just that one: `hm restart <server>`.

## Deep diagnostic views (when something breaks)

These are intentionally **not** part of the default cockpit — reach for them only
when debugging:

```bash
hm watch                 # the raw live tool-call stream (every call, heartbeat, fallback, decision)
hm observe               # the live waterfall: where wall-clock actually went
hm logs <server>         # tail a single server's log (~/.hermes-max/logs/)
hm summary               # per-task tool-call summary + the bottleneck split
hm bottleneck            # run the SAME task FULL vs BARE and compare timing splits
hm preflight             # validate the whole stack before a task (PASS/FAIL/WARN, auto-fixes)
hm smoke                 # a real ~15-min end-to-end agent proof
hm eval [capability]     # prove each feature works in a REAL agent turn (asserts real effects)
```

The bottleneck split classifies wall-clock as **inference** (model thinking,
irreducible) · **tool-work** (real execution: crawl, tests, indexing) ·
**artificial** (rate-limit waits, 429 backoffs, redundant calls, MCP overhead). A
large `artificial` fraction means a feature is wasting time — the summary names
which. Beyond the live stream, full OTel spans are in Phoenix (`./phoenix.sh`,
UI :6006).

The verbosity of the live stream is `.env`-controlled
(`HERMES_MAX_VERBOSITY=quiet|normal|verbose|debug`). Telemetry never breaks a tool:
a logging failure is swallowed.

## Proving the anti-Frankenstein property

```bash
kill $(cat ~/.hermes-max/run/kg.pid)   # take down one server
hm health                              # kg shows DOWN, others ✓, exit 1
hm restart kg                          # restart only the dead one
```
