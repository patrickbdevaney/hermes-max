---
name: workflow-codegraph
description: >-
  Before editing a function, call code_impact to see its blast radius (what breaks
  if it changes). Use code_callers/code_callees to understand call flow, code_importers
  for module dependents, code_dead_code to find unreferenced symbols, and
  code_structural_search for pattern-based refactoring across files. Trigger before
  any non-trivial edit to shared/core code, and when planning a refactor.
---

# workflow-codegraph

<!-- TRIGGERS WHEN: about to edit a shared/core function, or planning a refactor -->

Deterministic AST code-intelligence (mcp-codegraph) — the STRUCTURAL questions that
semantic RAG and per-symbol LSP don't answer. Run `index_codegraph(repo_path)` once
per repo first (fast, no model).

| Tool | Answers |
|---|---|
| `code_impact(symbol)` | **blast radius** — the transitive set of callers that could break if you change `symbol`. **Call this before editing shared code.** |
| `code_callers(symbol)` / `code_callees(symbol)` | call hierarchy — who calls it / what it calls |
| `code_importers(file_or_module)` | which files import this module |
| `code_dead_code()` | functions/classes never called by name (advisory) |
| `code_structural_search(pattern)` | ast-grep structural match (e.g. `def $F($$$): pass`) for pattern-based refactors |

## Discipline

- **Before changing a function's signature/behavior**, run `code_impact(name)` — the
  result is the set of call sites you must check. This is how you avoid the
  "fixed it here, broke it three files away" failure.
- Use `code_callees` to understand what a function depends on before you trust it.
- Complements [[workflow-lsp]] (LSP gives exact, compiler-resolved references for a
  single symbol; codegraph gives the fast graph-wide blast-radius closure) and
  `search_code` (semantic). Reach for codegraph for *structural* questions.

Call edges are name-resolved (an over-approximation of dynamic dispatch) — that bias
is deliberate for blast radius: better to over-list a dependent than miss one.
