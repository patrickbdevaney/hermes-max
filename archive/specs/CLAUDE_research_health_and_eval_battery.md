# CLAUDE_research_health_and_eval_battery.md — Permanently Fix Research Health + Prove Every Feature Works in the Real Agent Loop

Two problems, both must be fixed: (1) mcp-research shows DOWN while the process is alive and the agent
is actively calling it — the health check is wrong, and this blocks the entire deep-research capability;
(2) there is no eval that proves each MCP and core feature ACTUALLY WORKS when invoked by the Hermes
agent in the real loop (isolation smoke tests pass but live agent calls fail). Fix both. Work in STAGES,
in order; each committed and validated. Read the whole spec first. Report after each stage.

## STAGE 1 — PERMANENTLY FIX mcp-research HEALTH (decouple liveness from dependency probing)
The research server reports DOWN while alive and serving because its `/health` endpoint conflates two
different questions: "is this server process alive and able to accept requests?" (liveness) vs "are all
8 upstream sources + SearXNG + Crawl4AI + the chat model reachable?" (readiness/dependency status). A
health check used for UP/DOWN status MUST only answer liveness. Dependency status is informational.

- **Split the health endpoint into two concerns:**
  - `/health` (liveness) — returns 200 OK with `{"status":"ok","server":"mcp-research","port":9110}`
    IMMEDIATELY, doing NO upstream network calls. It only confirms the process is up and the HTTP
    server responds. This is what status.sh / healthcheck.sh use for UP/DOWN. It must never block on
    SearXNG, Crawl4AI, the chat model, or any source API. Sub-10ms response.
  - `/ready` or `/health?deep=1` (readiness) — the existing rich check (sources, searxng_up,
    crawl4ai_up, chat model reachable, corpus state). Informational only; a failing dependency here
    shows as a WARNING in status.sh, never as DOWN. The agent can still call the server; individual
    tools degrade per the existing graceful-degradation matrix if a dependency is actually down.
- **status.sh / healthcheck.sh use liveness for UP/DOWN**, and optionally show readiness as a separate
  informational line ("research: UP · sources 7/8 reachable, github warming"). A server is DOWN only if
  the liveness probe fails (process dead or port not listening).
- **Apply the same split to ALL servers** that currently do upstream checks in their health endpoint
  (mcp-docs does searxng/crawl4ai checks; any other). Liveness = process up, fast, no network.
  Readiness = dependencies, informational.
- **Root-cause the specific block:** find what mcp-research's health handler does that takes long
  enough or fails — likely it probes sources or the chat model synchronously on every /health call.
  Move all of that to /ready. Confirm /health returns in <50ms even with SearXNG/Crawl4AI/some sources
  down.

**Stage-1 DoD:** mcp-research /health returns 200 in <50ms with NO upstream calls; with SearXNG stopped,
research still shows UP (liveness) with a readiness WARNING, not DOWN; status.sh shows 10/10 UP with the
research server live; the agent's plan_research / deep_research calls reach the server and execute (or
degrade gracefully per-dependency), never blocked by a false DOWN; the same liveness/readiness split is
applied to every server that did upstream checks in /health. Committed.

## STAGE 2 — VERIFY DEEP RESEARCH ACTUALLY RUNS END-TO-END (the capability that's been blocked)
With health fixed, prove the full research cascade works when called by the agent — not in isolation,
in the real loop.
- A live test: invoke deep_research (via the MCP tool, as the agent would) on a real query
  ("Groth16 zk-SNARK verifier specification and test vectors"). Assert: plan_research returns a plan;
  develop_queries produces per-source queries; explore fetches from ≥2 sources (arxiv + at least one
  more) and writes to corpus; verify_claims runs; synthesize returns a cited brief. Each stage emits
  live telemetry (tqdm progress, per-source crawl/distil timing, chat-model call latency).
- If any source errors (e.g. a 429, a timeout), it degrades to fewer sources and the run COMPLETES —
  a single source failing never fails the whole cascade.
- Output the full cascade trace to the live log + a readable artifact so the operator can confirm every
  stage fired with real data.

**Stage-2 DoD:** deep_research runs end-to-end against real sources, completes within the wall budget
(~3-6 min typical), writes real sources to corpus, returns a cited synthesis, emits per-stage live
telemetry; a deliberately-killed source degrades gracefully without failing the run. Committed.

## STAGE 3 — THE FEATURE EVAL BATTERY (prove EVERY MCP + core feature works in the real agent loop)
The core problem: isolation smoke tests pass but features fail when the Hermes AGENT actually invokes
them. Build an eval battery that drives each capability THROUGH a real agent invocation (an actual LLM
agent turn calling the tool), not a direct HTTP smoke test. This is the difference between "the server
responds" and "the agent can actually use this feature to do work."

- **`scripts/eval-battery.sh`** — runs a series of MINIMAL real agent tasks, each designed to exercise
  exactly one capability through the full Hermes loop, and asserts the capability actually functioned.
  Each test: give Hermes a tiny task that REQUIRES the feature, run it, assert the expected tool was
  called AND produced the expected effect (checked in the real artifact: the file, the KG, the corpus,
  the git checkpoint — not just a 200 response).
- **Coverage — one focused agent task per capability:**
  | Capability | Minimal agent task | Asserted effect (real artifact) |
  |---|---|---|
  | verify gate | "write a function that adds two numbers, with a test" | verify ran; refused done while red; green at end |
  | codebase-rag | "what functions exist in this repo?" (after indexing) | search_code/index_repo called; returns real symbols |
  | knowledge-graph | "remember that we chose SQLite for storage" | record_entity/relation called; recall_about returns it |
  | checkpoint | "make a change, checkpoint, then revert it" | git checkpoint created; revert_to_last_green restored |
  | watchdog | a task with a deliberate tight loop / long op | spiral or stall detected and surfaced |
  | search (best-of-N) | a verifiable subtask flagged HARD | generate_and_select ran; verifier picked a green candidate |
  | docs | "find the docs for <a real library> and summarize" | search_docs + fetch_clean ran; content retrieved |
  | research | "research <a real recent topic> with citations" | deep_research ran end-to-end; cited synthesis returned |
  | escalation/conductor | (only if a key present) a HARD subtask | classify_difficulty + route fired; degraded to local if no key |
  | observability | any task | spans emitted to Phoenix; live log populated |
  | core memory | "update your core memory with X" | core_memory_append wrote to MEMORY.md |
- **The assertion model is the key:** each test checks the REAL EFFECT (file on disk, row in the KG db,
  doc in corpus, commit in git, span in Phoenix, line in live log), not just that an HTTP call returned
  200. A feature "works" only if the agent invoking it produced the real-world change it's supposed to.
- **Each test is isolated** (own temp project dir, snapshot/restore stores so the battery doesn't
  pollute real state) and **reports PASS/FAIL per capability** with the actual evidence (what tool
  fired, what artifact it produced/failed to produce).
- **Run modes:** `eval-battery.sh` (all), `eval-battery.sh <capability>` (one), `--no-cloud` (skip
  conductor tests that need keys). Output a readable `eval_battery_report.md`: a table of every
  capability, PASS/FAIL, the agent task used, the tool that fired, the artifact effect verified, and for
  failures the exact point the chain broke (tool not called / called but errored / called but no effect).
- **This is the answer to "does every feature actually work with the agent":** after this runs green,
  you have evidence that each capability functions in the real loop, and for any that fail you have the
  precise break point.

**Stage-3 DoD:** eval-battery.sh drives each core capability through a real Hermes agent task and asserts
the real-world effect (not just a 200); produces eval_battery_report.md with per-capability PASS/FAIL +
evidence + precise break-point for failures; runs isolated (temp dirs + store snapshot/restore) without
polluting real state; --no-cloud and single-capability modes work. Committed.

## STAGE 4 — WIRE THE BATTERY INTO THE WORKFLOW
- `hm eval` runs the full battery; `hm eval <capability>` runs one.
- The battery is the canonical "is the system actually working" check — documented in the README as the
  thing to run after any change or on a fresh install to confirm the agent can really use every feature.
- bootstrap.sh optionally runs a fast subset (`bootstrap.sh --verify-agent`) that drives 2-3 core
  capabilities through a real agent turn to confirm the install actually works end-to-end, not just that
  servers are up.

**Stage-4 DoD:** `hm eval` and `hm eval <capability>` work; README documents the battery as the
agent-level verification; bootstrap --verify-agent drives a real agent turn through 2-3 capabilities and
confirms end-to-end. Committed.

## NON-NEGOTIABLE DISCIPLINE
Never modify Hermes's core loop; fixes live in the MCP servers + scripts; presence-gated; degrade
cleanly; back up config; commit per stage; failures reported honestly.

## REPORT (per stage)
What landed; the root-cause of the research /health block (exactly what it was doing that caused DOWN);
liveness-vs-readiness split confirmation; the deep_research end-to-end trace; the eval-battery
per-capability PASS/FAIL with real-artifact evidence; git SHA.

## DEFINITION OF DONE
mcp-research (and every server) has a liveness /health that returns fast with no upstream calls so a
live server NEVER shows DOWN, with dependency status moved to an informational /ready — the deep-research
capability is unblocked and proven to run end-to-end with real sources and per-stage telemetry; and an
eval battery drives EVERY core MCP/capability through a real Hermes agent task, asserting the real-world
effect (file/KG/corpus/git/span), producing a per-capability PASS/FAIL report with precise break-points,
wired into `hm eval` and bootstrap --verify-agent. After this, there is EVIDENCE that every feature
actually works in the real agent loop — not just that servers respond. Core loop untouched; config backed
up; each stage committed; failures reported honestly.