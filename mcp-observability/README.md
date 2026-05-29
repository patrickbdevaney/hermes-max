# mcp-observability

Lightweight tracing so you can *see* unattended operation and tune it over
weeks. Emits **OpenTelemetry** spans to **Phoenix** (OTLP gRPC). No Langfuse, no
second backend.

## Tools

- `record_trace(name, attributes={}, status="ok", duration_ms=None)` — one span.
- `record_metric(name, value, unit="", attributes={})` — a numeric metric,
  modeled as a `metric:<name>` span (Phoenix is trace-first, so one pipeline).
- `record_task_metrics(task_id, tokens, duration_ms, verify_passed,
  retrieval_precision, skill_reused, escalation_usd, loop_stalled, attributes)`
  — one span carrying the standard per-task surfaces.

## Where traces go

`PHOENIX_COLLECTOR_ENDPOINT` (default `http://localhost:4317`). View them in the
Phoenix UI at **http://localhost:6006**. Start Phoenix with the repo's
`phoenix.sh`.

If Phoenix is **down**, recording still succeeds — the BatchSpanProcessor drops
spans on export without raising. The agent is never blocked by observability.

## Run / health / test

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MCP_OBSERVABILITY_PORT=9104 .venv/bin/python server.py
./healthcheck.sh                 # /health includes phoenix_reachable
.venv/bin/python smoke_test.py   # in-memory assertions + best-effort Phoenix flush
```

## Isolation

Independent process. If killed, Hermes reports the tools unavailable; nothing
else degrades. Metrics-as-spans keeps the dependency surface to a single OTLP
exporter.
