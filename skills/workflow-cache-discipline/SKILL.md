---
name: workflow-cache-discipline
description: >-
  Keep the prompt PREFIX stable and put dynamic content LAST so the serving layer's
  prefix cache stays warm across turns. Emit the stable parts (system prompt, skill
  metadata, tool schemas) first and unchanged; never interleave per-turn content
  (tool results, retrieved chunks) into the stable prefix. Trigger when assembling
  context for a multi-turn session and before any action that would rewrite early
  context.
---

# workflow-cache-discipline

<!-- TRIGGERS WHEN: multi-turn session; before any action that rewrites early context -->

The serving layer (vLLM) caches the longest **unchanged prefix** of the prompt across
turns — a cache hit skips re-prefilling those tokens. The orchestration side maximizes
that hit rate for free, with no serving-layer change, by keeping the prefix STABLE.

## The boundary (stable prefix → dynamic suffix)

Emit in this order, and never reorder:

1. **Stable prefix (identical every turn):** system prompt → skill metadata →
   tool schemas. These must be byte-identical across turns.
2. **Dynamic suffix (changes every turn):** conversation history → retrieved chunks
   → tool results → the current user turn.

**Rules:**
- Never interleave per-turn content (a retrieved chunk, a tool result) *into* the
  stable prefix — append it to the suffix.
- Avoid edits that rewrite early context mid-session. In particular, the
  [[workflow-context-condenser]] rewrites the prefix and so **busts the cache** —
  which is exactly why it's gated to "only near the limit, not routinely."
- Prefer [[workflow-filesystem-offload]] (keep a short stable reference, read the
  detail back on demand) over pasting large dynamic blobs that push the prefix around.

## Verify it's working

The prefix cache is observable: vLLM exposes `vllm:prefix_cache_hits_total` /
`vllm:prefix_cache_queries_total` (see `$VLLM_BASE_URL/../metrics`, surfaced by
`hm observe`). In a healthy multi-turn session the hit ratio should RISE across turns;
a sudden drop means something rewrote the prefix (a condense, a reordered system
block, a tool-schema change) — investigate before continuing.
