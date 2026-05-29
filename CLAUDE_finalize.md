# CLAUDE_finalize.md — Finalize & Validate the hermes-max Harness

You are finishing the already-built `hermes-max/` harness. Everything here is config fixes +
one validation run. Do NOT add new MCP servers, new skills, or anything on the out-of-scope
list. Do NOT modify the six existing MCP servers' core logic except the one scoped checkpoint
.gitignore fix below. Read this whole spec first, then do the items in order, reporting after each.

Context: model endpoint is read from `$VLLM_BASE_URL` (currently the your inference host at
http://YOUR_TAILSCALE_IP:8001/v1, serving max_model_len 262144). Never hardcode a host. Hermes config
lives at ~/.hermes/config.yaml (back it up before editing). The repo is ~/hermes-max.

---

## FIX 1 — Confirm/repair compression.threshold (the most likely silent misconfig)
The wizard set compression.threshold to 0.5; it must be 0.75 so the agent uses three-quarters of
the 262K window before compressing (at 0.5 it throws away ~131K of working memory and loses
PLAN.md mid-project).
- `grep -A6 '^compression:' ~/.hermes/config.yaml` and report current values.
- Ensure EXACTLY these under `compression:`  → threshold: 0.75, target_ratio: 0.35,
  protect_last_n: 40, protect_first_n: 5.
- If any differ, back up the config (cp to a timestamped .bak) and set them. Re-grep to confirm.

## FIX 2 — Confirm tool_use_enforcement (judgment value)
- `grep tool_use_enforcement ~/.hermes/config.yaml` and report.
- Leave it `required` (correct default for a coding agent). Just CONFIRM and report the live
  value — do not change it. (Note in your summary: relax to `auto` later only if the agent makes
  spurious tool calls during planning.)

## FIX 3 — .gitignore for the checkpoint mechanism (touches real repos — do this right)
`mcp-checkpoint` uses `git add -A`, which would commit __pycache__, node_modules, .env, secrets,
and large build artifacts into the "last green" checkpoints. Make checkpoints clean:
- In `mcp-checkpoint/checkpoint_core.py`, in the checkpoint path, BEFORE `git add -A`: if the repo
  has no `.gitignore`, write a sensible default one (Python + Node + env + OS noise:
  `__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `node_modules/`, `.env`, `.env.*`, `*.log`,
  `.DS_Store`, `dist/`, `build/`, `*.egg-info/`). If a `.gitignore` already exists, do NOT
  overwrite it — respect the project's own.
- Keep `git add -A` (now correctly filtered by .gitignore). Do not switch to scoped adds — the
  .gitignore is the clean fix and preserves the "commit everything that matters" behavior.
- Update `mcp-checkpoint`'s standalone smoke test to assert that after a checkpoint, a created
  `__pycache__/x.pyc` is NOT in `git ls-files` (i.e. it was ignored). Run the smoke test; it must pass.
- Re-run lint (ruff) + the existing smoke test for mcp-checkpoint to confirm no regression.

## FIX 4 — Wire semantic RAG (EMBED_BASE_URL) so codebase-rag is hybrid, not BM25-only
RAG is currently BM25-only because the chat vLLM returns 404 on /embeddings. Two acceptable paths
— pick based on what's reachable, and report which you used:
- (A) PREFERRED if available: a dedicated embedding endpoint. Check whether one is reachable
  (e.g. a small embed model served on another port, or an embeddings-capable endpoint). If yes,
  set `EMBED_BASE_URL` (and `EMBED_MODEL` if needed) in ~/hermes-max/.env and
  ~/hermes-max/.env.example.
- (B) FALLBACK if no embed endpoint exists yet: do NOT fake it. Instead, make the degradation
  honest and visible — confirm `mcp-codebase-rag` already falls back to BM25 cleanly, and add a
  one-line warning to `healthcheck.sh` that prints "RAG: BM25-only (no EMBED_BASE_URL set —
  semantic retrieval disabled)" when EMBED_BASE_URL is empty. Document in README how to enable
  semantic RAG later (serve an embed model, set EMBED_BASE_URL). Do not block on this.
- After whichever path: restart mcp-codebase-rag, run its standalone smoke test, and report
  whether retrieval is now hybrid (A) or BM25-only-with-clear-warning (B).

## VALIDATION — the real test (this is what proves the harness, not the toy Flask run)
Build a REAL multi-subtask project and then a compounding-proof follow-up. Use a throwaway dir
under /tmp or ~/hermes-validation (a fresh git repo), NOT a real project.

### V1 — Real multi-file feature (≥5 files, planned)
Drive it through the long-horizon scaffolding exactly as Hermes would (you may write a small
orchestrator like scripts/longhorizon-acceptance.py did, driving the REAL mcp-verify +
mcp-checkpoint + mcp-codebase-rag over MCP). Task:
  "Build a small FastAPI task-tracker service: SQLite storage, CRUD endpoints for tasks
   (create/list/get/update/delete), a /health route, Pydantic models, and pytest tests covering
   each endpoint. At least 5 files."
Assert the acceptance bar:
- [ ] PLAN.md written BEFORE any code.
- [ ] Each subtask ended with a verified-green mcp-checkpoint commit (`git log` shows
      `[hermes-max checkpoint]` commits, each created only after mcp-verify returned green).
- [ ] No __pycache__/.pyc in `git ls-files` (FIX 3 working on a real multi-file repo).
- [ ] Any server/runtime was started backgrounded and tested ONCE with a timeout — never polled.
- [ ] codebase-rag was queried at least once during the task.
- [ ] mcp-verify green before "done"; pytest actually passes.
- [ ] Kill mcp-checkpoint mid-run once → agent degrades gracefully (keeps working, warns).

### V2 — Compounding proof (the Claude-Code-beating property)
Immediately run a SECOND, related task in the same project:
  "Add a /tasks/{id}/complete endpoint and a /stats endpoint (counts by status), with tests,
   following the existing patterns."
Assert:
- [ ] The agent retrieved prior context (codebase-rag and/or knowledge-graph) and reused the
      existing patterns/structure rather than re-deriving from scratch.
- [ ] If a knowledge-graph entry or distilled skill from V1 was used, note it explicitly.
- [ ] V2 completes with fewer exploratory steps / less thrash than V1 would from cold (report the
      qualitative or step-count difference — this is the compounding evidence).
- [ ] verify green, checkpoints clean.

### V3 — Stuck-reset under a real wall
Inject one unsatisfiable subtask (e.g. "use a library that isn't installed and isn't available")
and confirm: the agent hits the tightened guardrail (same_tool_failure:4 / idempotent_no_progress:3),
writes a STUCK SUMMARY, calls revert_to_last_green (tree restored to last green), and pings/stops
rather than thrashing past hard_stop. Report the turn count at which it stopped.

---

## REPORT (what to show me at the end)
1. FIX 1: before/after compression values (confirm threshold 0.75).
2. FIX 2: live tool_use_enforcement value.
3. FIX 3: the .gitignore diff + smoke-test proof that .pyc is ignored.
4. FIX 4: which path (A semantic / B honest-BM25-warning) and the rag smoke-test result.
5. VALIDATION: the git log of V1's green checkpoints; the V2 compounding evidence; the V3
   turn-count-at-stop. Explicitly state PASS/FAIL on each acceptance checkbox above.
6. Confirm: no new MCP servers added, nothing from the out-of-scope list built, the six servers'
   boundaries intact, and ~/.hermes/config.yaml backed up before editing.

## DEFINITION OF DONE
All four fixes applied and verified; V1 passes every checkbox; V2 demonstrably reuses prior work;
V3 cleanly stuck-resets instead of hanging. If any validation step FAILS, report the failure
honestly with the diagnostics — do NOT paper over it. A failed V2 (no compounding) or V3 (thrashes
past hard_stop) is important signal, not something to hide.