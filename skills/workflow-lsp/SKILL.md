---
name: workflow-lsp
description: >-
  Use lsp_find_references, lsp_go_to_definition, and lsp_diagnostics when navigating
  cross-file symbol relationships or checking for type errors after edits. Prefer LSP
  over grep for symbol lookup — it is exact (compiler-grade) and ~50ms vs tens of
  seconds, and catches cross-file errors immediately after an edit. Trigger when
  finding a symbol's definition/callers or validating an edit touched no other file.
---

# workflow-lsp

<!-- TRIGGERS WHEN: looking up a symbol's definition/references, or checking type errors after an edit -->

Compiler-grade symbol intelligence (Serena language-server backend, via mcp-lsp).
**Prefer LSP over grep for symbol work** — grep matches text and misses scope/imports;
the language server resolves the actual symbol.

| Tool | Use it to |
|---|---|
| `lsp_find_references(name_path, relative_path)` | find every CALLER of a symbol before changing its signature |
| `lsp_go_to_definition(name_path)` | jump to the real signature/body instead of guessing or grepping |
| `lsp_diagnostics(relative_path)` | **immediately after editing a file**, catch type errors / undefined names without waiting for the verify gate |
| `lsp_find_symbol(name_path)` | locate a function/class/method by name across the project |

## Discipline

- **After every non-trivial edit**, run `lsp_diagnostics` on the changed file — this
  is the tight feedback loop (catch the cross-file type error now, not at verify time).
- **Before changing a signature / renaming**, run `lsp_find_references` to see the
  full blast radius.
- `relative_path` is relative to the project root; call `lsp_activate_project` when
  you switch to a different repo so the language server re-indexes it.

Degrades gracefully: if the backend/language server is down the tool returns a clear
error — fall back to `search_code` (RAG) or grep.
