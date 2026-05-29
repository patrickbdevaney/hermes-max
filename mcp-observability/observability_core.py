"""OpenTelemetry tracing that emits to Phoenix (OTLP gRPC on :4317).

This is the "is it actually working" layer: per-task token/time, retrieval
precision, skill-reuse rate, verify pass-rate, escalation spend and loop-stall
events, modeled as OTel spans with attributes and shipped to the Phoenix UI.

Phoenix is trace-first, so metrics are modeled as spans too (named `metric:*`),
which keeps the pipeline to ONE exporter and zero extra infra. If Phoenix is
down, the BatchSpanProcessor drops spans silently — recording never raises and
the agent is never blocked.

No Langfuse (per build context). No second backend.
"""

from __future__ import annotations

import os
import socket
from typing import Any
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

PHOENIX_ENDPOINT = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:4317")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "hermes-max")

_provider: TracerProvider | None = None
_tracer: trace.Tracer | None = None
_exporter_ok = False


def _init() -> None:
    global _provider, _tracer, _exporter_ok
    if _provider is not None:
        return
    _provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        insecure = PHOENIX_ENDPOINT.startswith("http://")
        exporter = OTLPSpanExporter(endpoint=PHOENIX_ENDPOINT, insecure=insecure)
        _provider.add_span_processor(BatchSpanProcessor(exporter))
        _exporter_ok = True
    except Exception:  # noqa: BLE001 - never let a bad exporter crash the server
        _exporter_ok = False
    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer("hermes-max-observability")


def enable_inmemory() -> Any:
    """Attach an in-memory exporter (used by the smoke test) and return it."""
    _init()
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    mem = InMemorySpanExporter()
    assert _provider is not None
    _provider.add_span_processor(SimpleSpanProcessor(mem))
    return mem


def force_flush(timeout_millis: int = 5000) -> bool:
    _init()
    assert _provider is not None
    return _provider.force_flush(timeout_millis=timeout_millis)


def _coerce(attributes: dict | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (attributes or {}).items():
        if v is None:
            continue
        out[str(k)] = v if isinstance(v, (str, bool, int, float)) else str(v)
    return out


def _emit(name: str, attributes: dict, status: str) -> dict[str, Any]:
    _init()
    assert _tracer is not None
    with _tracer.start_as_current_span(name) as span:
        for k, v in _coerce(attributes).items():
            span.set_attribute(k, v)
        span.set_status(Status(StatusCode.ERROR if status == "error" else StatusCode.OK))
        ctx = span.get_span_context()
        return {
            "ok": True,
            "name": name,
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
            "exported_to": PHOENIX_ENDPOINT if _exporter_ok else None,
        }


def record_trace(name: str, attributes: dict | None = None, status: str = "ok",
                 duration_ms: float | None = None) -> dict[str, Any]:
    attrs = dict(attributes or {})
    if duration_ms is not None:
        attrs["duration_ms"] = duration_ms
    return _emit(name, attrs, status)


def record_metric(name: str, value: float, unit: str = "", attributes: dict | None = None) -> dict[str, Any]:
    attrs = dict(attributes or {})
    attrs["value"] = value
    if unit:
        attrs["unit"] = unit
    return _emit(f"metric:{name}", attrs, "ok")


def record_task_metrics(
    task_id: str,
    tokens: int | None = None,
    duration_ms: float | None = None,
    verify_passed: bool | None = None,
    retrieval_precision: float | None = None,
    skill_reused: bool | None = None,
    escalation_usd: float | None = None,
    loop_stalled: bool | None = None,
    attributes: dict | None = None,
) -> dict[str, Any]:
    """Emit one span capturing the standard per-task observability surfaces."""
    attrs = dict(attributes or {})
    attrs.update({
        "task_id": task_id,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "verify_passed": verify_passed,
        "retrieval_precision": retrieval_precision,
        "skill_reused": skill_reused,
        "escalation_usd": escalation_usd,
        "loop_stalled": loop_stalled,
    })
    return _emit(f"task:{task_id}", attrs, "error" if verify_passed is False else "ok")


def phoenix_reachable() -> bool:
    try:
        u = urlparse(PHOENIX_ENDPOINT)
        host = u.hostname or "localhost"
        port = u.port or 4317
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


def status() -> dict[str, Any]:
    _init()  # ensure the exporter is set up so the reported state is accurate
    return {
        "service_name": SERVICE_NAME,
        "phoenix_endpoint": PHOENIX_ENDPOINT,
        "exporter_configured": _exporter_ok,
        "phoenix_reachable": phoenix_reachable(),
    }
