# mcp-checkpoint — verified-green git checkpointing

A thin MCP server that wraps `git` so that **checkpoint** and **revert** are
first-class tools the long-horizon skills call — and, crucially, so that a
checkpoint is *only ever created from a verified-green state*.

- Transport: streamable-http on `127.0.0.1:$MCP_CHECKPOINT_PORT` (default **9106**), path `/mcp`.
- Health: `GET /health`.
- Own venv, own port, own healthcheck, standalone smoke test — like every other
  hermes-max server. Killing it degrades the agent gracefully (it keeps working,
  it just can't checkpoint/revert); it never takes Hermes down.

## Why git commits (not Hermes native snapshots)
Clean rollback, durable, readable history — and the stuck-reset's *"revert to
last green"* maps exactly onto `git reset --hard` to the last verified commit.

## Tools
| Tool | What it does |
|---|---|
| `checkpoint(label, verify=True, repo_path=cwd, init=False)` | If `verify`, asks **mcp-verify** first and **refuses on RED** (the green invariant). Then `git add -A` + `git commit -m "[hermes-max checkpoint] <label>"`, tags the ref, returns the SHA. No-op (returns last SHA) when nothing changed. |
| `revert_to_last_green(repo_path=cwd)` | `git stash -u` any dirty tree (nothing is lost), then `git reset --hard` to the last `[hermes-max checkpoint]` commit. The stuck-reset recovery primitive. |
| `list_checkpoints(n=10, repo_path=cwd)` | Recent checkpoints: SHA, label, time. |
| `checkpoint_status(repo_path=cwd)` | Branch, dirty/clean, last-green SHA, commits-ahead. |

## Safety discipline
- Operates on the **project repo at the caller's cwd** (or an explicit
  `repo_path`), never on the hermes-max repo, `$HOME`, or `/`.
- Never force-pushes, never touches remotes. Local working-tree commits only.
- If **mcp-verify** is unreachable, `checkpoint(verify=True)` degrades to an
  *unverified* commit with a loud warning in the return value — graceful
  degradation, never a crash.

## Run it
```bash
../scripts/lib.sh           # (sourced by the scripts; nothing to run directly)
python smoke_test.py        # standalone: boots a throwaway verify, proves the invariants
MCP_CHECKPOINT_PORT=9106 python server.py
curl -s localhost:9106/health
```
The model never hardcodes a host: the verify boundary is read from
`$MCP_VERIFY_PORT` / `$MCP_BIND_HOST`, and the whole system ports to the your inference host by
flipping `$VLLM_BASE_URL` alone.
