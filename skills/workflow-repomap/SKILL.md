---
name: workflow-repomap
description: >-
  Use repo_map at the START of a coding task to orient to an unfamiliar codebase
  structure. Pass the files you plan to edit as focus_files. The map ranks the most
  structurally important symbols (PageRank over the call/def graph) and their
  relationships — use it to find the relevant classes/functions before reading whole
  files. Trigger when entering a new repo or subsystem you haven't mapped yet.
---

# workflow-repomap

<!-- TRIGGERS WHEN: starting work in an unfamiliar repo/subsystem, before reading full files -->

`repo_map(repo_path, focus_files, token_budget)` (mcp-repomap) returns Aider's
PageRank repo-map: the most **structurally central** symbols in the repo, ranked by
how connected they are in the tree-sitter symbol graph. It answers *"what's the
important code I haven't been told about?"* — a different question from the other two
context tools, and best used together:

| Tool | Question it answers |
|---|---|
| `search_code` (RAG) | "what is semantically similar to my query?" |
| `lsp_*` (LSP) | "where exactly is this symbol defined / used?" |
| `repo_map` | "what is structurally most important here?" |

## How

1. At task start, call `repo_map(repo_path="<repo>", focus_files=[<files you'll edit>])`.
   `focus_files` bias the ranking toward what's relevant to your task (weighted 50×).
2. Read the ranked symbols to find the classes/functions that matter, then open only
   those files — instead of blindly reading the tree.
3. The map is cached 60s (structure changes slowly); re-call with different
   `focus_files` as your edit target shifts.

Pure static analysis — no model call, fast. Degrades to nothing if the server is
down (fall back to `search_code` + reading files).
