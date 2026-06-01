# MCP servers — reference

Each capability is an **independent HTTP process** with its own healthcheck and
standalone smoke test. Killing any one degrades exactly one tool and never crashes
the agent (the anti-Frankenstein property). The single source of truth for the list
is [`mcp-manifest.yaml`](../mcp-manifest.yaml) — adding a server is one entry there;
every script (`start-all.sh`, `register-mcp.sh`, `healthcheck.sh`, …) reads it via
`scripts/manifest.py`.

There are **13 servers in the manifest** plus the **Serena language-server backend**
(port 9113) that `mcp-lsp` fronts — fourteen processes in all. Each runs in both
deploy profiles unless noted (see [deployment.md](deployment.md)).

## Verification

- **`mcp-verify` (9101)** · *pure-Python, both profiles* — the deterministic
  done-gate. `verify(path)` runs lint → typecheck → tests (the hard pass/fail);
  `quick_check` (lint+type, per-edit); `deep_verify` (difficulty-gated
  property/mutation/fuzz); `property_test` · `metamorphic_test` ·
  `differential_test` · `mutation_test`; `quality_check` (advisory senior-review
  texture — never gates).

## Retrieval & memory

- **`mcp-codebase-rag` (9102)** · *BM25 + AST-graph when no embed/rerank* —
  `search_code`, `index_repo`/`scan_repo`, `get_symbol_context`, `find_similar`,
  `retrieve_related` (multi-hop), `repo_map`, `corpus_hit_check`
  (already-answered gate), `index_document`. Hybrid BM25 + dense + graph-rank;
  degrades to BM25 + graph automatically.
- **`mcp-knowledge-graph` (9103)** · *pure-Python sqlite triples* —
  `record_entity`, `record_relation`, `query_graph`, `recall_about`, and the
  self-editing core memory (`core_memory_get/append/replace`) wired to Hermes's
  always-in-context `MEMORY.md` block.
- **`mcp-repomap` (9111)** · *static, no model* — `repo_map` (Aider PageRank over a
  tree-sitter / NetworkX symbol graph).
- **`mcp-lsp` (9112, + Serena backend 9113)** · *falls back to grep* —
  `lsp_find_symbol`, `lsp_find_references`, `lsp_go_to_definition`, `lsp_hover`,
  `lsp_rename`, `lsp_diagnostics`, `lsp_activate_project` (~50ms compiler-grade
  navigation over [oraios/serena](https://github.com/oraios/serena)).
- **`mcp-codegraph` (9114)** · *pure AST graph* — `code_impact` (blast radius),
  `code_callers`/`code_callees`, `code_importers`, `code_dead_code`,
  `code_structural_search` (ast-grep), `index_codegraph`.

## Research (sovereign web)

- **`mcp-docs` (9109)** · *Crawl4AI → trafilatura ladder* — `search_docs`
  (self-hosted SearXNG), `fetch_clean`, `ingest_doc`, `research_topic` (learn a
  framework on demand, no external API).
- **`mcp-research` (9110)** · *requires docs; degrades to deterministic
  plan/synthesis without a chat model* — the bounded deep-research engine. Core loop
  `plan_research → develop_queries → explore → verify_claims (≥2 sources) →
  synthesize`, with `deep_research` end-to-end (compounds into RAG/KG). Plus a wide
  keyless source fan-out (`arxiv_search`, `semantic_scholar_*`, `github_search`,
  `hn_search`, `stackexchange_search`, EIP/RFC readers), KG fact-edge landing, and a
  **Banyan** UCB1 self-direction layer for unattended research cycles. See
  [research-engine.md](research-engine.md).

## Reliability & long-horizon

- **`mcp-checkpoint` (9106)** · *requires verify* — `checkpoint(label,
  verify=True)`, `revert_to_last_green`, `list_checkpoints`, `checkpoint_status`,
  `snapshot_state`/`restore_state` (restores the PLAN.md + notes, not just the git
  tree). Commits only from a verified-green state.
- **`mcp-watchdog` (9107)** · *falls back to native guardrails* — out-of-band,
  not-turn-based detection: `check_spiral` (CoT loop), `check_stall` +
  `record_heartbeat` (hung vs legitimately-waiting), `tool_budget` /
  `estimate_duration`, `check_progress`, `start_task_budget`/`check_budget`.
- **`mcp-observability` (9104)** · *no-op when Phoenix down* —
  `record_trace`/`record_metric`/`record_task_metrics`,
  `record_trajectory`+`localize_failure`+`list_trajectories`, `record_skill_fired`,
  `condense_context` → OpenTelemetry to Phoenix.

## Search & cloud

- **`mcp-search` (9108)** · *requires verify; selector always on* —
  `generate_and_select` (verifier-guided best-of-N: the *verifier*, not a model,
  picks the winner — never returns a red patch), `parallel_draft` (fan the conductor
  pool over a verifiable subtask).
- **`mcp-escalation` (9105)** — **the conductor**: presence-gated tiered cloud help
  (`classify_difficulty`, `should_escalate`, `route`, `escalate`,
  `conductor_plan`/`conductor_synthesize`, `conductor_status`). Cloud OFF by
  default, hard USD cap, triple-gated frontier rung. See
  [architecture.md](architecture.md) §4.

Per-server detail (tools, env vars, healthcheck) lives in each `mcp-*/README.md`.
