# studio-spike — Phase 0 throwaway (CLAUDE_studio_v2.md)

**Throwaway de-risking spike. Delete after the gate passes — do not build on it.**

It proves the three things the entire v2 architecture rests on, before any
production Rust is written:

1. **Rust is the sole SSE consumer** (no CORS in Rust). `sse_probe/` opens the
   loopback `text/event-stream`, stays connected, and parses every typed event
   (`gen.token` / `gen.reasoning` / `conductor` …).
2. **A Tauri Channel sustains the token rate** (not `emit`). `app/` coalesces
   tokens to ~50 Hz and pushes them over a `Channel` to a webview that
   self-measures frame jank.
3. **A custom protocol serves a single-origin `index.html`** (`hermes://localhost/`)
   — a single buffered response (NOT stream proxying, which is impossible on
   Linux).

## Run the gate (on a real Ubuntu desktop)

```bash
./run-gate.sh            # 300s @ 60 tok/s — the 5-minute gate
./run-gate.sh 60 70      # quick 60s @ 70 tok/s smoke
```

A window opens (loaded via the custom protocol). **Watch it.** PASS criteria:

- tokens stream smoothly; `long frames(>50ms)` stays at/near **0**;
- no crash over the full duration;
- the printed verdict has `"ok": true`.

If the Channel can't sustain the rate cleanly (jank, climbing `long_frames`, a
crash) → **STOP and report**; the architecture needs rethinking before Phase 1.

## What was verified where

| Item | How | Status |
|---|---|---|
| 1 — Rust SSE consumer + typed parse | `sse_probe` headless | **PASS** (25s, 1395 events @ 55.8 Hz, max gap 18ms, no drops) |
| 2 — Channel throughput / no jank | `app` GUI, self-measured | **must run on real hardware** — the CI sandbox kills GPU/bg-server processes (exit 144), so it can't be driven headlessly there |
| 3 — custom-protocol asset serving | `app` window loads `hermes://localhost/` | compiles; validated together with item 2 on real hardware |

## Files
- `mock_firehose.py` — stdlib SSE token firehose (deterministic load; no hermes needed).
- `sse_probe/` — headless Rust SSE proof (item 1).
- `app/` — Tauri spike: Channel + custom protocol + self-measuring webview (items 2+3).
- `run-gate.sh` — orchestrates the gate.
