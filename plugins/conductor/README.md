# conductor — deterministic conductor↔executor split + MCP lifecycle enforcement

A Hermes plugin (`register(ctx)` + `ctx.register_hook`). A `pre_llm_call` hook re-injects
the execution contract every turn (survives compaction); `post_tool_call` detects file
writes + verify results and fires the conductor on stuck-detection; everything runs
in-process (no subprocess). State lives in `<cwd>/.hermes-conductor/state.json`.

## The enforcement framework (B0 — the general rule)
A value-adding MCP should be **lifecycle-ENFORCED** (fired from a hook regardless of the
model's judgment) rather than **DISCRETIONARY** (prompted, model decides) when ALL THREE
hold:
1. **Low LLM reliability** at calling it at the right moment (the model forgets/skips it).
2. **High consequence of skipping** — a broken guarantee or degraded future runs, not
   merely additive quality.
3. **Structural bias against calling it** — using it feels like overhead exactly when the
   model is most confident (verification, checkpointing, self-checks).

Score **3/3 → enforce**; otherwise → discretionary. The model's only influence over an
enforced capability is its *content*, never *whether the call happens*. Every enforced
fire emits an OTel/livelog span and degrades gracefully (a down MCP is logged and skipped,
never crashes the loop). Full per-MCP inventory: `../../MCP_ENFORCEMENT_AUDIT.md`.

## What is enforced (`enforce.py`, wired from the hooks)
| Capability | Hook point | Behaviour |
|-----------|-----------|-----------|
| **verify_formal** (B2.1) | `post_tool_call` on a source file write | fast compile/type/lint gate (rungs 0-1); a hard compile/type failure queues correction guidance (bounded to `CONDUCTOR_VERIFY_MAX_RETRIES`, then surfaces — never wedges). The FULL ladder (rung 2) runs at the done gate (`_handle_done`). Four-value handling: verified/unknown → proceed; counterexample → block-with-guidance; spec_rejected → downgrade-and-flag (never a pass). |
| **checkpoint** (B2.2) | `post_tool_call` after an observed green verify | fires `checkpoint(verify=True)` once per green step — the checkpoint re-verifies and refuses on RED, so it is the hard gate. |
| **research entry** (B2.3) | `pre_llm_call` at task start | if the novelty classifier marks the task `synthesis`, fires `deep_research` ONCE before implementation (still corpus-first-gated inside) and injects a digest. |
| **watchdog** (B2.4) | `post_tool_call`, every call | unconditional background-via-hook tick (spiral check); emits a span and nudges on a detected loop. Never a model tool call. |

## What is soft-enforced (B3 — fired at a lifecycle point, not a hard gate)
| Capability | Hook point | Behaviour |
|-----------|-----------|-----------|
| **knowledge-graph** (B3.5) | `_handle_done` success + `on_session_end` backstop | a KG task-close write once per run, recording what was decided + why (the ambient "we decided X about this codebase" facts the model misses). |
| **classification** (B3.6) | `pre_llm_call` | the plan/execute split already authors a conductor-signed PLAN.md with per-step complexity BEFORE execution (hook-injected, not prompted); on top of that, criticality_classify runs in-hook on the current step so the model can't dodge the conductor by self-classifying a step as "easy". |
| **codebase-rag** (B3.7) | `pre_llm_call` at the start of a multi-file step | a RAG retrieval pass surfaces relevant prior patterns before implementation, even when the model "knows the codebase". |

Discretionary (B4) capabilities (docs, search, lsp, repomap, codegraph, scopemap,
observability) stay prompt-driven — see the audit. Toggle any enforced capability off for
ablation with `CONDUCTOR_ENFORCE_{VERIFY,CHECKPOINT,RESEARCH,WATCHDOG,KG,CLASSIFY,RAG}=0`.

Spans emitted: `verify_enforced`, `checkpoint_enforced`, `research_entry_gate`,
`watchdog_background`, `kg_taskclose_write`, `classification_prefired`, `rag_pre_multifile`
(plus the existing `conductor.*` events).
