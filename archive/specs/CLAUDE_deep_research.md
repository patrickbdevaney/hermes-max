# CLAUDE_deep_research.md — Directive: World-Class Deep Research Upgrade

**For:** Claude Code, working in `~/hermes-max/` on branch `inference-fabric`.
**Scope:** Upgrade the existing `mcp-research` (9110) deep-research engine in place.
This is an UPGRADE AGAINST THE BUILT SYSTEM, not a rewrite. Do not import a
framework. Do not delete the deterministic fallbacks. Every change preserves the
sovereign / deterministic-first / public-repo-clean invariants.

**Source of truth:** the optimization report (compass_artifact_wf-c44f7f7e) and the
search-subsystem MD. The files you are editing already exist:
`mcp-research/{research_core,sources,banyan,rank,relevance,verify_gate,corpus,
session_state,extract,server}.py` and `mcp-search/search_core.py`.

---

## 0. The thesis you are implementing (read first)

hermes-max's cost structure is INVERTED vs Perplexity/Gemini/OpenAI: their retrieval
is paid (Tavily/Brave/Exa per query) so they cap breadth; ours is FREE (SearXNG +
Crawl4AI). Our marginal cost is cheap parallel inference. Therefore the dominant
design is: **retrieve far wider than they can afford, filter hard with a cheap→
expensive cascade, verify every claim (they don't), synthesize once with the
frontier model, and compound into the corpus.** The intelligence is in the PIPELINE
STRUCTURE, not any single call.

Two corrections from the research that constrain the build:
1. **Groq free-tier limits are per-ORGANIZATION, not per-key** (30 RPM / 6000 TPM /
   ~14,400 RPD). Stacking Groq keys on one org does NOT multiply throughput. The
   key-pool must be MULTI-PROVIDER (Groq + Cerebras + local Qwen), not multi-key-one-org.
2. **The real wall-clock bottleneck is scrape latency + SearXNG/Crawl4AI throughput,
   NOT inference.** Tune backend concurrency from measured p95, not guesses.

**Phase order is deliberate: quality first, then breadth.** Iterative coverage
(Phase 1) closes the largest quality gap and is mostly orchestration. Verification
(Phase 2) is the differentiator and is largely built. Wide parallel fan-out (Phase
3–4) is the biggest engineering lift and only pays off once coverage + filtering
exist to exploit it. Do them in order. Each phase must build clean, keep all smoke
tests green, and preserve the no-LLM deterministic path.

---

## PHASE 1 — In-request iterative coverage loop (the biggest quality win)

**Goal:** convert `deep_research`'s mostly-upfront, blunt `max_loops≤3` into a
QUALITY-GATED iterative loop: plan → wave → reflect-on-gaps → targeted wave →
stop-when-covered-or-saturated. This is how Gemini/OpenAI/search-r1 actually get
depth.

### 1.1 Gap-reflection step (`research_core.py`, new `reflect_gaps()`)
After each wave's explore+extract, call a CHEAP model with: the sub-goals, the
retained spans so far, and the per-subgoal coverage state. It returns STRICT JSON:
```json
{"uncovered_subgoals": ["..."], "unresolved_contradictions": ["..."],
 "followup_queries": ["targeted query for the gap", "..."]}
```
- Deterministic fallback (no LLM): a sub-goal is "uncovered" if it has <2 retained
  chunks from independent domains; followup_queries = the uncovered sub-goal text +
  its angle variants (reuse `develop_queries`' fallback).
- This is STORM expert-questioning + Self-Ask follow-up fused. Keep it cheap and
  short-context — it's the canonical "mildly intelligent rote call."

### 1.2 In-request coverage + saturation model (`research_core.py`, reuse `banyan.py`)
Maintain a per-run sub-goal × evidence matrix in the loop. After each wave:
- **Coverage** = fraction of sub-goals with ≥2 independent supporting domains
  (reuse the verify_gate independence definition — `_domain`, one-vote-per-domain).
- **Saturation**, porting Banyan's intra-session signals to intra-request:
  - marginal-gain: new unique retained chunks this wave < 15% of prior total → saturating;
  - drift: newly retained chunk embeddings ≥0.92–0.95 cosine to the run's evidence
    centroid (reuse `rank._embed`/`rank._cosine`) → redundant.
- Expose these as otel spans (`research_coverage`, `research_saturation`) so the UI
  swimlane can render wave-by-wave progress.

### 1.3 The loop (replace the body of `deep_research`'s `for loop`)
```
plan → Wave 1 (develop_queries → explore → extract → retain)
while waves < MAX_WAVES and not stop:
    coverage, saturation = assess()
    stop = (coverage >= 1.0) or saturation or wall_budget_exceeded
    if stop: break
    gaps = reflect_gaps()
    if not gaps.followup_queries: break
    Wave N+1: explore ONLY gaps.followup_queries (targeted)
synthesize once → verify → cite-audit → compound
```
- New bound: `RESEARCH_MAX_WAVES` (default **3**, env-tunable). Most runs stop at
  1–2; only genuine gaps reach 3. This REPLACES the blunt `max_loops` semantics
  (keep the env var name for compat but make it wave-gated, not pass-counted).
- Raise `WALL_BUDGET_S` default 600→**900** (the loop is now scrape-bound).

### 1.4 develop_queries upgrade — perspective + abstraction (Section 2 of report)
- Add STORM-style PERSPECTIVE conditioning: for a sub-goal, mine 2–4 personas
  (e.g. implementer / security-auditor / economist / end-user) and generate one
  query per perspective. Increases DIVERSITY OF SOURCES, not just query strings.
- Vary ABSTRACTION deliberately: emit queries at three altitudes — landscape
  ("X overview 2026"), mechanism ("how does X handle Y"), frontier ("X failure
  modes", "X vs Z benchmark"). Keep the 2-shingle Jaccard ≥0.8 dedup.
- Deterministic fallback unchanged.

**Phase 1 DoD:** on a fixed 10–question eval set, ≥80% reach full sub-goal coverage
within 2 waves; Wave 2 demonstrably adds verified evidence on under-covered
questions; smoke tests green; no-LLM path still produces a cited report.

---

## PHASE 2 — Verification-faithful hierarchical synthesis (the differentiator)

**Goal:** make synthesis a hierarchical map→reduce where the verify_gate runs INTO
synthesis, giving a citation-correctness property no commercial service has
(audits find 3–13% of their citation URLs hallucinated, up to 57% post-rationalized).

### 2.1 Hierarchical map→reduce synthesis (`research_core.py` `synthesize`)
- **MAP (cheap, parallel, local/Groq):** per sub-goal, summarize its retained+
  verified spans into a dense, citation-TAGGED "evidence brief" (leaf summary).
  Structure each brief as `claim → [supporting chunk_ids] → verdict_label`. CARRY
  CHUNK-IDS THROUGH (hierarchical merge can amplify hallucination if context is
  dropped — chunk-ids let reduce + verify always resolve to source).
- **REDUCE (the ONE frontier call):** the long-context conductor/frontier role takes
  the handful of briefs (not the thousands of raw spans) + PLAN + verification
  verdicts and writes the report. **This is the model-tier boundary: everything
  before reduce is cheap/local; reduce is the frontier long-context call.** Route it
  through the conductor steer/escalation tier (`ESCALATION_MCP_URL`,
  `conductor_steer`), local frontier fallback, then deterministic cited-bullet
  fallback. Keep all three rungs.
- Reduce input stays small (~10–20K tokens of briefs) — high intelligence, low
  token cost. Do NOT feed raw pages to the frontier model.

### 2.2 Verify gate INTO synthesis (`verify_gate.py`, `research_core.py`)
- Before reduce, decompose the draft findings into atomic claims (RARR / FActScore
  lineage) and run `verify_findings`: each claim → resolve sources to stored chunks
  → entail against the chunk → require ≥2 INDEPENDENT domains → label well-supported
  / single-sourced / conflicting; contradictions surfaced with BOTH citations,
  never averaged (already built — wire it in-line, not as a post-pass).
- **Batch the entailment calls** (multiple claim-source pairs per cheap call) — a
  ~50-claim report × 2 sources ≈ 100 entailment calls, seconds across the pool,
  ≈$0. This is the killer app of cheap parallel inference: the cost that makes THEM
  skip verification makes it nearly free for US.
- The reduce prompt receives the verdict labels so the frontier model hedges
  correctly (writes "well-supported" claims plainly, flags single-sourced, presents
  conflicts with both sides).

**Phase 2 DoD:** citation-faithfulness (fraction of report sentences whose cited
chunk ENTAILS the sentence) ≥95% on the eval set; unsupported claims are flagged not
asserted; contradictions surfaced with both citations; deterministic fallback intact.

---

## PHASE 3 — Async multi-provider key-pool + cheap→expensive relevance cascade

**Goal:** the infrastructure that lets Phases 1–2 go wide. Public-repo-safe.

### 3.1 Multi-provider key-pool (new `mcp-research/pool.py`, env-only)
Model it on the job_agent.py Groq/Cerebras rotation. NO keys committed.
- Read keys from env: `GROQ_API_KEYS=k1,k2,...`, `CEREBRAS_API_KEYS=...`, optional
  others. Local Qwen (`VLLM_BASE_URL`) is always a pool member.
- Rate-limit-aware scheduler: track per-key `x-ratelimit-remaining-requests/-tokens`
  headers (Groq returns them), round-robin, skip near-limit keys, exponential
  backoff on 429.
- **Multi-PROVIDER parallelism** (the correction): real concurrency = sum of safe
  per-provider concurrency across Groq + Cerebras + local, NOT N Groq keys on one
  org. Treat each provider as one rate-limited lane; keys within a provider share
  the org limit.
- **Degradation ladder (preserve all three):** no keys → local Qwen, sequential
  (keyless forker gets a working slower system); one provider → single-lane rate-
  limited; multi-provider → wide parallel. This keeps the repo forkable + sovereign.
- Expose `pool.map_cheap(prompts) -> results` (async, bounded, ordered) as the one
  entry point all cheap fan-out steps call (develop_queries waves, per-chunk
  relevance/extract, entailment batches, leaf summaries).

### 3.2 Relevance cascade (`rank.py`, `relevance.py`, `research_core.explore`)
Replace "fetch then LLM-extract each" with a funnel that spends progressively more
on progressively fewer items:
1. **Coarse, near-free (≈480 → ≈150):** SearXNG/BM25 + RRF (built) + **embedding
   cosine pre-filter** — embed every scraped lead chunk (`EMBED_BASE_URL`), cosine
   vs query + HyDE doc, drop <~0.3–0.4. Embed BEFORE any LLM sees the chunk. Apply
   authority(0–3) + relevance-floor as a multiplier, not a hard gate.
2. **Mid, cheap (≈150 → ≈40):** **cross-encoder rerank** (`RERANK_BASE_URL`) in
   batches ≤100 (latency knee); common default "rerank top ~100, keep top ~40".
   PIN the reranker model version; add a 20–50-pair regression test (silent rerank
   regressions degrade output with no error).
3. **Fine, per-chunk LLM (only the ≈40 that matter):** cheap pool relevance-judge +
   span-extract, batched. This is the "intelligent filter" stage — never use it as
   the primary filter (the expensive mistake); it's the last, narrowest rung.

**Phase 3 DoD:** a run consults 300–500 candidates, retains 20–40, at ≈$0 and bounded
latency; keyless mode still works sequentially; rerank regression test present.

---

## PHASE 4 — Wide fan-out + backend tuning (exploit the free retrieval)

**Goal:** raise the bounds now that coverage-gating + filtering can exploit breadth.
The anti-overspawn guard is redefined: overspawning is no longer "too many cheap
calls" — it's "melting SearXNG/Crawl4AI" or "synthesis input past the join model's
context." Maximize sources subject to backend throughput + diminishing returns.

### 4.1 New bounds (env-tunable; replace ≤8 sources / sequential)
- Candidate URLs/run: **300–500** soft cap (backend-limited).
- Retained-after-filter feeding synthesis: **20–40** (quality-gated, not count-gated).
- Concurrent scrapes: **10–16** (tune from measured p95 latency + memory).
- Inference concurrency: sum of safe per-provider lanes (≈12–24 in-flight cheap calls).
- Three independent bounded pools: sub-query (async over key-pool), scrape worker
  (bounded vs Crawl4AI), relevance/extract (async over key-pool).

### 4.2 Backend hardening (`extract.py`, mcp-docs substrate, `sources.py`)
- Crawl4AI: use its `MemoryAdaptiveDispatcher`/`RateLimiter` (base_delay 0.5–1.0s,
  exp backoff), `memory_threshold_percent` ~90, per-domain politeness. Sweet spot
  ~10–16 concurrent headless pages on the Thor — MEASURE, don't assume.
- SearXNG: spread queries to avoid Google/Bing CAPTCHA storms; lean on the 70+
  engine pool; add jitter between bursts.
- Plot the diminishing-returns curve (new retained evidence per added source);
  confirm the ~30–50 retained-source saturation knee and set the soft cap there.

### 4.3 Scraper acceleration (the open question — see Appendix A)
Implement extraction as a TIERED ladder by page type, adding a fast HTTP-first rung
IN FRONT of Crawl4AI for the ~80% of pages that don't need JS:
- **Tier A — fast static (NEW):** an HTTP-first fetch (httpx/static) → trafilatura,
  OR an external fast scraper (Appendix A). Milliseconds, no browser. Try first.
- **Tier B — JS render:** Crawl4AI (Chromium) only when Tier A yields empty/thin
  content or the host is known-JS (`_JS_HOSTS`). This is where the latency lives;
  reserve the browser for pages that actually need it.
- **Tier C — hosted fallback:** Jina reader for blocked/complex/PDF.
The existing `extract.py` ladder already has this SHAPE — make Tier A genuinely
HTTP-first/browserless and route by a cheap pre-check (does a static fetch return
enough text?) so Chromium is the exception, not the default. **This single change is
likely the biggest wall-clock win** because it removes the browser from the hot path
for most pages.

**Phase 4 DoD:** stable at 300–500 candidates (no CAPTCHA storms, Crawl4AI memory
stable); measured p95 scrape latency documented; browserless Tier A handles the
majority of pages; diminishing-returns knee identified.

---

## PHASE 5 — Novel capabilities (exceed, don't just match)

Only after 1–4 are green and benchmarked. Each is cheap for US, unaffordable for them.
- **Adversarial/disconfirming wave:** a fan-out wave whose job is to FALSIFY each
  tentative claim (query "X debunked / criticism / counter-evidence"). Turns
  conflict-surfacing into active red-teaming. Measure: claims revised/retracted after.
- **Cross-run contradiction detection:** when a new verified claim contradicts a
  prior corpus claim, surface it via the KG edges (`kg_provenance.py` already has
  cites/supersedes/contradicts). Self-correcting across time — no stateless service
  can do this.
- **Per-claim temporal provenance:** verify_gate + KG temporal edges → claims
  annotated "true as of <date>, superseded by <source>." A living artifact.
- **Ensemble-of-decompositions:** run 2–3 different decomposition strategies
  (plan-and-execute / STORM-perspective / citation-graph-seeded) in parallel,
  RRF-fuse retained evidence (extends mcp-search's "first green wins" into research).

---

## Cross-cutting requirements (all phases)
- **Sovereign/deterministic-first:** every new inference step degrades to a non-LLM
  fallback. No hard dependency on any one provider.
- **Public-repo clean:** zero keys committed; pool reads env only; keyless degraded
  mode must work.
- **Observability:** emit otel spans for waves, coverage, saturation, candidates,
  retained, entailment pass-rate, per-claim verdicts, $ (≈0), latency — the UI
  swimlane + cost meter should render the new pipeline automatically.
- **Smoke tests green after every phase**; add new smoke tests for the wave loop,
  the cascade, the pool degradation ladder, and the rerank regression suite.
- Commit per phase with a clear message; do not SHA-spam.

## The metric suite (run every phase vs Perplexity + Gemini on identical questions)
coverage (sub-goals satisfied) · citation-faithfulness (entailment pass rate, target
≥95%) · sources consulted (candidates + retained) · latency · $/run (target ≈0) ·
blind LLM-judge quality score. The bet: match/beat on coverage + breadth, DECISIVELY
win on citation-faithfulness + cost.

---

## Appendix A — Faster scrape/render: evaluate replacing/augmenting Crawl4AI

The research found Crawl4AI is the wall-clock bottleneck (Python/asyncio + Chromium;
independent benchmarks rate it slowest of the major scrapers on raw throughput,
~2GB Docker bundling Chromium). The right move is NOT a wholesale replacement — it's
a **fast HTTP-first tier in front of it** (Phase 4.3), because the static/JS split is
the real lever: HTTP-first tools are 5–50× faster but can't render SPAs; Crawl4AI
renders SPAs but pays browser cost on every page.

Candidates to evaluate for Tier A (fast, self-hostable, free, no paid API):

**Rust, HTTP-first, single static binary, Firecrawl-compatible REST + MCP server**
(the strongest fit — drop-in `/v1/scrape` `/v1/crawl` `/v1/search`, ~6 MB RAM,
~833ms/1k-URL benchmarks, native markdown output, built-in MCP server so mcp-docs
could call it as a tool). These exist as of 2026 (search GitHub topics
`web-scraper?l=rust` for the Firecrawl-alternative single-binary scrapers; one
advertises 2.3× faster than Tavily, 1.5× faster than Firecrawl on 1K-URL). **License
caveat: several are AGPL-3.0** — for a self-hosted internal tool called over localhost
this is generally fine, but VERIFY the license is compatible with hermes-max's
intended license before vendoring; prefer MIT/Apache variants where they exist, or
keep it as an optional external service (not vendored) to sidestep copyleft.

**Go, HTTP-first:** Colly (Apache-2.0, thousands of req/s on modest hardware, no
browser/JS) and Katana (MIT, fast URL/endpoint discovery). Both are HTTP-only — pair
with a Go html-to-markdown step. Good if you prefer Go and want a permissive license;
more integration work than the Rust Firecrawl-compatible binaries (no native markdown
/ MCP).

**Decision guidance for Claude Code:**
1. Keep trafilatura as the pure-Python browserless Tier A default (zero new dep,
   already in the ladder) — make sure it's tried FIRST with a real static fetch.
2. Evaluate ONE Rust Firecrawl-compatible single-binary scraper as an OPTIONAL
   external service behind a `FAST_SCRAPER_URL` env var: if set, Tier A routes to it
   (markdown + speed + MCP); if unset, falls to trafilatura. This preserves
   sovereignty + keyless-fork degradation and sidesteps AGPL vendoring (it's a
   separate process the operator opts into, like SearXNG/Crawl4AI already are).
3. Crawl4AI stays as Tier B (JS render) — unchanged, just no longer the default for
   static pages.
4. Benchmark Tier-A-vs-Crawl4AI p95 latency on a representative URL mix on the Thor
   before committing; report the numbers. Do NOT rip out Crawl4AI — it's the JS
   renderer of record.

Net: a browserless Tier A (trafilatura now, optional Rust fast-scraper service later)
removes Chromium from the hot path for ~80% of pages — the biggest single latency win
— while Crawl4AI remains for the SPAs that genuinely need rendering. Stay sovereign,
stay keyless-forkable, measure before swapping.