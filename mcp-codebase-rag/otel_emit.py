"""Tiny fire-and-forget OTel span emitter to Phoenix (OTLP gRPC on :4317).

Every hermes-max server that wants stuck/recovery events visible in Phoenix
imports this and calls `record(name, attrs, status)`. It mirrors the exporter
wiring in mcp-observability/observability_core.py but is self-contained so each
server stays an INDEPENDENT process (no cross-server runtime dependency).

If Phoenix is down the BatchSpanProcessor drops spans silently and `record`
returns `{"ok": False, "exported": False}` — it NEVER raises and never blocks
the agent. Graceful degradation is the whole point.
"""

from __future__ import annotations

import os
from typing import Any

PHOENIX_ENDPOINT = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:4317")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "hermes-max")

_tracer: Any = None
_exporter_ok = False
_init_done = False


def _init() -> None:
    global _tracer, _exporter_ok, _init_done
    if _init_done:
        return
    _init_done = True
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            insecure = PHOENIX_ENDPOINT.startswith("http://")
            exporter = OTLPSpanExporter(endpoint=PHOENIX_ENDPOINT, insecure=insecure)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            _exporter_ok = True
        except Exception:  # noqa: BLE001 - a bad exporter must never crash the server
            _exporter_ok = False
        # Do not call set_tracer_provider globally (avoid clobbering); use a local tracer.
        _tracer = provider.get_tracer("hermes-max-rag")
    except Exception:  # noqa: BLE001 - opentelemetry not installed -> degrade to no-op
        _tracer = None
        _exporter_ok = False


def record(name: str, attributes: dict | None = None, status: str = "ok") -> dict[str, Any]:
    """Emit one span. Never raises. Returns {ok, exported}."""
    try:
        _init()
        if _tracer is None:
            return {"ok": False, "exported": False, "reason": "otel unavailable"}
        from opentelemetry.trace import Status, StatusCode

        with _tracer.start_as_current_span(name) as span:
            for k, v in (attributes or {}).items():
                if v is None:
                    continue
                span.set_attribute(str(k), v if isinstance(v, (str, bool, int, float)) else str(v))
            span.set_status(Status(StatusCode.ERROR if status == "error" else StatusCode.OK))
        return {"ok": True, "exported": _exporter_ok}
    except Exception:  # noqa: BLE001 - observability is best-effort, always
        return {"ok": False, "exported": False, "reason": "emit failed"}
