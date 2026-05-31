---
name: workflow-long-running-processes
description: How to handle servers, daemons, watchers — anything that runs indefinitely.
trigger: starting any server, daemon, watcher, or long-lived process
---

<!-- TRIGGERS WHEN: How to handle servers, daemons, watchers — anything that runs indefinitely. -->
# A running server is SUCCESS, not a hang. Never poll a process that never ends.

The per-tool wall-clock timeout is short (native `terminal.timeout` ≈ 120s) precisely so a blocked
poll cannot hang forever. That backstop is only safe if you follow the **backgrounded +
check_stall-once** pattern — never block synchronously on a process that may not return.

When starting a Flask/FastAPI/uvicorn/node/webpack/any server or watcher:
- Start it backgrounded (append ` &` or use the background-process tool) and capture the PID.
- DO NOT poll it to completion. It will NEVER complete — that is its job. Polling it = infinite hang.
- Wait 2-3 seconds for startup, then TEST IT ONCE:
    - HTTP server → `curl -s -m 5 http://localhost:<port>/<known-route>` (use -m 5 timeout!)
    - check the port is listening → `ss -ltn | grep <port>`
- Then call the watchdog EXACTLY ONCE to classify it (do not loop):
  `mcp_hermes_max_watchdog_check_stall(tool_name="<server>", elapsed_s=<since start>,
  expecting_heartbeat=true, last_heartbeat_age_s=<since last log line/probe>)`.
    - `waiting: true` → it is serving/heartbeating. That is SUCCESS — leave it running, record the
      PID (`kill <pid>` later), move on. NEVER kill a heartbeating process (the false-kill trap).
    - `hung: true` → it is silent past its budget and really is stuck. Kill it and try another
      approach (go to `workflow-stuck-detect-reset`).
- If the watchdog is unavailable, fall back to the single-probe rule above: one `curl -m 5`, and if
  it responds, the subtask is DONE.
- NEVER wait more than ~10 seconds, and NEVER call check_stall in a loop.
- Any command with no natural exit (tail -f, watch, serve, dev) → same rule: start, test once,
  classify once, move on. A truly long but finite job (big build, full test suite) → background it
  too and check_stall once, rather than risking the 120s terminal timeout mid-run.
