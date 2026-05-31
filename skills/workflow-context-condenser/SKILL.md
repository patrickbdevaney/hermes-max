---
name: workflow-context-condenser
description: >-
  When a long session approaches the context limit (~80% of the model's window),
  call condense_context to summarize the OLDEST turns while preserving the first
  few messages and the most recent turns. Use ONLY when genuinely near the limit —
  condensing rewrites the prompt prefix and lowers prompt-cache hit rate, so it is
  not a routine optimization. Trigger on long multi-hour sessions, not short tasks.
---

# workflow-context-condenser

<!-- TRIGGERS WHEN: a session has grown long (many turns) and context is ~80%+ full -->

On a long-running session the 35B model loses coherence as the window fills. The
`condense_context` tool (mcp-observability) applies the OpenHands
LLMSummarizingCondenser mechanism: at ≥80% of `max_model_len` it summarizes the
**oldest** events into one dense digest, **always preserving the first 4 messages
and the most recent turns**. OpenHands measured up to ~2× token-cost reduction at
this fill with no quality degradation (arXiv:2511.03690).

## When to use it (and when NOT to)

- **Use it** when a session is genuinely near the limit — long multi-hour work,
  many tool round-trips, the prompt is dominated by stale early history.
- **Do NOT** condense routinely or early. Condensing rewrites the prompt **prefix**,
  which **invalidates the prompt cache** — a near-empty context loses more (cache
  misses) than it gains. Only fire when the window is actually ~80%+ full.

## How

`condense_context(history)` returns `{fired, history, tokens_before, tokens_after,
ratio, ...}`. If `fired` is false you were under threshold — keep going. If true,
continue from the returned (shorter) `history`; a `condenser_fired` span records the
before/after token counts and ratio so the savings are observable.
