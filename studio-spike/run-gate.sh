#!/usr/bin/env bash
# Phase 0 GATE — run on a REAL Ubuntu desktop (webkit2gtk-4.1, a live X/Wayland
# session). This is the throwaway spike from CLAUDE_studio_v2.md Phase 0. It must
# pass before any production Studio v2 code is written.
#
# What it proves:
#   1. Rust reads the loopback SSE stream and parses typed events (headless).
#   2. A Tauri CHANNEL sustains the token rate with NO jank (a GUI window opens;
#      watch it — `long_frames(>50ms)` should stay ~0 over the run).
#   3. A custom protocol (hermes://localhost/) serves the single-origin index.html
#      (the window you see IS loaded through it).
#
# Usage:
#   ./run-gate.sh [seconds] [tok/s]      # default 300s @ 60 tok/s (the 5-min gate)
#
# Point at a REAL run instead of the mock firehose (once Phase 2 emits gen.* —
# until then the mock is the load source):
#   SPIKE_SSE_URL=http://127.0.0.1:7080/api/events/<run_id> ./run-gate.sh 300
set -euo pipefail
cd "$(dirname "$0")"
SECS="${1:-300}"; RATE="${2:-60}"

echo "▸ building spike (one-time)…"
( cd app && cargo build -q )
( cd sse_probe && cargo build -q --release )

USE_MOCK=1; [ -n "${SPIKE_SSE_URL:-}" ] && USE_MOCK=0
if [ "$USE_MOCK" = 1 ]; then
  export SPIKE_SSE_URL="http://127.0.0.1:7099/api/events/spike"
  echo "▸ starting mock firehose (${RATE} tok/s, $((SECS+30))s)…"
  SPIKE_SECONDS=$((SECS+30)) SPIKE_RATE="$RATE" PORT=7099 python3 mock_firehose.py >/tmp/hmx_spike_firehose.log 2>&1 &
  FH=$!; trap 'kill $FH 2>/dev/null || true' EXIT
  curl -s --retry 20 --retry-connrefused --retry-delay 1 "http://127.0.0.1:7099/healthz" >/dev/null && echo "  firehose up"
fi

echo "▸ [item 1] headless Rust SSE proof (10s sample)…"
SPIKE_SECONDS_SAMPLE=10 timeout 14 ./sse_probe/target/release/sse_probe || true

echo "▸ [items 2+3] opening the GUI gate window for ${SECS}s — WATCH IT."
echo "  PASS if: tokens stream smoothly, long_frames stays ~0, no crash, the"
echo "  verdict prints ok:true at the end."
SPIKE_GATE_SECONDS="$SECS" ./app/target/debug/spike-app

echo "=== VERDICT (/tmp/spike_verdict.json) ==="
cat /tmp/spike_verdict.json 2>/dev/null || echo "(no verdict — the window likely crashed; gate FAILED)"
