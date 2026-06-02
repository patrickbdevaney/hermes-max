
Launch (three terminals)
Terminal 1 — cockpit:
bashcd ~/hermes-max
hm dev
Watch for: planner cascade firing, escalation calls, verify gate, checkpoints.
Terminal 2 — browser UI:
bashhm ui
# auto-opens localhost:7080
Terminal 3 — run a task:
bashmkdir ~/eval-<name> && cd ~/eval-<name>
hm run "<your prompt here>"
hm run handles everything:

Conductor plans (Kimi:free → V4-Pro fallback) → signs PLAN.md
Hermes receives execution instruction, never the raw task
Executor fills in the plan, calls escalation when stuck
Verify gate hard-blocks on red
Checkpoint on green


The capability eval prompt
Paste this as the hm run argument to test the full
planner → executor → verify → escalation loop:
bashhm run "Build a zero-dependency Python implementation of a \
persistent append-only event log with the following properties:

Core:
- Events are arbitrary JSON-serializable dicts with a monotonic
  sequence number, wall-clock timestamp, and SHA-256 content hash
- Append is atomic (no partial writes visible to readers)
- The log survives process crashes — on restart it replays from
  the last valid entry, detecting and truncating any partial
  write at the tail
- A consumer can tail the log from any sequence number with
  configurable poll interval

Compaction:
- A snapshot mechanism that captures current materialized state
  (a user-supplied reducer function applied to all events)
- On restart, if a snapshot exists, replay only events after
  the snapshot sequence number

Correctness guarantees (enforced by the test suite):
- Property: no event lost between append and a crash simulated
  by os.kill(os.getpid(), SIGKILL) via subprocess
- Property: sequence numbers strictly monotonic under concurrent
  appenders (threading + asyncio mixed)
- Property: snapshot + partial replay == full replay from genesis
- Fuzz: random append/snapshot/crash/restart cycles converge

Benchmark: append throughput (sync + async) and tail-consumer
lag at 10k events.

Done when: all properties pass 500 Hypothesis trials, crash test
passes, benchmark prints a table. stdlib only — no SQLite, no Redis."

What to watch
Cockpit (hm dev):
✓ conductor_plan   kimi:free → V4-Pro   PLAN.md signed        ~30s
✓ file_write       event_log.py         +180 lines
✓ uplift·ask       concurrency invariant  nemotron:free        ~8s
✓ file_write       test_event_log.py    +120 lines
✓ verify           pytest 24 pass · hypothesis 500 trials      ok
◆ checkpoint       verified-green @ <hash>
Read PLAN.md immediately after it appears:
bashcat ~/eval-<name>/PLAN.md
A good plan has specific types, named invariants, and a precise
DONE_CONDITION. That document is proof the planner was instrumental.
Browser (localhost:7080):
The run appears in the Run view within ~1s. L0 shows plain-language
progress. L1 shows the timeline. Cost ticks live in the chrome.

Pass criteria
The eval passes if:

PLAN.md has the conductor signature header
pytest passes with zero failures
hypothesis finds no counterexample after 500 trials
crash test kills mid-write and restarts clean
benchmark prints a table
total cost < $0.10

If all five are true: the thesis is proven in production.
Strong cloud planner + local executor + frontier escalation when
stuck = near-Opus output for pennies.

Quick reference
bashhm mode free-full-local   # kimi:free → V4-Pro fallback, local exec
hm mode full-local        # V4-Pro always, local exec (~$1-3/mo)
hm mode free              # full free cascade, $0 hard
hm status                 # show mode, providers, cost, rungs
hm cost                   # ledger breakdown
hm mode --list            # all seven modes