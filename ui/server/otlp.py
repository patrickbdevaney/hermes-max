"""OTLP/HTTP → normalized-span decoder + a pub/sub hub, for the L2 span tree.

The agent and MCP servers already emit OpenTelemetry spans (to Phoenix on :4317).
CLAUDE_ui.md's Tier-3 design is a Collector FAN-OUT: one OTLP receiver, two
exporters — Phoenix (unchanged) AND this backend's OTLP/HTTP receiver — so each
gets its own copy and Phoenix is unaffected. This module is the receiver: it
decodes an OTLP `ExportTraceServiceRequest` (protobuf OR JSON, whichever the
collector's `otlphttp` exporter is configured to send), normalizes each span to a
flat JSON shape, and publishes it to subscribers (the SSE generators) plus a ring
buffer so a late-connecting client still gets the recent tree.

Zero dependencies: the protobuf path is decoded with a ~40-line stdlib wire-format
reader (the OTLP trace schema field numbers are stable and encoded below), so we
never need the opentelemetry-proto package. The JSON path is the OTLP/JSON
encoding (snake_case or camelCase keys both handled).

Spans are NOT secret-bearing beyond what already flows to Phoenix; the receive
endpoint is loopback-only (a collector/agent posts to it, never the browser).
"""
from __future__ import annotations

import json
import threading
from collections import deque
from queue import Full, Queue
from typing import Any, Iterable, Optional

# ── minimal protobuf wire reader ──────────────────────────────────────────────
def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def _parse_fields(buf: bytes) -> dict[int, list[tuple[int, Any]]]:
    """Parse one protobuf message into {field_number: [(wire_type, value), ...]}.
    value is an int (varint/fixed) or bytes (length-delimited)."""
    out: dict[int, list[tuple[int, Any]]] = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        fnum, wt = key >> 3, key & 0x7
        if wt == 0:
            val, i = _read_varint(buf, i)
        elif wt == 1:                       # 64-bit
            val = int.from_bytes(buf[i:i + 8], "little"); i += 8
        elif wt == 2:                       # length-delimited
            ln, i = _read_varint(buf, i)
            val = buf[i:i + ln]; i += ln
        elif wt == 5:                       # 32-bit
            val = int.from_bytes(buf[i:i + 4], "little"); i += 4
        else:                               # 3/4 = deprecated groups
            raise ValueError(f"unsupported wire type {wt}")
        out.setdefault(fnum, []).append((wt, val))
    return out


def _first(fields: dict[int, list[tuple[int, Any]]], fnum: int) -> Optional[Any]:
    v = fields.get(fnum)
    return v[0][1] if v else None


def _all(fields: dict[int, list[tuple[int, Any]]], fnum: int) -> list[Any]:
    return [v for _, v in fields.get(fnum, [])]


def _s(b: Any) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b)


def _hex(b: Any) -> str:
    return b.hex() if isinstance(b, (bytes, bytearray)) else str(b)


# AnyValue: 1=string 2=bool 3=int 4=double 5=array 6=kvlist 7=bytes
def _any_value(buf: bytes) -> Any:
    f = _parse_fields(buf)
    if 1 in f:
        return _s(_first(f, 1))
    if 2 in f:
        return bool(_first(f, 2))
    if 3 in f:
        v = _first(f, 3)
        # int64 varint: reinterpret as signed
        return v - (1 << 64) if v >= (1 << 63) else v
    if 4 in f:
        import struct
        return struct.unpack("<d", int(_first(f, 4)).to_bytes(8, "little"))[0]
    if 5 in f:                              # ArrayValue { repeated AnyValue values=1 }
        arr = _parse_fields(_first(f, 5))
        return [_any_value(v) for v in _all(arr, 1)]
    if 6 in f:                              # KeyValueList { repeated KeyValue values=1 }
        kvl = _parse_fields(_first(f, 6))
        return _key_values(_all(kvl, 1))
    if 7 in f:
        return _hex(_first(f, 7))
    return None


def _key_values(kv_bufs: Iterable[bytes]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kvb in kv_bufs:
        kv = _parse_fields(kvb)
        key = _s(_first(kv, 1)) if 1 in kv else ""
        val = _any_value(_first(kv, 2)) if 2 in kv else None
        if key:
            out[key] = val
    return out


_STATUS_CODE = {0: "unset", 1: "ok", 2: "error"}


def _decode_span(buf: bytes, resource: dict, scope: str) -> dict[str, Any]:
    f = _parse_fields(buf)
    start = int(_first(f, 7) or 0)
    end = int(_first(f, 8) or 0)
    status_code, status_msg = "unset", ""
    if 15 in f:
        st = _parse_fields(_first(f, 15))
        status_code = _STATUS_CODE.get(int(_first(st, 3) or 0), "unset")
        status_msg = _s(_first(st, 2)) if 2 in st else ""
    events = []
    for evb in _all(f, 11):
        ev = _parse_fields(evb)
        events.append({
            "name": _s(_first(ev, 2)) if 2 in ev else "",
            "time_ns": int(_first(ev, 1) or 0),
            "attributes": _key_values(_all(ev, 3)),
        })
    return _normalize(
        trace_id=_hex(_first(f, 1)), span_id=_hex(_first(f, 2)),
        parent_span_id=_hex(_first(f, 4)) if 4 in f else "",
        name=_s(_first(f, 5)) if 5 in f else "", kind=int(_first(f, 6) or 0),
        start_ns=start, end_ns=end, status_code=status_code, status_msg=status_msg,
        attributes=_key_values(_all(f, 9)), events=events,
        resource=resource, scope=scope,
    )


def _decode_protobuf(body: bytes) -> list[dict[str, Any]]:
    """ExportTraceServiceRequest{ repeated ResourceSpans resource_spans=1 }."""
    spans: list[dict[str, Any]] = []
    req = _parse_fields(body)
    for rs_buf in _all(req, 1):                         # ResourceSpans
        rs = _parse_fields(rs_buf)
        resource: dict[str, Any] = {}
        if 1 in rs:                                     # Resource{ attributes=1 }
            resource = _key_values(_all(_parse_fields(_first(rs, 1)), 1))
        for ss_buf in _all(rs, 2):                      # ScopeSpans
            ss = _parse_fields(ss_buf)
            scope = ""
            if 1 in ss:                                 # InstrumentationScope{ name=1 }
                scope = _s(_first(_parse_fields(_first(ss, 1)), 1) or b"")
            for sp_buf in _all(ss, 2):                  # Span
                spans.append(_decode_span(sp_buf, resource, scope))
    return spans


# ── OTLP/JSON path (snake_case or camelCase) ──────────────────────────────────
def _g(d: dict, *names: str, default=None):
    for n in names:
        if n in d:
            return d[n]
    return default


def _json_any(v: dict) -> Any:
    if not isinstance(v, dict):
        return v
    if "stringValue" in v or "string_value" in v:
        return _g(v, "stringValue", "string_value")
    if "boolValue" in v or "bool_value" in v:
        return bool(_g(v, "boolValue", "bool_value"))
    if "intValue" in v or "int_value" in v:
        iv = _g(v, "intValue", "int_value")
        return int(iv) if isinstance(iv, str) else iv
    if "doubleValue" in v or "double_value" in v:
        return _g(v, "doubleValue", "double_value")
    if "arrayValue" in v or "array_value" in v:
        arr = _g(v, "arrayValue", "array_value") or {}
        return [_json_any(x) for x in (arr.get("values") or [])]
    if "kvlistValue" in v or "kvlist_value" in v:
        kvl = _g(v, "kvlistValue", "kvlist_value") or {}
        return _json_kvs(kvl.get("values") or [])
    if "bytesValue" in v or "bytes_value" in v:
        return _g(v, "bytesValue", "bytes_value")
    return None


def _json_kvs(items: list) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kv in items or []:
        k = kv.get("key")
        if k:
            out[k] = _json_any(kv.get("value") or {})
    return out


def _decode_json(body: bytes) -> list[dict[str, Any]]:
    doc = json.loads(body.decode("utf-8", "replace"))
    spans: list[dict[str, Any]] = []
    for rs in _g(doc, "resourceSpans", "resource_spans", default=[]) or []:
        resource = _json_kvs((_g(rs, "resource", default={}) or {}).get("attributes") or [])
        for ss in _g(rs, "scopeSpans", "scope_spans", default=[]) or []:
            scope = ((_g(ss, "scope", default={}) or {}).get("name")) or ""
            for sp in ss.get("spans") or []:
                st = _g(sp, "status", default={}) or {}
                code = st.get("code")
                code = {0: "unset", 1: "ok", 2: "error",
                        "STATUS_CODE_UNSET": "unset", "STATUS_CODE_OK": "ok",
                        "STATUS_CODE_ERROR": "error"}.get(code, "unset")
                events = [{
                    "name": e.get("name", ""),
                    "time_ns": int(_g(e, "timeUnixNano", "time_unix_nano", default=0) or 0),
                    "attributes": _json_kvs(e.get("attributes") or []),
                } for e in (sp.get("events") or [])]
                spans.append(_normalize(
                    trace_id=_g(sp, "traceId", "trace_id", default=""),
                    span_id=_g(sp, "spanId", "span_id", default=""),
                    parent_span_id=_g(sp, "parentSpanId", "parent_span_id", default=""),
                    name=sp.get("name", ""), kind=sp.get("kind", 0),
                    start_ns=int(_g(sp, "startTimeUnixNano", "start_time_unix_nano", default=0) or 0),
                    end_ns=int(_g(sp, "endTimeUnixNano", "end_time_unix_nano", default=0) or 0),
                    status_code=code, status_msg=st.get("message", ""),
                    attributes=_json_kvs(sp.get("attributes") or []),
                    events=events, resource=resource, scope=scope))
    return spans


# ── normalization (one shape for both encodings) ──────────────────────────────
def _normalize(*, trace_id, span_id, parent_span_id, name, kind, start_ns, end_ns,
               status_code, status_msg, attributes, events, resource, scope) -> dict[str, Any]:
    dur_ms = round((end_ns - start_ns) / 1e6, 3) if end_ns and start_ns else None
    return {
        "trace_id": trace_id, "span_id": span_id,
        "parent_span_id": parent_span_id or "",
        "name": name, "kind": kind,
        "start_ns": start_ns, "end_ns": end_ns, "duration_ms": dur_ms,
        "status": {"code": status_code, "message": status_msg},
        "attributes": attributes, "events": events,
        "service": (resource or {}).get("service.name", ""), "scope": scope,
    }


def decode(body: bytes, content_type: str) -> list[dict[str, Any]]:
    """Decode an OTLP ExportTraceServiceRequest body to normalized spans. Picks
    JSON vs protobuf by content-type, with a sniff fallback (JSON starts with '{')."""
    ct = (content_type or "").lower()
    if "json" in ct:
        return _decode_json(body)
    if "protobuf" in ct or "x-protobuf" in ct:
        return _decode_protobuf(body)
    return _decode_json(body) if body[:1] == b"{" else _decode_protobuf(body)


# ── pub/sub hub ───────────────────────────────────────────────────────────────
class SpanHub:
    """A bounded ring buffer of recent spans + live subscribers. Thread-safe.
    Publishing never blocks: a slow subscriber's queue drops (it still has the
    ring buffer to backfill from on (re)connect)."""

    def __init__(self, cap: int = 4000):
        self._lock = threading.Lock()
        self._ring: deque[dict[str, Any]] = deque(maxlen=cap)
        self._subs: set[Queue] = set()

    def publish(self, spans: list[dict[str, Any]]) -> None:
        with self._lock:
            subs = list(self._subs)
            for s in spans:
                self._ring.append(s)
        for s in spans:
            for q in subs:
                try:
                    q.put_nowait(s)
                except Full:
                    pass

    def subscribe(self, maxsize: int = 1000) -> Queue:
        q: Queue = Queue(maxsize=maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def recent(self, since_unix_s: Optional[float] = None) -> list[dict[str, Any]]:
        """Snapshot of buffered spans, optionally only those that ended at/after a
        wall-clock second (so a run only backfills its own spans)."""
        with self._lock:
            spans = list(self._ring)
        if since_unix_s is None:
            return spans
        cutoff_ns = since_unix_s * 1e9
        return [s for s in spans if (s.get("end_ns") or s.get("start_ns") or 0) >= cutoff_ns]


HUB = SpanHub()
