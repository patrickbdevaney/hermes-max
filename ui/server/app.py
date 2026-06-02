"""The HTTP+SSE handler: routes the Tier-1 API, streams events, serves the built
React frontend, and enforces localhost hardening on every /api request.

Threaded so an open SSE stream never blocks the REST endpoints. All /api routes
require the launch token (query `?token=` for GETs incl. EventSource, `X-HMX-Token`
header for POSTs) plus a loopback Host and (when present) a loopback Origin. Static
assets are served without a token (the page carries no secret; it reads the token
from the URL the user opened and presents it on its own API calls)."""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlsplit

from . import config_api, feeds, otlp, runs, security

_UI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../ui
_DIST = os.path.join(_UI_DIR, "web", "dist")


class Handler(BaseHTTPRequestHandler):
    # Set by the server factory in __main__.py.
    launch_token: str = ""     # opt-in bearer (--token), for remote/Tailscale exposure
    csrf_token: str = ""        # per-process double-submit CSRF token (SameSite cookie)
    server_version = "hermes-max-ui/1.0"
    protocol_version = "HTTP/1.1"

    # ── tiny response helpers ─────────────────────────────────────────────────
    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_csrf_cookie()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _deny(self, status: int, msg: str) -> None:
        self._send_json({"error": msg}, status=status)

    def _send_csrf_cookie(self) -> None:
        """Set the SameSite=Strict double-submit CSRF cookie (readable by the page's
        JS — not HttpOnly — so it can echo it back as X-HMX-CSRF on POSTs)."""
        if self.csrf_token:
            self.send_header(
                "Set-Cookie",
                f"hmx_csrf={self.csrf_token}; Path=/; SameSite=Strict")

    # ── security gate for /api/* ──────────────────────────────────────────────
    def _query_token(self, query: dict) -> str | None:
        vals = query.get("token")
        return vals[0] if vals else None

    def _api_authorized(self, query: dict, *, is_post: bool) -> str | None:
        """Localhost hardening, token-free by default. Three guards:

          • Host must be a loopback authority (blocks DNS-rebinding / 0.0.0.0-day).
          • Origin (when present) must be loopback; POSTs REQUIRE a loopback Origin —
            a cross-site page can't forge one, so this is the CSRF guard.
          • A SameSite=Strict double-submit cookie (hmx_csrf) is also required on
            POSTs — a cross-site page can't read our cookie to echo it back.

        The launch token is NOT required to open the page; it only kicks in when the
        operator opts into `--token` to expose the UI beyond loopback (Tailscale)."""
        origin = self.headers.get("Origin")
        if not security.host_ok(self.headers.get("Host")):
            return "bad Host (loopback only)"
        if not security.origin_ok(origin):
            return "bad Origin (loopback only)"
        if is_post:
            if not origin:
                return "POST requires an Origin (loopback)"
            if self.csrf_token and not security.token_ok(self.headers.get("X-HMX-CSRF"), self.csrf_token):
                return "missing or invalid CSRF token"
        if self.launch_token:  # opt-in bearer for remote exposure (--token)
            token = self.headers.get("X-HMX-Token") or self._query_token(query)
            if not security.token_ok(token, self.launch_token):
                return "missing or invalid access token"
        return None

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)

        if path == "/healthz":  # tokenless readiness probe (no sensitive data)
            return self._send_json({"ok": True})

        if path.startswith("/api/"):
            err = self._api_authorized(query, is_post=False)
            if err:
                return self._deny(403, err)
            return self._route_get_api(path, query)

        # Everything else → the built frontend (SPA).
        return self._serve_static(path)

    def _route_get_api(self, path: str, query: dict) -> None:
        if path == "/api/status":
            return self._send_json(feeds.status_payload())
        if path == "/api/config":
            return self._send_json(feeds.config_payload())
        if path == "/api/cost":
            window = (query.get("window") or ["today"])[0]
            return self._send_json(feeds.cost_payload(window))
        if path == "/api/projects/recent":
            return self._send_json({"projects": runs.recent_projects()})
        if path == "/api/runs":
            # All known runs (registry ∪ in-memory) so the UI shows ANY hermes run —
            # terminal, hm dev, or launched here (Fix 4: universal SSE).
            return self._send_json({"runs": runs.list_runs()})
        if path == "/api/keys/status":
            return self._send_json(config_api.keys_status())
        if path == "/api/history":
            # Searchable run history (Phase 4) — SQLite + FTS5 over the livelog.
            from . import history
            q = (query.get("q") or [""])[0]
            status = (query.get("status") or [""])[0]
            return self._send_json({"runs": history.list_history(q, status)})
        if path.startswith("/api/history/"):
            from . import history
            run_id = unquote(path[len("/api/history/"):]).strip("/")
            detail = history.get_run(run_id)
            if detail is None:
                return self._deny(404, f"no indexed run: {run_id}")
            return self._send_json(detail)
        if path.startswith("/api/events/"):
            run_id = unquote(path[len("/api/events/"):])
            return self._stream_events(run_id)
        return self._deny(404, f"no such endpoint: {path}")

    # ── POST ────────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)

        # OTLP/HTTP trace ingest (the collector fan-out's second exporter posts here).
        # No launch token — a collector/agent can't carry it — but loopback-only.
        if path == "/v1/traces":
            return self._otlp_traces()

        if not path.startswith("/api/"):
            return self._deny(404, "not found")
        err = self._api_authorized(query, is_post=True)
        if err:
            return self._deny(403, err)

        body = self._read_json_body()
        if body is None:
            return self._deny(400, "invalid or missing JSON body")

        if path == "/api/run":
            prompt = body.get("prompt") or ""
            if not prompt.strip():
                return self._deny(400, "prompt is required")
            # A run_id in the body continues that conversation (turn 2+, same cwd);
            # otherwise start a fresh run.
            existing = body.get("run_id")
            if existing and existing != "live":
                return self._send_json(runs.continue_run(existing, prompt))
            cwd = body.get("cwd") or os.getcwd()
            mode = body.get("mode")
            return self._send_json(runs.create_run(cwd, prompt, mode))
        if path.startswith("/api/keys/"):
            provider = unquote(path[len("/api/keys/"):]).strip("/")
            if not provider:
                return self._deny(400, "provider is required")
            value = body.get("value") or body.get("key") or ""
            return self._send_json(config_api.store_key(provider, value))
        if path == "/api/config":
            return self._send_json(config_api.apply_config(body))
        if path == "/api/test-connection":
            provider = body.get("provider") or ""
            if not provider:
                return self._deny(400, "provider is required")
            return self._send_json(config_api.test_connection(provider))
        if path == "/api/browse-dir":
            # Pop the OS-native folder chooser on the local display (loopback-only,
            # CSRF-guarded like every POST). Best-effort; returns {path|cancelled|error}.
            return self._send_json(config_api.browse_dir(body.get("start")))
        return self._deny(404, f"no such endpoint: {path}")

    def _otlp_traces(self) -> None:
        """Receive an OTLP/HTTP ExportTraceServiceRequest (protobuf or JSON),
        decode + publish to the span hub, and return an OTLP success. Always 200
        on a decode error too — a non-2xx would make the collector retry-storm;
        we'd rather drop a malformed batch than back-pressure the agent."""
        if not security.host_ok(self.headers.get("Host")):
            return self._deny(403, "bad Host (loopback only)")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        try:
            spans = otlp.decode(body, self.headers.get("Content-Type", ""))
            if spans:
                otlp.HUB.publish(spans)
        except Exception:  # noqa: BLE001 - never let a bad batch break ingest
            pass
        resp = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        try:
            self.wfile.write(resp)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode())
        except (ValueError, OSError):
            return None

    # ── SSE stream ───────────────────────────────────────────────────────────
    def _stream_events(self, run_id: str) -> None:
        run = runs.get_run(run_id)
        if run is None:
            return self._deny(404, f"unknown run: {run_id}")
        # Replay-on-reconnect (Phase 4.5): the browser echoes the last `id:` it saw
        # as Last-Event-ID. Our ids are livelog byte offsets, so resume = seek there
        # rather than replaying from the run's start. Guarded so it never seeks
        # before the run began.
        leid = self.headers.get("Last-Event-ID")
        if leid and leid.isdigit():
            run = {**run, "start_offset": max(int(run.get("start_offset", 0)), int(leid))}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # disable any proxy buffering
        self.end_headers()
        try:
            self.wfile.write(b"retry: 3000\n\n")  # client auto-reconnect backoff
            for frame in feeds.stream_events(run):
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client navigated away / closed the EventSource — normal
        except OSError:
            pass

    # ── static SPA serving (path-traversal safe) ──────────────────────────────
    def _serve_static(self, url_path: str) -> None:
        rel = posixpath.normpath(unquote(url_path)).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        target = os.path.normpath(os.path.join(_DIST, rel))
        # Containment check — never escape the dist directory.
        if not (target == _DIST or target.startswith(_DIST + os.sep)):
            return self._deny(403, "forbidden")
        if not os.path.isfile(target):
            # SPA fallback: unknown non-asset paths render index.html.
            index = os.path.join(_DIST, "index.html")
            if os.path.isfile(index):
                target = index
            else:
                return self._send_json({
                    "error": "frontend not built",
                    "hint": "cd ui/web && npm install && npm run build",
                }, status=503)
        try:
            with open(target, "rb") as f:
                data = f.read()
        except OSError:
            return self._deny(404, "not found")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # index.html must never be cached (it embeds nothing secret, but we want
        # fresh asset hashes); hashed assets can cache forever.
        if target.endswith("index.html"):
            self.send_header("Cache-Control", "no-store")
            self._send_csrf_cookie()  # seed the CSRF cookie on page load
        else:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # Quiet the default per-request stderr logging (keep the console calm).
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if os.environ.get("HMX_UI_ACCESS_LOG"):
            super().log_message(fmt, *args)
