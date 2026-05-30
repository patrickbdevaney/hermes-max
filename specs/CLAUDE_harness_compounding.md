# CLAUDE_harness_compounding.md — Wire hermes-max into a Compounding, Self-Seeding, Self-Evolving Local Harness

You are upgrading the already-complete `hermes-max/` harness (eight MCP servers, two-axis robustness
+ capability stack, 50/50 validated) into a system that (a) **compounds** — gets measurably better on
the operator's own work over time via GEPA/DSPy skill evolution, (b) **self-seeds technical knowledge**
— finds, ingests, and distills documentation for novel/domain-specific frameworks into high-signal
retrievable memory, entirely self-hosted, and (c) **installs and configures itself** with zero friction
(single shell command, no `chmod +x` dance, auto-detects/creates venvs and MCP registrations for any
user). Everything stays lean and local: the ONLY hard external dependencies are the local vLLM
OpenAI-compatible endpoint (`$VLLM_BASE_URL`) and a self-hosted SearXNG. No paid APIs, no cloud keys
required for any core capability. Work in STAGES, in order; each stage independently committed,
smoke-tested, validated. Read the whole spec first. Report after each stage.

## NON-NEGOTIABLE DISCIPLINE (unchanged — the anti-Frankenstein gate)
1. **Extend only via native surfaces** (Hermes config, MCP servers, skills, hooks). **Never modify
   Hermes's core loop.** If a lever seems to need a loop change, implement it as an MCP server +
   skill + config and note the limitation.
2. **Each value-add is an independent MCP server or an extension of an existing one** — own venv,
   127.0.0.1 bind, healthcheck, smoke_test, OTel spans, register-mcp entry. Kill-any →
   degrades-gracefully, never crashes the agent.
3. **Lean & local & sovereign.** Core capabilities require ONLY local vLLM + self-hosted SearXNG.
   Any optional cloud path stays off-by-default, behind an env flag, USD-capped. No capability may
   HARD-fail when offline — it degrades with a clear warning.
4. **Single `$VLLM_BASE_URL`** for the chat model; new local model roles (embeddings, reranker,
   extraction-distill) get their OWN explicit env vars, each optional with a graceful fallback.
5. **Discovery-first.** Before building, grep Hermes config/docs for a native knob (web.backend,
   web.extract_backend, embeddings settings, skills install paths, cron). Prefer native; build only
   the gap; report native-vs-built.
6. **Zero-friction setup is itself a deliverable** (Stage 0). Back up `~/.hermes/config.yaml`
   (timestamped) before edits. Commit each stage.

## EXISTING STACK (build on; do not duplicate)
8 servers: verify(9101), codebase-rag(9102, BM25+dense+graph), knowledge-graph(9103),
observability(9104), escalation(9105, classifier+local+cloud), checkpoint(9106, revert+state),
watchdog(9107), search(9108). dspy-evolution/ cron wrapper (currently graceful no-op — package
unbundled). RAG has run BM25-only since inception because `EMBED_BASE_URL` was never set and the
chat vLLM doesn't serve `/embeddings`. SearXNG container provided; web_extract NOT self-hosted.

---

## STAGE 0 — ZERO-FRICTION AUTO-SETUP & ENV/DEP SYNC (do first; makes every later stage installable in one command)
The dev process must become a single no-friction command for any user on any of the operator's
machines (laptop, your inference host, mini-PC), with no `chmod +x`, no manual venv creation, no manual MCP
registration, and automatic detection/installation of any new server or pip dependency a later
stage introduces.

### 0.1 — `bootstrap.sh` — the one command (invoked as `bash bootstrap.sh`, never needs chmod)
- Always invoked via `bash bootstrap.sh` (so no execute bit needed); internally it `chmod +x`'s the
  repo's own scripts so the user never has to.
- Idempotent. Detects: OS/arch (x86 vs ARM/your inference host), Python version, whether Hermes is installed and
  WHICH python/venv it uses (`hermes which python` or follow `$(which hermes)` shebang), whether
  Docker is present (for SearXNG/Crawl4AI/Phoenix), and whether each MCP server's venv exists.
- For EACH MCP server dir (including any added by later stages — discovered by scanning for
  `*/requirements.txt` + `*/server.py`): create `.venv` if missing, `pip install -r requirements.txt`,
  run its `smoke_test.py`, report PASS/FAIL. This is the "detect new MCP, install & configure" loop —
  generic, so Stages 1–4 just drop a new server dir and bootstrap picks it up.
- Reads/writes `.env` from `.env.example` if absent; prompts (with sane defaults) ONLY for values
  with no safe default; everything else defaults silently.
- Calls `scripts/register-mcp.sh` and `scripts/apply-config-deadlines.sh` (idempotent), then prints a
  healthcheck summary + the exact `hermes` restart line.
- `--check` mode: dry-run audit (what's missing) without changing anything.

### 0.2 — `lib/ensure_dep.sh` + in-server lazy-install guard
- A shared helper any server/script sources to ensure a pip dep is present in ITS venv before use
  (mirrors Hermes's own lazy-install behavior, e.g. `ddgs`). New deps a later stage needs (gepa,
  dspy, crawl4ai, sentence-transformers client, etc.) are declared in that server's requirements.txt
  AND guarded at runtime so a partially-set-up machine self-heals on first call rather than crashing.

### 0.3 — New-MCP auto-registration manifest
- A single `mcp-manifest.yaml` listing every server (name, port, dir, healthcheck). `register-mcp.sh`,
  `start-all.sh`, `healthcheck.sh`, `smoke-test.sh`, and `bootstrap.sh` all READ this manifest instead
  of hardcoding the server list — so adding a server in a later stage = one manifest line, and every
  script picks it up automatically. Refactor the existing scripts to consume the manifest (do NOT
  change their behavior, just their source-of-truth).

**Stage-0 DoD:** `bash bootstrap.sh` on a fresh clone (no venvs, no .env) brings the entire existing
8-server stack to healthcheck-green with zero other manual steps and zero chmod; `--check` audits
cleanly; the manifest drives all scripts; adding a dummy server dir is auto-discovered. Committed.

---

## STAGE 1 — CLOSE THE EMBEDDING/RERANKER GAP (local, finally turns RAG hybrid)
RAG has been BM25-only since inception. Close it with local models on vLLM — no external API.

### 1.1 — Local embedding + reranker serving
- Add `serve-embed.sh`: serve **Qwen3-Embedding-0.6B** (MTEB-Code 80.68, top of leaderboard;
  Matryoshka dims 32–1024; ~1–2GB) on a dedicated vLLM port (e.g. 8002) exposing `/embeddings`.
  Add `serve-rerank.sh`: serve **Qwen3-Reranker-0.6B** (cross-encoder) on another port (e.g. 8003).
  Both tiny; both run alongside the chat model on your inference host's 128GB. Document your inference host contention (they
  share the bus with the chat stream — measure; they're small enough to be negligible at idle but
  bound concurrent calls).
- Set `EMBED_BASE_URL=http://localhost:8002/v1`, `EMBED_MODEL=Qwen3-Embedding-0.6B`,
  `RERANK_BASE_URL=http://localhost:8003/v1` in `.env`/`.env.example`. All optional: blank ⇒
  mcp-codebase-rag stays BM25+graph (the proven fallback), with a clear healthcheck banner.

### 1.2 — Wire hybrid + rerank into mcp-codebase-rag
- When `EMBED_BASE_URL` is set: dense retrieval joins BM25 + graph-rank (the hybrid the code already
  supports but never had an endpoint for). When `RERANK_BASE_URL` is set: rerank the fused top-K with
  the cross-encoder before returning (the single highest-precision-per-token RAG lever). Both
  independently optional; degrade to the next-best mode with a warning. Smoke-test all four modes
  (bm25-only, +dense, +graph, +rerank) and the fallbacks.

**Stage-1 DoD:** embed+rerank servers live (or cleanly absent); RAG runs full hybrid+rerank when
endpoints present, degrades to BM25+graph when not; healthcheck reports the active mode honestly;
retrieval precision on a known query measurably improves with rerank on. Committed.

---

## STAGE 2 — SELF-HOSTED DOCUMENTATION INGESTION (the sovereign knowledge pipeline)
Today: SearXNG returns only titles/URLs/snippets; reading a page needs an EXTERNAL extract provider
(Firecrawl/Tavily/Exa keys) — the one hole in "self-contained." Close it with a local extractor and
build the full novel-framework knowledge loop:
**SearXNG (search) → Crawl4AI (extract→clean markdown) → local model (distill) → RAG + KG (store).**

### 2.1 — `mcp-docs` server (new, e.g. port 9109) — self-hosted extract + ingest
- Stand up **Crawl4AI** (pip `crawl4ai` + `crawl4ai-setup`; ARM64 Docker image available for the
  your inference host) — fully self-hosted, no API key, Playwright-backed, emits clean RAG-optimized "fit markdown"
  with built-in BM25 noise filtering. This is Hermes's missing `web_extract` backend, sovereign.
- Tools:
  - `search_docs(query, category?)` → calls the local SearXNG (`$SEARXNG_URL`) for candidate URLs
    (reuses Hermes's native SearXNG; supports its category filters: research-paper/pdf/docs).
  - `fetch_clean(url)` → Crawl4AI → clean markdown (the sovereign replacement for Firecrawl extract).
  - `ingest_doc(url|markdown, topic)` → fetch_clean → distill via the **local chat model**
    (`$VLLM_BASE_URL`): summarize to a high-signal technical note (keep code blocks, signatures,
    version-specific facts; drop boilerplate) → write to mcp-codebase-rag index under a `docs/<topic>`
    namespace AND record key entities/relations to mcp-knowledge-graph (framework→API→usage). This is
    the high-signal accumulation, separate from but co-retrievable with code-trace memory.
  - `research_topic(topic)` → orchestrates search_docs → pick top N → ingest_doc each → return a
    distilled topic brief. The "learn a novel framework" entry point.
- Discovery: also set Hermes-native `web.extract_backend` to point at this local extractor if Hermes
  supports a custom/self-hosted extract URL (per the web-search docs' self-hosted Firecrawl pattern);
  if it does, the native `web_extract` tool becomes sovereign too. Report whether native wiring worked.
- Graceful: Crawl4AI down ⇒ fall back to `curl` + a readability/`trafilatura` text extract (pip,
  local, no JS) with a warning; SearXNG down ⇒ tool reports unavailable; never crash.

### 2.2 — Skill: `workflow-learn-framework`
- When the agent hits a novel/domain-specific framework it can't reason about from pretraining (signal:
  repeated hallucinated-API failures from mcp-verify, or low-confidence on unfamiliar imports): trigger
  `research_topic` BEFORE coding — search official docs, ingest the high-signal subset, then implement
  against retrieved real signatures. Closes the hallucinated-API trap at the knowledge layer (prevention),
  complementing the verify-layer detection. Gate on the shared `classify_difficulty`/novelty signal.

### 2.3 — Optional pre-seed corpus
- Add `scripts/seed-docs.sh <topic-list>` — batch-ingest a user-supplied list of doc URLs (their
  stack: framework docs, internal wikis exported to markdown, RFCs) into the `docs/` RAG namespace +
  KG up front, so a fresh deployment starts already knowing the operator's domain. Optional, idempotent,
  re-runnable. Answers the "do we need a preprocessed document set" question: NOT required (the agent
  self-seeds on demand via 2.2), but pre-seeding is a cheap accelerator and this makes it one command.

**Stage-2 DoD:** mcp-docs live; full sovereign loop works end-to-end on a real novel framework
(search→extract→distill→store→retrieve) with NO external API; `workflow-learn-framework` triggers on
the novelty signal; seed-docs batch-ingests; every external-API path is replaced by a local one or
degrades gracefully. Committed.

---

## STAGE 3 — WIRE DSPy/GEPA SELF-EVOLUTION (the compounding loop, finally live)
GEPA (`pip install gepa`, or `dspy.GEPA`) is reflective prompt evolution: it reads structured execution
traces + textual feedback, reflects with an LLM, and evolves prompts/skills along a Pareto frontier —
needs only ~10 examples / 20–100 evaluations (your inference host-viable), runs entirely on the local model as BOTH
task_lm and reflection_lm. This turns the static skill library into one that improves on the operator's
own codebase over time.

### 3.1 — Install path (resolve the "pip vs curl" question)
- Hermes was curl-installed and owns its own env; `hermes-agent-self-evolution` is a separate, unbundled
  repo. Do NOT pip into Hermes's env. Instead: `dspy-evolution/` gets its OWN venv (via the Stage-0
  bootstrap) and installs `dspy-ai`, `gepa`, and (if available) `hermes-agent-self-evolution` there.
  The evolution job runs OUT-OF-PROCESS, reading traces by FILE PATH and writing optimized skills by
  FILE PATH — it never needs to import Hermes. This is the clean, env-isolated wiring.

### 3.2 — The evolution loop (`dspy-evolution/run-evolution.sh`, now functional)
- **Trace source:** read the Hermes session SQLite store + the mcp-observability Phoenix spans
  (task outcomes, verify pass/fail, stuck events, escalations) — the labeled signal GEPA needs.
- **Optimization targets (in priority order):**
  (a) the **difficulty classifier** prompt (mcp-escalation `classify_difficulty`) — it gates search
      depth, verify depth, and escalation across the whole stack, so improving it lifts everything;
      label data = actual task outcomes (got-stuck / clean / needed-escalation).
  (b) the **workflow skills** (the SKILL.md instructions) — evolve the ones with the most failure
      traces; GEPA's textual-feedback reflection reads the actual stuck/verify-fail logs.
  (c) the **critic** prompt (workflow-critic) — optimize against caught-vs-missed silent-wrong patches.
- **Metric:** a `dspy.Prediction(score, feedback)` where score = verify-green + completed + low-stuck,
  feedback = the actual error/stuck text (GEPA's Actionable Side Information). Bounded
  `max_metric_calls` (default ~50, configurable) so a run is hours-not-days on your inference host.
- **Output:** write evolved prompts/skills as NEW versioned variants under
  `~/.hermes/skills/hermes-max/` (never overwrite in place — keep the prior version; A/B-able). Record
  the before/after Pareto scores to the KG so improvement is auditable.
- **Schedule:** weekly cron (register-cron.sh, already present), gated to run only when ≥N new traces
  exist (no point optimizing on no data). Graceful no-op (exit 0) if gepa/dspy unavailable, with
  install instructions — but Stage-0 bootstrap should have installed them.

### 3.3 — Closing the escalation→classifier learning loop
- When a task escalates and the higher tier solves it, record the task's difficulty features +
  outcome to the trace store. The next GEPA run uses these as labels to improve `classify_difficulty`
  — so every escalation becomes training signal and the local model handles progressively more of the
  formerly-escalated band. (This is the compounding flywheel: better classifier → better gating →
  more local wins → fewer escalations.)

**Stage-3 DoD:** dspy-evolution venv installs gepa+dspy; run-evolution reads real traces, runs a
bounded GEPA optimization on the difficulty classifier (and one skill), writes a versioned improved
variant, records before/after scores to KG; cron gated on trace count; escalation outcomes feed back
as labels. Demonstrate a measurable score lift on a small held-out trace set (honest — if no lift on
sparse data, report it as "needs more traces," which is correct signal). Committed.

---

## STAGE 4 — MemGPT-STYLE SELF-EDITING MEMORY (borrow the pattern, not the framework)
Letta/MemGPT is a whole agent framework that would overlap Hermes — do NOT run it as a parallel agent.
Instead borrow its proven PATTERN: tiered, self-editing memory with the agent managing its own
high-signal "core memory" block.

### 4.1 — Extend mcp-knowledge-graph (or a thin sibling) with self-editing memory blocks
- Add `core_memory_get/append/replace(block)` — a small, always-in-context, agent-curated block of
  the highest-signal facts about the current project/codebase (conventions, gotchas, the architecture
  one-liner). The agent edits it deliberately (MemGPT's insight: let the model own its working
  memory), distinct from the auto-accumulated KG triples and RAG chunks.
- A `workflow-memory-curation` skill: at task boundaries, the agent reviews and prunes core memory
  (keep high-signal, evict stale) — and this curation itself becomes a GEPA optimization target over
  time. Bound the block size (it's always in context — protect the window).
- Audit: confirm whether Hermes's NATIVE memory (memory.md / distillation pass) already covers this;
  if so, wire to it rather than duplicating (discovery-first). Report native-vs-built.

**Stage-4 DoD:** self-editing core-memory block works (get/append/replace, size-bounded, round-trips);
curation skill prunes at task boundaries; either extends KG cleanly or wires to native Hermes memory
without duplication; degrades gracefully. Committed.

---

## CROSS-CUTTING
- **OTel spans** for every new capability: doc_ingested, framework_learned, rerank_applied,
  gepa_run_started/completed, skill_evolved, core_memory_edited, classifier_relabeled.
- **Manifest-driven:** every new server (mcp-docs 9109, embed/rerank are vLLM not MCP) is one
  `mcp-manifest.yaml` line; all scripts pick it up.
- **Graceful-degradation matrix:** document + test the offline behavior of EVERY new component
  (embed/rerank absent → BM25+graph; Crawl4AI down → trafilatura; SearXNG down → unavailable; gepa
  absent → no-op; reranker down → fused-no-rerank). The agent always continues with a warning.
- **Sovereignty assertion (add to README + a test):** with ALL external API keys unset and only
  vLLM + SearXNG + Crawl4AI running locally, the full loop — search, extract, distill, store,
  retrieve, verify, evolve — works. This is the headline property; prove it.

## OUT OF SCOPE (anti-Frankenstein)
- No running Letta/MemGPT/Mem0 as a parallel agent framework (borrow the memory pattern only).
- No core-loop modification; no replacing Hermes's planner/delegation/native memory wholesale.
- No cloud-dependent extract/search/embedding as a REQUIRED path (all must have a local default).
- No unbounded GEPA runs (your inference host is one box; bound max_metric_calls).
- No 8-stage RAG zoo (HyDE/RAG-Fusion/ColBERT/Self-RAG) — the sanctioned additions are exactly the
  local reranker (Stage 1) and the doc-ingestion namespace (Stage 2); add more only if eval shows
  retrieval precision is still the bottleneck.
- Don't reintroduce known traps (kill-the-waiter, lossy sub-agent summaries, ungrounded self-correction).

## REPORT (per stage)
What was implemented as {config / new server / extended server / skill / script}; native-vs-built
(discovery honesty); smoke + validation PASS/FAIL per assertion (failures are signal — report, don't
paper over); the graceful-degradation test; the git commit SHA.

## DEFINITION OF DONE (whole spec)
`bash bootstrap.sh` brings any fresh machine fully live in one frictionless command (no chmod, auto
venv/dep/MCP detection via manifest). RAG is hybrid+reranked with local models (BM25+graph fallback).
The sovereign documentation loop (SearXNG→Crawl4AI→local-distill→RAG/KG) lets the agent learn novel
frameworks on demand with NO external API, and `seed-docs.sh` pre-seeds a domain corpus optionally.
GEPA/DSPy self-evolution runs on real traces, evolves the difficulty classifier + skills + critic into
versioned variants, and escalation outcomes feed back as classifier labels (the compounding flywheel).
MemGPT-style self-editing core memory works (or wires to native Hermes memory). Every new component
degrades gracefully offline; the all-keys-unset sovereignty test passes. Nothing on the out-of-scope
list was built; the core loop was never modified; `$VLLM_BASE_URL` is the only chat-model host and new
model roles have their own optional env vars; config was backed up before edits. Report any failed
validation honestly with diagnostics.