# CLAUDE_bifurcate_search.md — Deployment-Profile Bifurcation + SOTA Local Deep-Research Search

You are upgrading the completed `hermes-max/` harness (9 MCP servers, compounding stack, sovereign
docs loop, 6 stages committed) with two things, in order:

1. **A clean deployment bifurcation** — one codebase, two profiles: `gpu_local` (the default,
   maximalist, CUDA/embeddings/full-RAG, local-OR-cloud chat endpoint) and `lean_cloud` (CPU-only /
   Mac-mini / VPS, no CUDA assumed, cloud chat endpoint assumed, no heavy embedding compute). The
   bifurcation MUST NOT inhibit any current or future first-class capability on the gpu_local path —
   lean is a graceful *subset*, never a ceiling on full.
2. **A SOTA local deep-research search capability** — free, fully local (SearXNG + Crawl4AI +
   local/cloud model), engineered to approach the *quality* of proprietary deep-research (Perplexity /
   Gemini / OpenAI Deep Research) on the patterns that actually matter, not a naive single-shot search.

Work in STAGES, in order; each independently committed, smoke-tested, validated. Read the whole spec
first. Report after each stage. **Do NOT build drop-in steering-LLM replacement yet** — that is a
later, separate spec; this one precedes it.

## NON-NEGOTIABLE DISCIPLINE (unchanged anti-Frankenstein gate)
Extend via native surfaces only; never modify Hermes's core loop; each capability an independent MCP
server or extension with own venv/health/smoke/OTel and graceful degradation; manifest-driven;
discovery-first (prefer native knobs, report native-vs-built); single `$VLLM_BASE_URL` for the chat
model with new roles getting their own optional env vars; back up config before edits; commit per stage.

## EXISTING STACK (build on; do not duplicate)
9 servers (verify 9101, codebase-rag 9102 w/ BM25+dense+graph+rerank, knowledge-graph 9103 w/
core-memory, observability 9104, escalation 9105 w/ classifier+flywheel, checkpoint 9106, watchdog
9107, search 9108, docs 9109 w/ SearXNG→Crawl4AI→distill→RAG/KG). `mcp-manifest.yaml` is the single
source of truth; `bootstrap.sh` is the one-command installer; local embed/rerank via Qwen3-0.6B on
vLLM ports; Crawl4AI self-hosted (:basic arm64 / :latest x86).

---

## STAGE 0 — DEPLOYMENT PROFILES (the bifurcation)

### 0.1 — Profile model
- Add `DEPLOY_PROFILE` to `.env`/`.env.example`, values `gpu_local` (DEFAULT) | `lean_cloud`.
- `bootstrap.sh` auto-detects and SUGGESTS a profile, but never silently overrides an explicit one:
  detect CUDA (`nvidia-smi` present + working), detect arch (`uname -m`), detect total RAM, detect
  whether `$VLLM_BASE_URL` is localhost vs remote. Heuristic: CUDA + ≥32GB ⇒ suggest gpu_local; no
  CUDA / Apple Silicon / <16GB / remote-only endpoint ⇒ suggest lean_cloud. Print the detection
  table and the chosen/suggested profile; `--profile X` overrides.

### 0.2 — Manifest gains a capability/requirement model
- Extend `mcp-manifest.yaml`: each server gets `profiles: [gpu_local, lean_cloud]` (which profiles
  run it) and `requires: []` / `degrades_to:` notes. `bootstrap.sh`, `start-all.sh`, `healthcheck.sh`,
  `register-mcp.sh` all filter by the active profile. The point: adding a future gpu_local-only
  capability = one manifest line, lean is unaffected, and full is never capped.

### 0.3 — Per-profile capability matrix (implement the swaps; never a hard ceiling on gpu_local)
| Capability | gpu_local (default, maximalist) | lean_cloud (CPU/Mac/VPS) |
|---|---|---|
| Chat model | local vLLM OR cloud endpoint (either) via `$VLLM_BASE_URL` | cloud endpoint via `$VLLM_BASE_URL` (assumed) |
| RAG embeddings | local Qwen3-Embed-0.6B on vLLM (CUDA) | OPTIONAL cloud embed (`EMBED_BASE_URL`→OpenAI/Voyage) OR **BM25+graph only** (no embed compute) |
| Reranker | local Qwen3-Reranker-0.6B (CUDA) | cloud rerank if set, else fused-no-rerank |
| RAG graph (tree-sitter+PageRank) | full | **full** (pure Python, CPU-fine — keep it; this is NOT heavy) |
| Doc extract | Crawl4AI (Playwright/Docker) | Crawl4AI if Docker present, else **trafilatura** (pure-Python, no browser, no Docker) |
| GEPA self-evolution | full (local model) | OPTIONAL (cloud model, rate-limited) — off by default on lean |
| Watchdog/verify/checkpoint/KG | full | **full** (all CPU/pure-Python — no change) |
| DFlash / speculative decode | gpu_local serving concern (out of harness scope) | n/a (cloud endpoint) |
- The honest CPU-RAG path: graph retrieval is tree-sitter + networkx PageRank — pure Python, runs
  fine on a VPS; KEEP it on lean. Only the *embedding/reranker* compute is CUDA-heavy, and lean swaps
  those to an optional cloud embed endpoint or drops to BM25+graph (already proven). So lean still has
  real hybrid-ish retrieval (BM25 + AST graph), just not local dense vectors.

### 0.4 — Two thin profile bootstraps over one engine
- `bootstrap.sh` stays the single engine; add `bootstrap-gpu.sh` and `bootstrap-lean.sh` as ONE-LINE
  wrappers (`DEPLOY_PROFILE=gpu_local bash bootstrap.sh "$@"` / `...lean_cloud...`) so a user picks by
  filename with zero flags, but there is no code duplication. Both invoked via `bash` (no chmod).
- The CUDA/PyTorch/driver reality: NO hermes-max MCP server imports torch (they call vLLM over HTTP) —
  preserve this. The ONLY torch/CUDA touchpoints are the optional embed/rerank vLLM serves, which are
  gpu_local-only and started by `serve-embed.sh`/`serve-rerank.sh`, NOT by the MCP venvs. So a lean
  box never needs CUDA/torch at all. Verify and assert this (grep that no server requirements.txt
  pulls torch).

**Stage-0 DoD:** `bash bootstrap-lean.sh` on a CUDA-less box brings up the CPU/pure-Python servers
(verify, checkpoint, watchdog, KG, RAG-in-BM25+graph mode, search, docs-via-trafilatura) green, with
no torch/CUDA anywhere, chat via cloud `$VLLM_BASE_URL`; `bash bootstrap-gpu.sh` (default) brings the
full stack incl. local embed/rerank; the manifest filters correctly; no gpu_local capability is
removed or capped by the bifurcation. Committed.

---

## STAGE 1 — SOTA LOCAL DEEP-RESEARCH SEARCH
Goal: free/local web research approaching proprietary deep-research *quality*. The research is
explicit that quality comes from a small set of patterns, NOT from a bigger framework — and that a
well-configured agent+SearXNG skill has beaten dedicated frameworks. So build it as ONE new MCP
server + a skill on top of the EXISTING sovereign loop (SearXNG + Crawl4AI/trafilatura + local model
+ RAG/KG), not by importing local-deep-research/LangChain. Reference quality bar: open local deep
research reaches ~72-78% SimpleQA fully local (Qwen3.6-27B-class); the named failure modes to engineer
against are echo-chamber retrieval, SEO/source-quality bias, intermediate-step (planning) hallucination,
and sub-agent overspawning.

### 1.1 — `mcp-research` server (new, e.g. port 9110) — the deep-research loop
Implements the canonical four-stage deep-research architecture as deterministic, bounded tools the
agent (and a skill) drive. Profiles: runs on BOTH (it's orchestration; uses whatever chat endpoint
`$VLLM_BASE_URL` points at, local or cloud). Tools:

- **`plan_research(question)`** → decompose into structured sub-goals + an explicit roadmap (what to
  search, in what order, how findings support synthesis). This is the stage where proprietary systems
  win and where intermediate hallucination is most damaging — so the plan is written to PLAN.md-style
  external state and is itself checkable.
- **`develop_queries(subgoal)`** → generate *diverse, complementary* queries (varied abstraction/
  specificity), NOT near-duplicates — this directly counters echo-chamber retrieval. Dedup queries by
  n-gram similarity before issuing.
- **`explore(queries)`** → iterative web exploration via the existing SearXNG search + Crawl4AI/
  trafilatura extract. Apply: **URL-level + n-gram content dedup** (break echo chambers — don't
  re-ingest the same source across iterations), **authority-aware re-ranking** (prefer primary/official
  sources — docs sites, papers, gov, project repos — over SEO content farms; use SearXNG category
  filters research-paper/pdf/docs and a domain-authority heuristic + the local reranker when available),
  and bounded breadth (cap sources/iteration — NO overspawning; default conservative, configurable).
- **`verify_claims(findings)`** → the differentiator most open systems lack (Marco DeepResearch's
  thesis): cross-check each material claim against ≥2 independent sources (independent = different
  domain, post-dedup); flag single-sourced or conflicting claims rather than asserting them. Reuse the
  harness's verification discipline. Intermediate verification, not just final.
- **`synthesize(verified_findings, question)`** → compile a structured, **citation-backed** report
  (every claim → source URL), distinguishing well-supported / single-sourced / conflicting. Keep
  quotes/code/figures in original form (content compressor, not paraphraser — mirrors web_extract).
- **`deep_research(question)`** → orchestrates plan→develop→explore→verify→synthesize with bounded
  loops (default MAX_RESEARCH_LOOPS ~3-5, configurable), wall-clock budget via mcp-watchdog, and
  writes the final brief + key entities to RAG `docs/` + KG so research compounds (a later run on a
  related topic starts ahead). Single-threaded by default; bounded parallel explore only for
  independent subgoals (avoid overspawning).

### 1.2 — Skill: `workflow-deep-research`
- When the task needs current/external knowledge beyond pretraining+RAG (novel framework, recent
  release, "what's the current best X", cross-referencing multiple sources): drive `deep_research`.
  Tells the agent to verify before asserting, cite every claim, prefer primary sources, and stop at
  the loop/budget cap with an honest "confidence + gaps" note rather than padding. Gate depth on the
  shared `classify_difficulty`/scope signal (a quick lookup ≠ a multi-source synthesis).

### 1.3 — Engineer against the four named failure modes (make these explicit, tested invariants)
- Echo-chamber → query-diversity + URL/n-gram dedup (test: repeated similar queries don't re-ingest
  same URLs).
- Source-quality bias → authority-aware ranking (test: a primary doc outranks an SEO farm for the
  same query).
- Planning hallucination → external checkable plan + intermediate verify (test: a wrong plan step is
  caught by verify_claims, not propagated to synthesis).
- Overspawning → hard source/loop caps (test: a simple query does NOT fan out to dozens of fetches).

### 1.4 — Quality bar & honest evaluation
- Add `scripts/eval-research.sh`: run a small fixed set of factual questions through `deep_research`,
  score citation-correctness + answer-correctness, report. Target the open-local bar (~72-78% on
  simple factual, higher with cloud endpoint). Report honestly — if local-model synthesis is the
  bottleneck vs retrieval, say so (it informs whether to route synthesis to the escalation tier later).

**Stage-1 DoD:** `mcp-research` live on both profiles; `deep_research` runs the full
plan→develop→explore→verify→synthesize loop end-to-end with ZERO external API (SearXNG + Crawl4AI/
trafilatura + `$VLLM_BASE_URL`); the four failure-mode invariants are tested and hold; results compound
into RAG/KG; eval-research reports an honest quality number; degrades gracefully (SearXNG down → tool
unavailable; Crawl4AI down → trafilatura; reranker absent → authority-heuristic-only ranking). Committed.

---

## CROSS-CUTTING
- OTel spans: research_planned, queries_developed, sources_explored, claims_verified,
  report_synthesized, echo_chamber_blocked, low_authority_filtered.
- Manifest: mcp-research (9110) is one line, `profiles: [gpu_local, lean_cloud]`.
- Graceful-degradation matrix updated for the new server and the trafilatura fallback path.
- README: document the two bootstraps (gpu default / lean), the capability matrix, and that the deep-
  research loop is fully sovereign. Re-run the all-keys-unset sovereignty test to include research.

## OUT OF SCOPE (this spec)
- Drop-in steering-LLM replacement / interleaved reasoning escalation (Cerebras/Gemini-burst) — NEXT
  spec, explicitly deferred. Do not build now.
- No importing local-deep-research / LangChain / a parallel agent framework — build the patterns as
  native MCP tools + skill (the research shows configurable-native beats pre-packaged here).
- No multi-agent overspawning; no cloud-required path on either profile (lean uses cloud CHAT by
  assumption, but search/extract/synthesis stay local-capable).
- No core-loop modification; no gpu_local capability removed or capped to serve lean.

## REPORT (per stage)
What landed as {config / new server / extended server / skill / script}; native-vs-built; smoke +
validation PASS/FAIL per assertion (failures are signal); the four failure-mode invariant tests;
graceful-degradation result; git commit SHA.

## DEFINITION OF DONE
Two profiles work from one codebase: `bootstrap-gpu.sh` (default, maximalist, CUDA/local-embed/full)
and `bootstrap-lean.sh` (CPU/Mac/VPS, no torch/CUDA anywhere, cloud chat, BM25+graph RAG, trafilatura
extract) — and the bifurcation provably caps NO gpu_local capability now or in future (manifest-gated,
torch isolated to optional gpu-only serve scripts). A SOTA-pattern local deep-research capability
(`mcp-research`: plan→develop→explore→verify→synthesize, with query-diversity, URL/n-gram dedup,
authority-aware ranking, intermediate claim-verification, bounded loops, citation-backed synthesis)
runs fully sovereign on both profiles, compounds into RAG/KG, and passes its four engineered
failure-mode invariants and an honest eval. Steering-LLM work is left for the next spec. Nothing
out-of-scope was built; core loop untouched; sovereignty test (all external keys unset) passes
including research; config backed up; each stage committed.