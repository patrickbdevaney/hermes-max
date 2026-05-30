# CLAUDE_research_engine.md — Sovereign Deep-Research Engine + Banyan Content-Evolution (on hermes-max)

You are extending the existing `hermes-max` harness with (A) a multi-source sovereign deep-research
engine and (B) the realistically-implementable Banyan exploration concepts. Both extend the existing
`mcp-research` and `mcp-knowledge-graph` servers as native MCP tools. Work in STAGES, in order; each
independently committed, smoke-tested, validated. Read the whole spec first. Report after each stage.

## THE HARD LINE: EVOLVE CONTENT, NEVER MACHINERY
The system may, over long unattended horizons, autonomously evolve its CONTENT — the skill library,
the RAG corpus, the knowledge graph, and which research directions it explores. It must NEVER
autonomously modify its MACHINERY — the MCP servers themselves, the Hermes core, the router, or its own
tool code. That kind of self-modification stays human-gated and out of scope here. Concretely:
- **Allowed unattended:** write/refine skills at runtime, ingest research into RAG/KG, build the
  knowledge corpus, run Banyan explore-exploit over research namespaces, generate standing research
  tasks, flag saturated branches, surface findings to the operator.
- **Forbidden unattended:** editing any `mcp-*` server code, the Hermes loop, the conductor router,
  this spec's own tools, or any `.py`/config that defines the machinery. Any such change requires an
  explicit human-run Claude Code session, never the autonomous loop.
This is the safe half of the Banyan vision: short-term deterministic Claude-Code-like execution +
long-term autonomous evolution of the knowledge/skill content, with the code itself frozen unless a
human changes it.

## NON-NEGOTIABLE DISCIPLINE (anti-Frankenstein, unchanged)
Extend via native MCP surfaces only; never modify Hermes's core loop; each capability is a tool the
driver MAY call; absent keys/sources → tool no-ops or degrades; single `$VLLM_BASE_URL` for local
untouched; back up config before edits; commit per stage; graceful degradation tested for every piece.

## EXISTING STACK (reuse, do not rebuild)
mcp-research (SearXNG→Crawl4AI→distill→RAG/KG, engineered vs echo-chamber/SEO-bias/planning-
hallucination/overspawning), mcp-knowledge-graph (Graphiti/Neo4j core-memory), codebase-rag
(Qdrant + graph/rerank), mcp-verify, mcp-escalation (conductor: DeepInfra DeepSeek synth/steer,
presence-gated), watchdog, observability (Langfuse). The current research sources are arXiv + GitHub-
trending + HN + Reddit. This spec adds source breadth, on-disk corpus, provenance, and Banyan
exploration.

---

## STAGE 1 — SOURCE FAN-OUT (the biggest quality jump; all free, mostly no-key)
Add native MCP source adapters to mcp-research. VERIFIED access (May 2026; treat limits as volatile):
- **`arxiv_search`** — already exists; EXTEND to remove the hard 90-day filter (make `days_back`
  optional) so seminal work is reachable, and add category targeting (cs.CR, cs.LG, cs.DC, cs.AI).
  HTTP Atom API, no key, ~1 req/3s.
- **`semantic_scholar`** — NEW, highest-value add. REST/JSON, **no key needed** (unauth shared pool
  5,000 req/5min; optional free key → 1 RPS dedicated). Implement: paper relevance search (bulk
  endpoint), paper details (batch endpoint), and CITATION-GRAPH traversal (`/paper/{id}/references`
  backward → seminal, `/paper/{id}/citations` forward → latest). Keep `fields` minimal. This citation
  graph is the killer feature — it's what turns "search" into "find the canonical + frontier of a
  topic." Attribution string required when displayed.
- **`github_search`** — NEW (distinct from existing github_trending). REST/GraphQL search over code,
  issues, discussions, repos. Use a **free PAT** (env `GITHUB_TOKEN`) to get 30 req/min search /
  ~9 req/min code-search instead of 10/min unauth. Full file contents retrievable. This reaches the
  specific code/issue/discussion that answers a question, not just what's trending.
- **`hn_search`** — already exists (Algolia, no key); keep.
- **`stackexchange_search`** — NEW, optional. REST/JSON, 300 req/day no-key (10k with free key).
  Q&A with votes/tags. Medium value; add if cheap.
- All adapters: errors returned as strings (never raise), presence-gated (skip if a required token
  absent), and the engine ALWAYS degrades to the existing SearXNG web layer if a structured source is
  down. No source is load-bearing.
- **RRF fusion (free, do this):** when multiple adapters/queries return ranked lists, merge with
  Reciprocal Rank Fusion (Σ 1/(k+rank), k≈60). Pure arithmetic, no model, biggest robustness-per-effort
  win. Rewards docs ranking consistently across sources.
- **Lightweight classifier-router:** a cheap local-model (or keyword/embedding heuristic) maps a query
  to a source set: crypto/protocol → arXiv cs.CR + github_search + ethresearch + eip + SearXNG; applied
  ML → arXiv cs.LG + semantic_scholar + github_search + HN; library-how-to → github_search + SearXNG +
  HN + stackexchange. Emits source-set + per-source budget (bounded). Always include SearXNG as catch-all.

**Stage-1 DoD:** semantic_scholar (incl. citation-graph traversal), github_search (PAT-authed), extended
arxiv, and the classifier-router + RRF fusion all work; a crypto query routes to the crypto source set;
each adapter degrades gracefully (kill the PAT → github_search no-ops, web layer still answers); errors
are strings not exceptions. Committed.

## STAGE 2 — CRYPTO/STANDARDS ADAPTERS (where this beats consumer tools for your domain)
- **`ethresearch`** — NEW. ethresear.ch is Discourse; public read needs NO auth — append `.json` to
  topic/category URLs (`/latest.json`, `/t/{slug}/{id}.json`, `/c/{category}.json`). Pull topics,
  posts, authors, dates, full post text. This is the Ethereum-research frontier consumer tools miss.
- **`eip_erc`** — NEW. Read ethereum/EIPs + ethereum/ERCs GitHub repos (markdown + front-matter) via
  the github_search adapter's auth. Full spec text. High value for crypto.
- **`ietf_rfc`** — NEW, optional. IETF Datatracker REST/JSON + RFC-Editor; no key; full RFC/draft text.
  Add if protocol-standards work is frequent; defer otherwise.
- Same discipline: string errors, presence/availability-gated, degrade to web.

**Stage-2 DoD:** ethresearch pulls real topics+posts as text (no auth); eip_erc reads a named EIP's full
text; sources route correctly from the classifier; all degrade cleanly. Committed.

## STAGE 3 — ON-DISK HUMAN-READABLE CORPUS + PROVENANCE (the "save as markdown" you wanted)
The corpus currently truncates to 10K chars in Qdrant and isn't human-browsable. Fix both. (NOTE: keep
EXECUTION TRACES in Langfuse — do NOT dump traces to disk; only the knowledge corpus goes to markdown.)
- **Markdown corpus on disk:** when research is ingested, ALSO write the FULL (untruncated) extracted
  content to `corpus/{namespace}/{source_type}/{slug}.md` with YAML front-matter (`source_url`, `title`,
  `authors`, `date`, `retrieval_query`, `source_type`, `citation_count`, `ingested_at`, `session_id`).
  This makes the corpus greppable, git-versionable, human-readable, and sovereign on-disk — independent
  of Qdrant. Idempotent (re-ingest overwrites the same slug).
- **Distillation re-architecture:** STOP local-model distill-on-ingest (it compresses away the technical
  nuance frontier work needs). Instead: store full text on disk + chunk + embed; distill LAZILY at query
  time over only the retrieved chunks. Route distillation by density: local Qwen for bulk; OPTIONAL
  cheap-cloud (DeepSeek via the existing conductor) for dense technical sources (papers, audit reports,
  specs) — behind a feature flag so the engine stays fully sovereign/offline if disabled. Cents per
  session at most.
- **RAG payload enrichment:** store full chunks (not 10K-truncated) with payload `source_type`, `url`,
  `authors`, `date`, `citation_count`, `authority_score`, `retrieval_query`, `extraction_method`,
  `session_id` — so the verify gate can resolve any claim → its backing chunk.
- **Embedding:** Qwen3-Embedding local (0.6B/4B on your inference host for throughput; 8B for offline batch
  re-embedding). Pair with the Qwen3 reranker final pass. Already partly present — ensure it embeds the
  full chunks, not truncated.

**Stage-3 DoD:** ingesting a long arXiv paper writes a full untruncated `.md` with front-matter to disk
AND full chunks to Qdrant; lazy query-time distillation works; the cloud-distill flag toggles cleanly
(off = fully local); a stored chunk is resolvable from its RAG payload. Committed.

## STAGE 4 — EXTRACTION LADDER + DEDUP/AUTHORITY/CITATION-GRAPH
- **Extraction ladder (graceful degradation):** Trafilatura first (free, CPU, ms-fast, great on static
  articles) → Crawl4AI for JS-rendered pages (existing) → Jina Reader (r.jina.ai, rate-limited free) as
  fallback for blocked/complex pages + PDFs. Pick by page type, fall through on failure.
- **Semantic dedup (not URL/n-gram):** embed chunks, collapse near-duplicates above a cosine threshold
  so paraphrased SEO mirror content doesn't dominate; keep the most authoritative instance.
- **Authority ranking:** boost primary domains (arxiv, rfc, eip, official repos, audit reports), demote
  content farms; add citation-count (semantic_scholar) + recency signals (weight recency for fast-moving
  fields but anchor to seminal).
- **Citation-graph → KG:** the semantic_scholar references/citations traversal from Stage 1 becomes KG
  edges in Stage 5.

**Stage-4 DoD:** the extraction ladder falls through Trafilatura→Crawl4AI→Jina on appropriate pages;
semantic dedup collapses a deliberately-duplicated source; authority ranking surfaces an arXiv primary
over a blog summary of it. Committed.

## STAGE 5 — KG PROVENANCE + VERIFICATION GATE (grounding, the most important reliability layer)
- **Graphiti ingestion with provenance:** research outputs become episodes; entities = papers/repos/
  protocols/EIPs/people/techniques; edges = `cites`/`supersedes`/`implements`/`audits`/`contradicts`/
  `authored_by` (the citation graph maps directly). Each fact edge carries its source ID. Graphiti's
  temporal validity (`valid_from`/`valid_until`) matters for fast-moving fields — a 2024 claim may be
  superseded; mark it rather than silently keeping both.
- **Verification = decomposed retrieval (not generation):** every synthesized research claim must carry
  a source ID resolvable to a stored chunk; a cheap entailment pass (local or DeepSeek) checks each
  claim is entailed by its cited chunk; CONTRADICTIONS across sources are surfaced explicitly with both
  citations (critical when the agent uses research to make an architecture decision) — never averaged.
- **Query-diversity (echo-chamber fix):** use the conductor's cheap-cloud (or local) for sub-question
  decomposition + diverse query angles; per-source syntax translation (arXiv field syntax ≠ GitHub
  qualifiers ≠ web); 3-5 paraphrases per sub-question fused via RRF; optional HyDE for dense academic
  retrieval. Sub-question decomposition is the step that most benefits from the stronger model.

**Stage-5 DoD:** a research run produces claims each carrying a resolvable source ID; the entailment
pass flags an unsupported claim; a contradiction between two sources is surfaced with both citations;
findings land in Graphiti as episodes+entities+edges with provenance and temporal validity. Committed.

## STAGE 6 — BANYAN CONTENT-EVOLUTION (the realistically-implementable autonomy, CONTENT only)
Port the safe, valuable concepts from the Banyan repo as native tools over the EXISTING namespaces +
KG + RAG — strictly content evolution, never machinery. This is the long-horizon-autonomy half.
- **`banyan_select`** — UCB1 explore-exploit over research namespaces:
  `U_i = utility*priority + c*sqrt(ln(N)/n_i)`. Selects which research direction to invest the next
  unattended cycle in, balancing exploiting high-utility namespaces vs. exploring undervisited ones.
  Human `priority` weight multiplies the exploitation term so the operator can steer attention.
- **`banyan_update`** — after a research/skill task completes, update the namespace's visit_count,
  running utility estimate (0.8 history / 0.2 new), and marginal-gain history (keep last 20).
- **Saturation detection (two signals, prevents pathological over-investment):**
  (1) embedding-drift — new research embeddings too similar to the namespace's corpus centroid (cosine
  < threshold) ⇒ retreading ground; (2) marginal-gain decline — last 10 gains trending down AND avg
  below threshold ⇒ diminishing returns. On saturation: set a flag, STOP investing that branch, and
  SURFACE TO THE OPERATOR (Telegram/log) for review of next direction — do NOT silently churn.
- **Standing-task generation:** when a namespace's research queue empties, generate standing research
  tasks (e.g., "what's new in {namespace} since {last_ingest}") so unattended cycles never idle — but
  these are RESEARCH/SKILL tasks (content), never machinery edits.
- **Directive interrupt (the operator-in-the-loop seam):** at the top of each unattended cycle, check
  for a pending human directive; if present, it preempts UCB1 selection (human priority steer). Absent
  a directive, the loop self-directs via Banyan. This is the dual mode: supervised-steer OR unattended-
  explore, same machinery.
- **Skill evolution at runtime (allowed):** the agent may write/refine SKILLS (markdown/skill files in
  the skill library) based on what it learns — this is content. It may NOT edit the MCP/tool code that
  RUNS skills. Gate even skill-evolution behind the existing maturity check
  (`SELF_IMPROVEMENT_ENABLED=false` until 200+ tasks / 30+ days / 50+ skills) — keep that discipline.

**Stage-6 DoD:** banyan_select picks a namespace by UCB1 (verified: an underexplored namespace gets
visited despite lower utility); banyan_update adjusts utility + gain history; saturation flags + surfaces
to the operator on a deliberately-saturated branch (similar embeddings); a directive interrupt preempts
selection; standing tasks generate on empty queue; ALL changes are to content (namespaces/RAG/KG/skills)
— a test asserts NO machinery file is written by the loop. Committed.

---

## CROSS-CUTTING
- OTel/Langfuse spans: source_routed, source_fanout (n sources, n results), rrf_fused, corpus_written
  (disk path), distill_local/cloud, dedup_collapsed, authority_ranked, kg_episode_added,
  claim_verified/unsupported, contradiction_surfaced, banyan_selected (namespace + UCB1 score),
  saturation_flagged, directive_interrupt. Traces stay in Langfuse — NOT dumped to disk.
- **.env additions (all optional, no-key-needed sources work without):**
  `# GITHUB_TOKEN=` (free PAT → lifts github_search to 30 req/min; absent = unauth 10/min or skip)
  `# SEMANTIC_SCHOLAR_API_KEY=` (optional; absent = 5,000 req/5min shared pool, fine for agent volume)
  `# STACKEXCHANGE_KEY=` (optional; absent = 300 req/day)
  `# JINA_API_KEY=` (optional; absent = rate-limited free Jina Reader or skip to Crawl4AI)
  `# RESEARCH_CLOUD_DISTILL=false` (flag; true routes dense-source distill to DeepSeek via conductor)
  No new REQUIRED keys — arXiv, Semantic Scholar (unauth), HN, ethresearch, EIP-read all work keyless.
- README: document the source inventory + volatility (Bing API retired Aug 2025, Brave free tier
  removed Feb 2026, OpenAlex now key-gated Feb 2026, S2/limits change without notice — hence
  degrade-to-web-only is the hedge); the CONTENT-evolves / MACHINERY-frozen line; the on-disk markdown
  corpus location; that traces live in Langfuse.

## OUT OF SCOPE
- Any unattended modification of MCP servers, Hermes core, the router, tool code, or this spec's tools
  (machinery is human-gated only).
- Dumping execution traces to disk (Langfuse owns traces).
- OpenAlex (now key+credit-metered — add only if Semantic Scholar coverage gaps appear).
- Reddit expansion (ToS risk; HN + Stack Exchange preferred).
- Paid web APIs (Brave/Tavily/Exa) as core (optional hedge only).
- Self-hosted Zoekt code index (only if github_search 9 req/min becomes a real bottleneck).
- RL-trained bespoke research models; long-horizon autonomous browser chains (high cost, marginal gain
  given direct structured-source APIs).
- Prose-polish / multi-format report generation (this engine informs a coding agent, not a human reader).

## REPORT (per stage)
What landed as {adapter / router / corpus-writer / kg-tool / banyan-tool}; native-vs-built; smoke +
validation PASS/FAIL per assertion (failures are signal); the degrade-to-web tests; the
NO-machinery-write assertion for Stage 6; airtight notes on which sources need keys vs none; git SHA.

## DEFINITION OF DONE
mcp-research gains multi-source fan-out (semantic_scholar + citation graph, github_search, extended
arXiv, ethresearch, eip_erc, optional stackexchange/ietf) with a classifier-router + RRF fusion, an
extraction ladder, semantic dedup + authority ranking, an on-disk human-readable markdown corpus with
front-matter provenance, lazy hybrid distillation (local default, optional cheap-cloud flag), and a
decomposed verification gate; mcp-knowledge-graph gains citation-graph episodes/entities/edges with
provenance + temporal validity; and a Banyan content-evolution layer (UCB1 namespace selection,
saturation detection that surfaces to the operator, standing-task generation, directive interrupt,
gated runtime skill evolution) drives long-horizon UNATTENDED evolution of CONTENT only. Execution
traces stay in Langfuse. NO new required API keys (key-free sources work; optional keys only lift
limits). The system NEVER autonomously modifies its own machinery — a Stage-6 test asserts it. Nothing
out-of-scope built; core loop untouched; config backed up; each stage committed; failures reported
honestly.