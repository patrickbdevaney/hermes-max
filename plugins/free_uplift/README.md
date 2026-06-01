# free_uplift (optional plugin)

A proactive coherence checkpoint: after a file passes verify, spend **one
Kimi-K2.6:free** call to confirm the implementation matches its FILE SPEC and the
already-completed interfaces. Catches drift the local executor missed, at $0.

**This is a plugin, not core.** `conductor_policy.py`, `mcp-escalation`, and
`mcp-research` have zero knowledge of it. It registers against the conductor's one
generic post-verify hook (`register_post_verify_hook`) — or it doesn't.

## Dependencies (all required for it to register)

1. `INFERENCE_MODE_FREE_UPLIFT=true` (set it with `hm up --free-uplift`).
2. `OPENROUTER_API_KEY` present.
3. A **one-time $10 OpenRouter deposit** so `:free` models get 1000 req/day (without
   it you have ~50/day — too little to also run uplift). Credits never expire.
4. `moonshotai/kimi-k2.6:free` live (the roster check; not in `KNOWN_DEPRECATED`).
5. Daily free-RPD headroom above `FREE_UPLIFT_MIN_RPD` (default 200).

If any is false, `load_plugins` logs `free_uplift: not registered (...)` and the
core loop runs exactly as before.

## Caps (never burn the budget)

- ≤ 2 calls per file, ≤ 10 per task (`FREE_UPLIFT_MAX_PER_FILE` / `_PER_TASK`).
- Skips silently when the rate bucket is tight (never absorbs a 429).
- Never blocks the loop on error — a failed call is treated as CLEAN.

## Toggle & visibility

- `hm up --free-uplift` / `hm up --no-free-uplift` — persist the toggle in `.env`.
- `hm mode` shows `[free-uplift: ON/OFF]`.
- `hm cost` shows a dedicated `free_uplift` line (calls + tokens + `$0.000000`).

## Files

- `policy.py` — ALL uplift logic; the only file that names the Kimi slot for this.
- `SKILL.md` — `checkpoint_review`, the agent-facing trigger.
- `README.md` — this file.

## When Kimi-K2.6:free is deprecated

`hm health` flags it; the plugin's `should_register` returns False → it stops
registering. Update the id in `inference.yaml` (one line) and it registers again on
the next `hm up`. No other file changes.
