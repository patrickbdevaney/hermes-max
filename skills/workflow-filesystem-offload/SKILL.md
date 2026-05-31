---
name: workflow-filesystem-offload
description: >-
  When a tool returns large output (a research synthesis, a big file read, long logs,
  a wide search result), write it to a scratch file and keep only a ~100-token
  summary + the file path in context — then read back specific line ranges with grep
  on demand instead of holding the whole blob. Trigger whenever a single tool result
  is more than ~50 lines / a few KB.
---

# workflow-filesystem-offload

<!-- TRIGGERS WHEN: a single tool result is large (>~50 lines / a few KB) -->

Holding large tool outputs in context burns the window and degrades coherence
(the same pressure the condenser relieves after the fact — this prevents it up
front). Offload, don't hold.

## The rule (deterministic)

1. When a tool returns a large result, write it to `.hermes-scratch/<descriptive-name>.md`
   in the project (create the dir if missing; it's git-ignorable scratch).
2. Keep in context only: a **one-line summary** + the **path** + the **line count**.
   e.g. `→ deep_research on BLAKE3 saved to .hermes-scratch/blake3-research.md (412 lines): spec + 8 sources + test vectors`.
3. Read back **only what you need**, on demand:
   - `grep -n "<term>" .hermes-scratch/<file>` to locate, then
   - read the specific line range (e.g. `sed -n '120,160p'` or the file-read tool with offset/limit).
4. Never paste the whole blob back into context to "remember" it — the file IS the
   memory; re-read the slice you need.

## When to use

- `deep_research` / `synthesize` reports → offload, keep the gap_note + path.
- Large file reads you only need a section of → note the path, grep the symbol.
- Long command logs / test output → save, keep the failing lines only.

This pairs with [[workflow-context-condenser]] (offload prevents the bloat;
condense relieves it once it's already there) and [[workflow-cache-discipline]]
(keeping context small + stable also protects the prompt cache).
