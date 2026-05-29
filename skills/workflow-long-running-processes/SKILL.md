---
name: workflow-long-running-processes
description: How to handle servers, daemons, watchers — anything that runs indefinitely.
trigger: starting any server, daemon, watcher, or long-lived process
---
# A running server is SUCCESS, not a hang. Never poll a process that never ends.

When starting a Flask/FastAPI/uvicorn/node/webpack/any server or watcher:
- Start it backgrounded (append ` &` or use the background-process tool) and capture the PID.
- DO NOT poll it to completion. It will NEVER complete — that is its job. Polling it = infinite hang.
- Instead: wait 2-3 seconds for startup, then TEST IT ONCE:
    - HTTP server → `curl -s -m 5 http://localhost:<port>/<known-route>` (use -m 5 timeout!)
    - check the port is listening → `ss -ltn | grep <port>`
- If it responds correctly, the subtask is DONE. Record the PID so it can be stopped later
  (`kill <pid>`). Report success.
- NEVER wait more than ~10 seconds on a process meant to run forever.
- If you started a server and it is "still running" — that is the success condition, not a problem.
- Any command with no natural exit (tail -f, watch, serve, dev) → same rule: start, test once, move on.
