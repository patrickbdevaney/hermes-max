# mcp-escalation

A thin router that lets the agent escalate a genuinely-hard, well-scoped
subproblem to a cheap cloud tier — while the default stays **$0 local grinding**.

## Two guarantees enforced in the server (not in a prompt)

1. **OFF by default.** `ESCALATION_ENABLED` must be exactly `true` to route
   anything. Otherwise `escalate` returns `{"disabled": true}`.
2. **Hard daily USD cap.** Spend is tracked in `ESCALATION_STATE_PATH` and reset
   daily. Once today's spend ≥ `ESCALATION_DAILY_USD_CAP`, `escalate` refuses.
   A per-call `ESCALATION_MAX_TOKENS` bounds any single call so it can't blow
   the cap in one shot.

## Tool

- `escalate(task, tier="cheap")` → routes to an OpenAI-compatible cheap-frontier
  endpoint and returns `{content, usage, cost_usd, spend_today_usd,
  daily_cap_usd}`. Returns a `disabled` / `cap_reached` / `error` marker
  (never raises) so callers always fall back to local work cleanly.

## Tiers (from env; available only if base_url is set)

- `cheap` → `ESCALATION_BASE_URL` / `ESCALATION_API_KEY` / `ESCALATION_MODEL`
  (e.g. DeepSeek V4 Flash, default prices $0.14/$0.28 per 1M).
- `long` → `ESCALATION_LONG_BASE_URL` / ... (e.g. Kimi K2.6 for long-horizon-hard).

**Tier-3 (Opus / Claude Code) is rejected by design** — those tier names error
out — to avoid auth collisions with the laptop's separate Claude Code.

## Run / health / test

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MCP_ESCALATION_PORT=9105 .venv/bin/python server.py     # OFF unless ESCALATION_ENABLED=true
./healthcheck.sh                                         # /health shows enabled + spend vs cap
.venv/bin/python smoke_test.py                           # stubbed endpoint: routing + cap + rejection
```

## Isolation

Independent process. If killed, Hermes reports the tool unavailable and the
agent stays on the free local model — the default anyway.
