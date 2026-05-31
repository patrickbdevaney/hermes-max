---
name: workflow-learn-framework
description: "Learn a novel/domain-specific framework from its real docs BEFORE coding, to prevent hallucinated APIs."
version: 1.0.0
author: Hermes Max
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [workflow, rag, docs, knowledge, anti-hallucination, hermes-max]
    category: hermes-max
    related_skills: [workflow-plan-first, workflow-critic]
---

<!-- TRIGGERS WHEN: "Learn a novel/domain-specific framework from its real docs BEFORE coding, to prevent hallucinated APIs." version: 1.0.0 author: Hermes Max license: MIT platfor -->

# Learn a framework before coding against it

When the work involves a framework/library/API you cannot reason about reliably
from pretraining, **ground yourself in its real documentation first** — don't
guess signatures. This closes the hallucinated-API trap at the *knowledge* layer
(prevention), complementing mcp-verify's *detection* of bad APIs after the fact.

## Trigger — when to invoke

Invoke `research_topic` BEFORE writing code when ANY of these hold:

- mcp-verify (or a test/import) reports a **hallucinated / nonexistent API**
  more than once for the same library — a clear signal you don't know its surface.
- You're about to use an **unfamiliar or domain-specific import** and you're
  **low-confidence** on its exact API (names, parameters, version differences).
- The task is flagged **novel/hard** by the shared `classify_difficulty` /
  novelty signal and hinges on a specific framework.

Do NOT invoke for the standard library or frameworks you already know well — that
just burns time.

## How (fully local — no external API)

1. `research_topic("<framework> <the specific thing you need>", n=3)` — searches
   the self-hosted SearXNG, extracts the top pages via Crawl4AI, distils them with
   the local model, and stores the notes under a `docs/<topic>` RAG namespace plus
   `framework→api` edges in the knowledge graph.
2. Then `search_code("<framework> <api you need>")` — it now returns the **real
   signatures** from the distilled docs, co-retrieved alongside your own code.
3. Implement against the retrieved signatures. If a needed page wasn't ingested,
   `ingest_doc("<url>", "<topic>")` a specific one.

## Notes

- It's **on-demand**: you don't need a pre-built corpus. (To pre-seed an
  operator's stack up front, `scripts/seed-docs.sh <urls>` exists — optional.)
- Every backend degrades gracefully: Crawl4AI down → trafilatura; SearXNG down →
  reported; model down → raw note stored. You always continue, with a warning.
- Bound it: a few targeted topics, not a crawl of the whole docs site.
