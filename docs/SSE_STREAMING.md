# SSE streaming: from hermes to the UI

How the agent's live activity — tool calls, conductor events, and the model's
token stream — reaches the two user interfaces (the web UI and Hermes Studio).

## The spine: one append-only livelog

Everything starts as a line in a single JSONL file:

```
~/.hermes-max/logs/live.jsonl
```

`lib/livelog.py` is the only writer. Each record is one event:

```json
{"ts": 1780399708.95, "hms": "07:28:28", "kind": "span", "span": "gen.token", "text": "Hello "}
```

Producers append to it:
- **hermes / the agent** — tool start/end, file writes, shell, decisions.
- **the conductor plugin** (`plugins/conductor`) — `conductor.*` orchestration
  spans (llm_call, verify_pass/fail, trigger, guidance, step_advance,
  run_complete) and the model's **token deltas** (`gen.token` / `gen.reasoning` /
  `gen.thinking`), written by the AIAgent stream callbacks it installs on
  `pre_llm_call` and detaches on `post_llm_call`.

The log is **offset-addressable**: a "run" is just a byte offset into this file,
so a stream can resume from where a client left off.

## The translator: `ui/server/feeds.py` → one typed SSE channel

The Python backend (stdlib `http.server`, zero pip deps) tails the livelog from a
run's start offset and translates each JSONL record into a typed Server-Sent
Event over **one** channel, discriminated by the SSE `event:` name:

```
GET /api/events/{run_id}        →  text/event-stream

event: gen.token
data: {"run_id":"…","text":"Hello "}

event: conductor
data: {"run_id":"…","event":"step_advance","step":2,"total":5}
```

`feeds._translate()` maps livelog `kind`/`span` → SSE event. `conductor.*` and
`gen.*` pass through nearly verbatim; tool/file/verify/checkpoint spans map to
typed `tool_call`/`file_op`/`gate`/`checkpoint` events. Each frame carries an
`id:` line equal to the livelog byte offset, so a reconnecting client sends
`Last-Event-ID` and resumes from there.

Hardening (loopback-only): the Host must be loopback; browser POSTs need a
loopback `Origin` + a SameSite CSRF cookie; Hermes Studio's Rust control plane
instead presents a per-launch secret (`X-HMX-Secret`) — see below.

## Consumer A — the web UI (browser, same origin)

`ui/web` is served by the same Python backend, so it's same-origin and can open
the stream directly:

```
EventSource("/api/events/{run_id}")   // lib/events.ts; auto-reconnects
   → lib/feed.ts (a pure, capped reducer)
   → the feed / conductor swimlane / flow / chrome views
```

`lib/feed.ts` folds the event stream into a memory-bounded view (a 500-item
circular buffer). Token deltas (`gen.token`) grow **one live item per turn** —
the "typing" effect — finalized when the next structural event arrives.
EventSource reconnection + `Last-Event-ID` give replay-on-reconnect for free.

## Consumer B — Hermes Studio (Tauri desktop, different origin)

The Studio shell runs at a `tauri://` origin, so it **cannot** read the stream
directly: Linux webkit2gtk sends a blank `Origin` (wry #366) and cross-origin
`EventSource` is CORS-blocked, and Tauri custom protocols can't hold a streaming
response open. So **Rust is the sole consumer**:

```
Rust (src-tauri/stream.rs, ureq + rustls — no CORS)
   opens GET /api/events/{run_id}, parses the full typed stream
   coalesces gen.* token deltas to ~50 Hz, structured events pass immediately
   → a Tauri Channel (ordered, high-throughput; NOT the event system)
      → the shell renders via the SAME ui/web feed.ts reducer + components
```

Control flows the other way as Rust → loopback `POST` (carrying the per-launch
secret), never from the webview. The result: the desktop renders identically to
the web UI because it reuses the same reducer and components — they can't diverge.

## The whole path

```
hermes / conductor ──append──► live.jsonl ──tail+translate──► /api/events/{id} (SSE)
                                                                  │
                              same-origin EventSource ───────────┤──► web UI  (feed.ts)
                              Rust ureq → Tauri Channel ──────────┘──► Studio  (feed.ts)
```

One spine, one typed channel, one reducer — two surfaces.

## Verify it live

```bash
tail -f ~/.hermes-max/logs/live.jsonl | grep gen.token   # the source
# open the run in the web UI or `hm studio` — tokens stream into the feed
```

## Touch points

| Concern | File |
|---|---|
| Livelog writer | `lib/livelog.py` |
| Token callbacks → gen.* spans | `plugins/conductor/__init__.py` |
| JSONL → typed SSE + offset ids + auth | `ui/server/feeds.py`, `ui/server/app.py` |
| Browser consumer + reducer | `ui/web/src/lib/events.ts`, `ui/web/src/lib/feed.ts` |
| Desktop consumer (Rust → Channel) | `studio/src-tauri/src/stream.rs` |
