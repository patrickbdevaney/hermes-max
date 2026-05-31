---
name: skill-process-gotchas
description: Common-sense traps a fast small model misses. Consult when a command behaves oddly.
trigger: any command that hangs, errors unexpectedly, or behaves contrary to expectation
---

<!-- TRIGGERS WHEN: Common-sense traps a fast small model misses. -->
# Things that are "obvious" but easy to get wrong. Check here before thrashing.

- LONG-RUNNING PROCESS never exits → don't wait for it (see workflow-long-running-processes).
- INTERACTIVE PROMPT (apt, npm init, ssh host-key, pip on conflict) hangs waiting for input →
  use non-interactive flags (-y, --yes, --no-input, DEBIAN_FRONTEND=noninteractive) or echo input.
- WRONG WORKING DIR: each terminal tool call may reset cwd. Always cd into the project, or use
  absolute paths. If a file "doesn't exist," check `pwd` first.
- VENV NOT ACTIVE: `pip install` then `python` can use different environments. Use the venv's
  python explicitly, or activate in the same command chain with &&.
- PORT ALREADY IN USE: a failed prior run left a server bound. `ss -ltn | grep <port>`,
  kill the old PID before restarting.
- TEST THAT NEEDS A RUNNING SERVER: start server backgrounded FIRST, then run the test, then
  kill the server. Don't run the test against nothing.
- A COMMAND THAT PRINTS NOTHING may have succeeded (many unix tools are silent on success).
  Check the exit code ($?), don't assume silence = failure and retry.
- BUILD/INSTALL IS SLOW not hung: npm install, cargo build, pip compile can take minutes. Give
  them a real timeout (300s+), don't kill at 30s and retry.
- INFINITE RETRY: if the same command failed twice identically, the THIRD identical attempt
  will also fail. Change something or invoke workflow-stuck-detect-reset.
