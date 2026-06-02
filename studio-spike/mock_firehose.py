#!/usr/bin/env python3
"""THROWAWAY SPIKE — a deterministic SSE token firehose (Python stdlib only).

Mimics the shape ui/server/feeds.py will emit once Phase 2 lands the gen.* token
events, so the Phase 0 spike can stress the Rust→Channel→webview path at a known
rate without a live hermes run. Holds the text/event-stream open and emits:
  - gen.token        choices[0].delta.content equivalent, at SPIKE_RATE tok/s
  - gen.reasoning    occasional reasoning deltas (de-emphasized in UI)
  - conductor        periodic structured events (pass through immediately)
for SPIKE_SECONDS, then `data: [DONE]` and close.

Env: SPIKE_RATE (tok/s, default 55), SPIKE_SECONDS (default 300), PORT (7099).
Route: GET /api/events/<id>  ·  GET /healthz
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RATE = int(os.environ.get("SPIKE_RATE", "55"))
SECONDS = int(os.environ.get("SPIKE_SECONDS", "300"))
PORT = int(os.environ.get("PORT", "7099"))

_WORDS = ("the model streams tokens here while we measure whether the Tauri "
          "Channel and webkit2gtk renderer can sustain the rate without jank "
          "or dropping frames over a long run ").split()


def _frame(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        if not self.path.startswith("/api/events/"):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b"retry: 3000\n\n")

        interval = 1.0 / max(1, RATE)
        deadline = time.time() + SECONDS
        i = 0
        step = 1
        try:
            while time.time() < deadline:
                i += 1
                word = _WORDS[i % len(_WORDS)]
                self.wfile.write(_frame("gen.token", {"run_id": "spike", "text": word + " "}))
                # occasional reasoning + structured events (the mixed real stream)
                if i % 40 == 0:
                    self.wfile.write(_frame("gen.reasoning", {"run_id": "spike", "text": "considering options… "}))
                if i % 200 == 0:
                    self.wfile.write(_frame("conductor", {"run_id": "spike", "event": "step_advance",
                                                          "from_step": step, "to_step": step + 1, "step": step + 1,
                                                          "total": 7}))
                    step += 1
                self.wfile.flush()
                time.sleep(interval)
            self.wfile.write(_frame("conductor", {"run_id": "spike", "event": "run_complete",
                                                  "done": True, "total_turns": i}))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    print(f"firehose: :{PORT}  rate={RATE}/s  seconds={SECONDS}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
