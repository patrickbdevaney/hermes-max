---
name: workflow-edit-format
description: Make small, well-formed, diff-style edits and verify each one incrementally — the Aider 20%→61% lever.
trigger: every time you modify an existing file
---
# Edit in small, well-formed diffs — and verify each edit before the next.

The single biggest jump in measured edit success (Aider: 20%→61%) came from *edit format*:
targeted search/replace diffs that apply cleanly, not whole-file rewrites that drift. A small model
loses the thread on a full-file rewrite; a scoped diff keeps it honest.

## Rules
1. **Prefer diff / search-replace edits over whole-file rewrites.** Change the minimum lines needed.
   Hermes' native edit tool renders `inline_diffs` and runs a `file_mutation_verifier` — use that
   native edit path; do NOT paste an entire regenerated file when a few lines change.
2. **One coherent change per edit.** Don't bundle unrelated edits — each should be independently
   correct and reviewable.
3. **Well-formed:** the old text must match exactly (indentation included) and the new text must be
   syntactically complete (balanced brackets/quotes, valid indentation). A malformed diff that
   half-applies is worse than no edit.
4. **Incremental verify after EACH edit:** call `mcp_hermes_max_verify_quick_check(path)` — fast
   lint + typecheck, no tests — to confirm the edit is well-formed before moving on. Cheap enough to
   run every edit. Run the full `mcp_hermes_max_verify_verify(path)` (adds the test stage) once at
   subtask end (`workflow-subtask-loop`), then checkpoint.
5. On a `quick_check` failure: fix THAT edit immediately — do not pile another edit on top of a
   malformed one. If two fixes don't make it well-formed, revert the edit and rethink.

## Graceful degradation
If mcp-verify is down, `quick_check` is unavailable — fall back to the native `file_mutation_verifier`
+ your own re-read of the changed hunk, and rely on the subtask-end full verify. Keep edits small
regardless: small diffs are the protection, the verifier just confirms them.
