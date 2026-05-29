# mcp-verify

Deterministic verification gate: **lint → typecheck → unit tests**. An agent
cannot honestly declare "done" until this returns `passed: true`. No model
calls, no network, no randomness — never flaky.

## Tool

- `verify(path, language="auto")` → structured result:
  ```json
  {
    "path": "...", "language": "python", "passed": true,
    "stages": [
      {"name":"lint","tool":"ruff","status":"passed", "...": "..."},
      {"name":"typecheck","tool":"mypy","status":"passed", "...": "..."},
      {"name":"tests","tool":"pytest","status":"passed", "...": "..."}
    ],
    "summary": "PASS: lint(ruff), typecheck(mypy), tests(pytest)"
  }
  ```
  `passed` is `true` only when ≥1 stage ran and none failed/errored. A missing
  tool is reported as `skipped` (it neither passes nor fails the gate).

## Languages

| Language | lint | typecheck | tests |
|----------|------|-----------|-------|
| python   | ruff | ty → mypy | pytest |
| ts/js    | eslint | tsc --noEmit | vitest \| jest |
| rust     | clippy | cargo check | cargo test |

For Python it runs the tools from the **target project's** interpreter when a
`.venv`/`venv` is present (so it sees the project's deps), else its own.
Override with `VERIFY_PYTHON=/path/to/python`.

## Run / health / test

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MCP_VERIFY_PORT=9101 .venv/bin/python server.py     # streamable-http on /mcp
./healthcheck.sh                                     # GET /health
.venv/bin/python smoke_test.py                       # standalone smoke test
```

## Isolation

Independent process; shares no state. If killed, Hermes reports the `verify`
tool unavailable and the agent degrades gracefully — the harness never crashes.

## Scope (intentionally not built)

Exactly three deterministic stages. **No** 10-stage ladder (mutation / fuzz /
Lean4 / debate). Add mutation testing later only for a specific high-value repo
(Lane 3).
