# mcp-search (port 9108)

**Verifier-guided test-time search** (Stage 1.2) — bounded best-of-N selection,
**lossless by construction**: candidates are chosen by *execution* (each run
through `mcp-verify`), never by a model judging itself. SWE-PRM-class selection
was +10.7 pts; this is the your inference host-safe form of it.

## Tool

`generate_and_select(task_spec, n=3, language, target_path, tests, base_files, candidates)`

- **Selector mode** (pass `candidates=[{"id","files":{path:content}}, ...]` + `tests`):
  runs each candidate through `mcp-verify`, returns the green one — **most tests
  passed, smallest diff**. Cheap, always available, no model calls.
- **Generate mode** (omit `candidates`): generates N patches from `$VLLM_BASE_URL`
  for `task_spec`, then selects against `tests`.

Never returns a red selection: if nothing verifies green, `selected` is `None`
(caller escalates / rethinks).

## your inference host discipline

Best-of-N competes for the **single GPU stream**, so:
- default `N=3`, hard-capped at `SEARCH_MAX_N=6`;
- generation is for **HARD subtasks only** — the difficulty signal (Stage 3
  classifier) gates it via the `workflow-effort-routing` / subtask skills;
- the selector path adds no model calls (only the verifier).

## Graceful degradation

- No `$VLLM_BASE_URL` → generation returns a clean `disabled` marker; the agent
  writes the single best patch itself.
- `mcp-verify` unreachable → `verify_unreachable`, no selection (honest, never a
  guessed pick).
- Server killed → Hermes reports the tool unavailable; the agent proceeds with a
  single patch. Never crashes Hermes.

OTel: emits a `search_selected` span (selected id / green count / size) to Phoenix.

## Run / test

```bash
.venv/bin/python smoke_test.py     # boots a throwaway mcp-verify; no model needed
./healthcheck.sh                   # GET /health
```
