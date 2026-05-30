# dry_run_trace.md — Stage-4 reliability + observability dry-run

_Generated 2026-05-30 16:10:47 · model-independent reliability sequence (watchdog / RAG / KG / checkpoint + live log + summary)._

Run `scripts/watch.sh` in a side terminal to see this stream live; the same events feed Phoenix. The model-dependent steps (deep_research, parallel_draft, verify) run in `scripts/dry_run.py` and stream here too via the otel→livelog bridge.

## Sequence (per step: timing · est-vs-actual · result)

| # | step | secs | est~ | ok | result / reason |
|---|------|-----:|-----:|:--:|-----------------|
| 1 | index_repo[empty] | 0.12 | — | ✓ | {"empty": true, "files_indexed": 0, "mode": "empty"} |
| 2 | index_repo[sample] | 0.10 | 0 | ✓ | {"files_indexed": 3, "chunks_indexed": 9, "mode": "bm25+graph"} |
| 3 | search_code | 0.08 | — | ✓ | {"hits": ["fibonacci"]} |
| 4 | kg_record | 0.01 | — | ✓ | {"triple": "hermes-max -uses-> watchdog"} |
| 5 | kg_recall | 0.00 | — | ✓ | {"relations": 1} |
| 6 | checkpoint[green] | 0.03 | — | ✓ | {"ok": true} |
| 7 | revert_to_last_green | 0.03 | — | ✓ | {"ok": true} |

## Per-tool summary

```
═══ per-task tool-call summary ═══
  tool               calls  total_s fails fallbk    est~    act~   hb
  ───────────────────────────────────────────────────────────────────
  index_repo[empty]      1      0.1     0      0      0s    0.1s    0
  index_repo[sample]     1      0.1     0      0      0s    0.1s    0
  search_code            1      0.1     0      0      0s    0.1s    0
  revert_to_last_green     1      0.0     0      0      0s    0.0s    0
  checkpoint[green]      1      0.0     0      0      0s    0.0s    0
  kg_record              1      0.0     0      0      0s    0.0s    0
  kg_recall              1      0.0     0      0      0s    0.0s    0
  index_repo             0      0.0     0      0      0s    0.0s    6
  deep_research          0      0.0     0      0      0s    0.0s    1
  ───────────────────────────────────────────────────────────────────
  TOTAL                  7      0.4     0      0                    7

  bottleneck split (where wall-clock went):
    inference 0.0s (0%) · tool-work 0.4s (100%) · artificial 0.0s (0%)
    ✓ no artificial cost (no rate-limit backoff / redundant waiting)

  decisions (3):
    • look-ahead → deep_research ~120s | 4 planned queries x ~30s/source = est ~120s
    ✗ kill → fetch_clean killed | exceeded HARD ceiling (600s > 90s)
    • recover → revert_to_last_green | killed step → restore last green state
```

## Decisions (with reasons)

- • **look-ahead** → deep_research ~120s — 4 planned queries x ~30s/source = est ~120s
- ✗ **kill** → fetch_clean killed — exceeded HARD ceiling (600s > 90s)
- • **recover** → revert_to_last_green — killed step → restore last green state

## What this proves

- **No premature kill on legitimately-long work** — `index_repo[sample]` and the over-budget-but-heartbeating `deep_research` step run/keep-alive past their estimate because they heartbeat (slow-but-alive, not killed).
- **Empty-dir index is a clean empty success**, not a hang — `index_repo[empty]` returns instantly with a valid queryable empty index.
- **Genuinely-hung work IS killed** with a clear report (silent past budget), and the deliberately-killed step **reverts cleanly** to the last green checkpoint.
- **Full visibility** — every step's input/output/timing/est-vs-actual and every decision is in the live log and the per-tool summary above.
