# CLAUDE.md — Hermes Maximal Capability Build (Lane 1 Sovereign Coder)

## Mission
Power up a stock Hermes Agent (v0.15.1, running on local Qwen3.6-35B-A3B via vLLM) into the most capable possible long-horizon autonomous engineering harness — one that beats Claude Code + Opus on **persistence, accumulated knowledge, unattended operation, and zero marginal cost**, while matching or exceeding its tool suite. NOT trying to beat Opus on single-turn reasoning (impossible with this model); winning on every axis where a free, always-on, compounding local agent structurally can.

## THE PRIME DIRECTIVE: maximal capability, clean architecture
"Maximal" means every capability lever that earns its place — NOT 30 fragile co-resident services. The discipline that makes this stable:
1. **Use Hermes's native capabilities first; never rebuild what it has.** Hermes already provides: a self-improving skill library, 3-layer SQLite memory + FTS5 cross-session search, an official DSPy+GEPA self-evolution module, cron scheduling, Telegram/WhatsApp/email approval gates, subagent delegation/orchestration, 90 bundled skills, LSP integration, checkpoints, and a kanban task decomposer. Most "SCC layers" already exist here — your job is to WIRE and TUNE them, not duplicate them.
2. **Every NEW capability is an independent MCP server** — own process, own healthcheck, startable/stoppable alone. A crash in one degrades one tool, never the agent. No shared mutable state except through APIs.
3. **Never fork or modify Hermes core.** Extend only via: MCP servers (config), skills (markdown in `~/.hermes/skills/`), hooks (`hooks:` in config.yaml), and native settings. If a lever seems to need editing Hermes source, it's the wrong lever — find the native-surface equivalent.
4. **Each component passes a standalone smoke test before integration.** Build → test isolated → register → integration test. If you can't test it alone, the boundary is wrong.
5. **The anti-Frankenstein gate:** kill any single MCP server mid-task; Hermes must degrade gracefully (report tool unavailable), never crash. If it crashes, fix the boundary before continuing.
6. **The single-env-var port story:** everything talks to `$VLLM_BASE_URL`. Dev = your inference host Tailscale IP (`http://YOUR_TAILSCALE_IP:8001/v1`), prod = `http://localhost:8001/v1`. One variable swap = full port. Any hardcoded host is a bug.

## What "holistically better than Claude Code" means here (the win conditions to build toward)
Build every component in service of these four structural advantages Claude Code cannot match:
- **Persistence/compounding:** Claude Code reads cold every session; Hermes accumulates skills + memory + a knowledge graph so it starts each task already knowing your stack. THIS is the core win — prioritize it.
- **Unlimited unattended time at $0 marginal cost:** 24/7 local grinding on electricity vs Claude Code's metered, attention-bound hours. Build for overnight autonomy.
- **Deterministic verification gates:** an agent that *cannot* declare done on broken code beats a smarter one that can. Reliability you can leave alone.
- **Total sovereignty + a superset tool suite:** your MCP servers + Hermes's 90 skills can exceed Claude Code's built-in tool surface.

---

## THE MAXIMAL CAPABILITY STACK

### TIER 0 — Native Hermes capabilities to WIRE/TUNE (no build; configure correctly)
These already exist. Your job is to make them excellent, not rebuild them.
- **Self-improving skill library** (= SCC Voyager layer). Already on. Tune `skills.creation_nudge_interval`, keep `guard_agent_created: true` initially. Feed it well via Tier-2 workflow skills below.
- **3-layer memory + FTS5 session search** (= SCC memory tiers 1-2). Already on. Set `memory.memory_enabled: true`, generous char limits. This is the cross-session recall.
- **DSPy + GEPA self-evolution** (= SCC goal-evolution layer, ICLR 2026). This is the big one most people miss: Hermes has an OFFICIAL evolution module (`hermes-agent-self-evolution`, separate repo, `pip install -e`). It optimizes skills/prompts/code from real session history. **Wire it as a weekly cron** that evolves the most-used skills against accumulated session data. This is "prompt-guided + stored goal evolution" done cleanly — no MAP-Elites archive process needed.
- **Cron scheduler** (= SCC outer scheduler, lightweight). Already on. Use for: nightly skill-curation, weekly DSPy evolution run, scheduled autonomous tasks, daily digest.
- **Subagent delegation + orchestration + kanban decomposer** (= SCC multi-agent topology). Already on (`delegation.orchestrator_enabled: true`, `kanban.auto_decompose: true`). Tune `delegation.max_concurrent_children` and `max_iterations`. This gives role-decomposition (planner/coder/reviewer) WITHOUT building a custom multi-agent framework.
- **Approval gates + messaging** (= SCC human-in-loop). Already on. Telegram = the overnight-ping channel.
- **LSP integration** (`lsp.enabled: true`) — already gives the agent go-to-definition/references/diagnostics across the codebase. A capability Claude Code largely lacks natively. Keep on.
- **Checkpoints** — enable for long-task resumability.
- **90 bundled skills** — includes `codebase-inspection`, `systematic-debugging`, `test-driven-development`, `github-pr-workflow`, `subagent-driven-development`, `writing-plans`, `requesting-code-review`, `dspy`, `arxiv`, `research-paper-writing`, `polymarket`. Audit which to keep enabled; these ARE a superset of Claude Code's tool surface already.

### TIER 1 — MCP servers to BUILD (the genuine capability gaps)

**`mcp-codebase-rag` — semantic code retrieval (the #1 capability add)**
The thing Hermes lacks natively that most closes the gap to Opus: retrieval-grounded context over your repos.
- Tools: `index_repo(path)`, `search_code(query, k)`, `get_symbol_context(symbol)`, `find_similar(snippet)`.
- Implementation: ONE vector store (Qdrant via Docker, or sqlite-vec for zero-infra), ONE embedding model (call a local embedding endpoint or a small embed model). Hybrid BM25 + dense. Tree-sitter chunking by function/class, not fixed windows — this matters for code.
- **Dual-mode retrieval (covers the per-step concern cleanly):** (a) per-task injection — retrieve at job start, inject into prompt; AND (b) `search_code` exposed as a callable tool so the agent re-retrieves mid-task when it hits something unfamiliar (agent-initiated = discovery-driven retrieval without touching Hermes's loop).
- Do NOT build the SCC's 8-stage HyDE→RAG-Fusion→ColBERT→Self-RAG→HippoRAG pipeline. Hybrid dense+BM25 with good code-aware chunking delivers ~85% of the value at ~10% of the fragility. (Optional, later: add a reranker call as ONE extra step if eval shows retrieval precision is the bottleneck. Don't pre-build it.)
- Standalone test: index a sample repo, assert relevant symbols returned for queries.

**`mcp-verify` — deterministic verification gate (the #1 reliability add)**
Makes unattended operation trustworthy. The agent cannot declare "done" until this is green.
- Tool: `verify(path, language)` → runs lint → typecheck → unit tests → (optional) property tests, returns structured pass/fail + diagnostics.
- Languages: at minimum Python (ruff + ty/mypy + pytest) and TS/JS (eslint + tsc + vitest/jest); Rust (clippy + cargo check + cargo test) if you work in it.
- The GATE is enforced by a Tier-2 skill instructing the agent to run `verify` before reporting done and to iterate on red. Plus a Hermes `hooks:` entry on task-completion if available, as a belt-and-suspenders block.
- Do NOT build the 10-stage ladder (mutation/fuzz/Lean4/debate). Three deterministic stages catch ~90% of breakage and never flake. Add mutation testing later ONLY for a specific high-value repo.
- Standalone test: green on known-good code, red on known-broken.

**`mcp-knowledge-graph` — structured project/entity memory (the compounding-knowledge add)**
The capability that most powers long-horizon persistence beyond flat memory: a graph of entities (files, functions, decisions, bugs, services) and relations the agent builds as it works.
- Tools: `record_entity(type, name, props)`, `record_relation(a, rel, b)`, `query_graph(pattern)`, `recall_about(entity)`.
- Implementation: the SIMPLEST thing that works — SQLite with a triples table + a query helper, OR a single embedded graph lib. **Do NOT stand up Neo4j + Graphiti + Cognee as three separate services** (SCC Frankenstein trap). One embedded store. The agent writes decisions/bugs/architecture facts here at task end (via skill instruction) and queries it at task start.
- This is the genuine upgrade over Claude Code: a persistent, queryable model of YOUR codebase's decisions and structure that survives across all sessions.
- Standalone test: record entities + relations, query them back.

**`mcp-escalation` — model routing (the capability-per-dollar add, OFF by default)**
Lets the agent escalate genuinely-hard subtasks to a cheap cloud tier while defaulting to free local.
- Tool: `escalate(task, tier)` → routes to a cheap-frontier OpenAI-compatible endpoint (DeepSeek V4 Flash $0.14/$0.28, or Kimi K2.6 for long-horizon-hard) and returns the result.
- **Hard per-day spend cap enforced in the server** (the field's #1 Hermes warning is silent autonomous spend). Default cap low; default the whole server OFF — the Lane-1 point is $0 local grinding. Turn on only after the local loop is proven.
- Tier-3 Opus stays EXCLUSIVELY on the laptop's separate Claude Code — never wired here (auth-collision avoidance).
- Standalone test: stub the endpoints, assert routing + cap enforcement.

**`mcp-observability` — trace + cost + skill-coverage dashboard (the "is it actually working" add)**
You can't improve unattended operation you can't see. Lightweight self-hosted tracing.
- Either: run **Langfuse** as a Docker container and point Hermes/MCP-server traces at it (OpenTelemetry), OR a minimal SQLite trace table + a tiny status page. Prefer Langfuse if you want real trace UI; prefer the minimal version if you want zero extra infra.
- Surfaces: per-task token/time, retrieval precision, skill-reuse rate, verify pass-rate, escalation spend, loop-stall events. This is how you tune the system over weeks.
- Standalone test: emit a trace, see it in the dashboard.

### TIER 2 — Skills to WRITE (markdown; the behavior layer — zero added instability)
Encode your engineering discipline as Hermes skills. These are prompts, not programs.
- `workflow-task-start`: query `mcp-codebase-rag` + `mcp-knowledge-graph` before acting; load relevant prior skills.
- `workflow-task-finish`: run `mcp-verify`; must be green before reporting done; on red, fix and re-run; record decisions/entities to `mcp-knowledge-graph`; let Hermes distill a skill if the task was novel.
- `workflow-stuck`: after N failed attempts on the same error, STOP thrashing — write a STUCK report and ping via Telegram with the specific blocker + 2-3 attempted approaches (the operator's "loop then ping me" pattern, explicit).
- `workflow-escalate`: criteria for when to call `mcp-escalation` (only genuinely-hard, well-scoped subproblems; never routine work).
- `workflow-plan`: for any task >5 files, use Hermes's native `plan`/`writing-plans` skill + kanban decomposition first.
- Let Hermes's self-improvement loop + the DSPy evolution cron refine these over time. Don't over-engineer them upfront.

### TIER 3 — Native skills to ENABLE/CONFIGURE (already bundled; just turn on the right ones)
From the 90 bundled, ensure these are active (they're a superset of Claude Code's surface):
`codebase-inspection`, `systematic-debugging`, `test-driven-development`, `github-pr-workflow`, `github-issues`, `github-code-review`, `requesting-code-review`, `subagent-driven-development`, `writing-plans`, `plan`, `spike`, `dspy`, `python-debugpy`, `node-inspect-debugger`. Audit and disable noise skills (the macOS/imessage/spotify/minecraft/pokemon ones) to keep the skill-retrieval space clean — too many irrelevant skills degrades retrieval precision.

---

## EXPLICITLY OUT OF SCOPE (the Frankenstein parts — do NOT build)
- Separate Neo4j + Graphiti + Cognee services (use ONE embedded graph in `mcp-knowledge-graph`)
- Letta as a separate memory service (Hermes's native memory + the KG cover it)
- 8-stage RAG pipeline with ColBERT/HippoRAG/Self-RAG (hybrid dense+BM25 is enough)
- Temporal/LangGraph outer scheduler (Hermes cron + DSPy evolution module cover it)
- MAP-Elites/ADAS/OMNI-EPIC archive processes (Hermes DSPy+GEPA is the clean evolution path; the rest is research-tier and Lane-3)
- 10-stage verification ladder with mutation/fuzz/formal (3 deterministic stages; add more per-repo only if needed)
- HSM signing sidecar / Merkle audit / Vault netns (Lane-3 prod-security; Lane-1 uses non-root user + sandboxed workdir + Hermes tool allow-lists + the native `tirith` security layer already in config)
- Custom multi-agent debate framework (Hermes native delegation/subagents cover it)
Each deferred item, if ever wanted, attaches later as one more independent MCP server without touching this build.

## BUILD ORDER (each step: standalone smoke test → register → integration test)
1. **Tune stock Hermes** per Tier 0 (config.yaml already optimized; verify skill library, memory, LSP, delegation, cron all live). Confirm stable on local Qwen before adding anything.
2. **`mcp-verify`** (simplest, highest reliability value, fully deterministic).
3. **`mcp-codebase-rag`** (highest capability value).
4. **`mcp-knowledge-graph`** (highest persistence value).
5. **Tier-2 workflow skills** (wire the above into behavior).
6. **Wire the DSPy self-evolution cron** (weekly skill/prompt evolution from session history).
7. **`mcp-observability`** (so you can see and tune).
8. **`mcp-escalation`** (last, OFF by default).
9. **Run the acceptance test.** Then port to your inference host (one env var) and re-run identically.

## Acceptance test (the bar for "maximally capable AND clean")
Unattended task: "Implement feature X across ≥5 files in <repo> with tests, following existing patterns."
- Queries `mcp-codebase-rag` AND `mcp-knowledge-graph` at start (grounded in your codebase).
- `mcp-verify` ends green; agent refused to report done while red.
- Completes unattended OR cleanly hits an approval gate and pings via Telegram with a specific question.
- Records ≥1 decision/entity to the knowledge graph and distills ≥1 skill.
- **Anti-Frankenstein: kill each MCP server in turn mid-task; Hermes degrades gracefully each time.**
- Runs identically on laptop (Tailscale endpoint) and your inference host (localhost) with only `$VLLM_BASE_URL` changed.
- **Compounding proof:** run a SECOND related task; confirm it starts faster/better by reusing the skill + KG entries from the first. This is the Claude-Code-beating property — demonstrate it.

## Repo layout
```
hermes-max/
  .env.example                # VLLM_BASE_URL + keys — the one swap point
  README.md                   # run + port-to-your inference host instructions
  hermes-config/              # the optimized config.yaml + hooks (no Hermes source)
  skills/                     # Tier-2 markdown workflow skills
  mcp-verify/                 # standalone server + tests + healthcheck
  mcp-codebase-rag/           # standalone server + tests + healthcheck
  mcp-knowledge-graph/        # standalone server + tests + healthcheck
  mcp-observability/          # Langfuse compose OR minimal trace server
  mcp-escalation/             # router + spend cap (OFF by default)
  dspy-evolution/             # cron wrapper around hermes-agent-self-evolution
  scripts/
    start-all.sh              # starts every MCP server + Hermes; laptop AND your inference host unchanged
    healthcheck.sh            # pings every component independently
    smoke-test.sh             # runs each standalone test
    register-mcp.sh           # registers all MCP servers with Hermes config
```

## Definition of done
- Stock Hermes tuned and stable on local Qwen via one env var.
- All five MCP servers built, each an independent process with passing smoke test + healthcheck.
- DSPy evolution cron wired to run weekly off session history.
- Tier-2 skills written; relevant native skills enabled, noise skills disabled.
- Acceptance test passes on laptop AND on your inference host (one var changed).
- Killing any single component does not crash the system.
- The compounding-proof second task demonstrably benefits from the first.
- Nothing on the out-of-scope list was built (note any deferral in README).
- Result feels like a clean, production-grade, maximally-capable tool — not a research prototype held together with tape.
```