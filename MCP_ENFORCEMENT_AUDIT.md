# MCP_ENFORCEMENT_AUDIT.md — lifecycle-enforcement inventory (B1)

**Inventory only — no behaviour changed in this step.** Records, for each of the 14
MCPs, whether it is currently DISCRETIONARY (the driver LLM decides to call it) or
already LIFECYCLE-ENFORCED (fired from a conductor hook regardless of the model's
judgment), and at which hook. This is the baseline the B2/B3/B4 wiring works against.

## The enforcement framework (B0)
A capability should be **enforced** (hook-fired) when ALL THREE hold: (1) low LLM
reliability at calling it at the right moment, (2) high consequence of skipping
(broken guarantee / degraded future runs, not merely additive quality), (3) structural
bias against calling it (feels like overhead exactly when the model is most confident).
3/3 → enforce; otherwise → discretionary.

## The conductor hook surface (the enforcement mechanism)
`plugins/conductor/__init__.py` → `register(ctx)` registers four hooks
(`plugins/conductor/__init__.py:461`):

| Hook | Fires | Callback | Context available |
|------|-------|----------|-------------------|
| `pre_llm_call` | before every model call | `_pre_llm_call` (`:210`) | step/plan/turn count, last verify, pending guidance, operator pause/steer; returns `{"context": …}` injected into the next message |
| `post_llm_call` | after each response | `_post_llm_call` (`:316`) | response/usage → token counts |
| `post_tool_call` | after ANY tool call | `_post_tool_call` (`:330`) | `tool_name`, `args` (path/command), `result` (≤2000 chars) |
| `on_session_end` | session end | `_on_session_end` | final metrics |

Repo `*_core` modules are imported **in-process** (sys.path includes repo root +
`mcp-escalation` + `mcp-verify`, `:35`); other MCPs are reachable the same way by
appending their dir, or over HTTP. All hermes-max imports inside hooks are lazy +
guarded — a missing module degrades, never breaks the loop (`:116` `_emit` swallows all).

Detections already present in `_post_tool_call`: file writes
(`write_file/edit_file/str_replace/patch`, `:338`); pytest pass/fail by parsing the
agent's own `bash`/`terminal` runs (`:343`); step advance via `EXECUTION_STATE.json`
(`:172`); done declaration → `_handle_done` (`:368`).

---

## Per-MCP inventory

| # | MCP | Port | Status today | Fired from | Evidence | B0 score | Target (B2/B3/B4) |
|---|-----|------|--------------|-----------|----------|----------|-------------------|
| 1 | **verify** | 9101 | **partially enforced** | `post_tool_call` → `_handle_done` runs `verify_core.verify(cwd)` **only on done-declaration** (`:194`). Pytest *parsing* happens per tool call but does not *fire* verify. | direct import | 3/3 | **HARD-ENFORCE** — fire `verify_formal` on every file write above threshold + as a hard gate before every checkpoint. (B2.1) |
| 2 | **escalation / conductor** | 9105 | **enforced (stuck)** | `post_tool_call` → `_trigger_conductor` on verify-double-fail / no-progress / executor-requested (`:354-365`); `on_session_end` summary. **Classification (`classify_difficulty`/`should_escalate`) is NOT hook-fired.** | direct import (`conductor_core`) | 3/3 (classification) | **SOFT-ENFORCE** the *classification* in `pre_llm_call` (confirm the plan/execute split is hook-fired, not prompted). (B3.6) |
| 3 | **checkpoint** | 9106 | **discretionary** | model calls `checkpoint` / `revert_to_last_green` at its discretion | — | 3/3 | **HARD-ENFORCE** — fire `checkpoint` automatically after every green verify, before advancing. (B2.2) |
| 4 | **research** | 9110 | **discretionary (overuse-gated)** | corpus-first/budget/cooldown/exhaustion gates prevent *overuse*; nothing enforces *entry use* | — | 3/3 (entry) | **HARD-ENFORCE entry** — fire `deep_research` once at task start for tasks the classifier marks as needing novel external knowledge (still corpus-first-gated). (B2.3) |
| 5 | **watchdog** | 9107 | **discretionary** | exposes `check_spiral/check_stall/check_progress/check_budget` as tool calls; **not a background process** | — | enforce (should not be a tool call) | **HARD-ENFORCE as background** — run unconditionally on every run; model never invokes it. The conductor's per-tool-call hook already gives an unconditional fire point. (B2.4) |
| 6 | **knowledge-graph** | 9103 | **discretionary** | model calls `record_entity/record_relation/recall_about` | — | 3/3 (ambient facts) | **SOFT-ENFORCE** — require a KG write at task close summarising what was decided + why. (B3.5) |
| 7 | **codebase-rag** | 9102 | **discretionary** | model calls retrieval when it chooses | — | borderline | **SOFT-ENFORCE** — a RAG retrieval pass at the START of any multi-file edit. (B3.7) |
| 8 | **docs** | 9109 | **discretionary** | `research_topic/search_docs/fetch_clean/ingest_doc` | — | additive | **LEAVE DISCRETIONARY** (B4) |
| 9 | **search** | 9108 | **discretionary** | verifier-guided best-of-N, model-invoked | — | additive | **LEAVE DISCRETIONARY**; A3 adds formal-pass as a best-of-N tiebreaker (a *ranking* change, not a fire). |
| 10 | **lsp** | 9112 | **discretionary** | symbol/type introspection | — | additive | **LEAVE DISCRETIONARY** (B4) |
| 11 | **repomap** | 9111 | **discretionary** | repo map | — | additive | **LEAVE DISCRETIONARY** (B4) |
| 12 | **codegraph** | 9114 | **discretionary** | `code_impact` etc. | — | borderline | **LEAVE DISCRETIONARY** + strong skill prompt to call `code_impact` before modifying a many-caller function; do NOT hard-fire. (B4) |
| 13 | **scopemap** | 9115 | **discretionary** | scope map | — | additive | **LEAVE DISCRETIONARY** (B4) |
| 14 | **observability** | 9104 | **infra** | OTel/metrics plumbing, not really a tool call | — | infra | **NOT A TOOL CALL** — every enforced fire already emits a span. (B4) |

---

## Summary of the gap
- **Enforced today:** verify (done only) + conductor escalation (stuck only). 2 of 14.
- **B2 will hard-enforce 4:** verify_formal (post-write + pre-checkpoint), checkpoint
  (post-green), research-entry (once/qualifying task), watchdog (background-via-hook).
- **B3 will soft-enforce 3:** KG task-close write, classification-in-hook, RAG-before-
  multi-file.
- **B4 leaves 7 discretionary:** docs, search, lsp, repomap, codegraph, scopemap,
  observability.

Every enforced fire (B2/B3) must: be deterministic (model can't skip it), emit an OTel
span (`verify_enforced`, `checkpoint_enforced`, `research_entry_gate`,
`watchdog_background`, `kg_taskclose_write`, `classification_prefired`,
`rag_pre_multifile`), degrade gracefully if the target MCP is down, and never wedge the
loop (bounded retries; proceed-with-flag on `unknown`).
