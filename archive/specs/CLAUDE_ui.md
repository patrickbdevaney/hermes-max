# CLAUDE_ui.md — hermes-max Hybrid UI: Web View → Wizard → Visualization → Tauri Desktop

## What this builds

A user interface layer on top of the hermes-max CLI harness, delivered as a **hybrid**: the `hm`
CLI serves a localhost web frontend (opened in the browser), and that SAME frontend is later
wrapped in a **Tauri** desktop app for a one-click installable binary. One frontend codebase,
two delivery surfaces. The UI serves three audiences at once through layered progressive
disclosure (L0 ambient / L1 timeline / L2 raw detail).

Decided up front (do not relitigate):
- **Tauri, not Electron.** Smaller bundle, OS WebView, Rust backend, deny-by-default security.
- **No WASM, no Electron, no separate TUI framework.** The existing `hm dev` tmux cockpit is the
  terminal-native view; do not build a Textual/Ratatui TUI.
- **The web view and the Tauri app render the SAME frontend.** Build the frontend once; serve it
  from `hm` for the web tier and wrap it in Tauri for the desktop tier. The desktop app is a
  packaging tier, not a rewrite.
- **Build order is web-first.** The localhost web view is Tier 1 because it reuses the existing
  livelog + cost ledger with zero new instrumentation and zero packaging overhead. Tauri is a
  later tier. Do NOT build the desktop app first.

Hard constraints carried from the rest of the project:
- **Local-first, sovereign, key-safe.** Runs on the user's machine; data (code, keys) never leaves
  it. Secrets live in the OS keychain, never in browser storage, never logged, never transmitted.
- **Consume existing telemetry, don't reinstrument.** The UI's data sources are the existing
  livelog stream, the cost ledger ($0.000000), OTel/Phoenix spans, bucket status, and trajectory
  capture. Tap these; do not add a parallel telemetry system.
- **Degrade gracefully.** Token streaming may be off (local vLLM); the UI must show meaningful
  progress from discrete events alone. The UI is read-only in v1 — it visualizes, it does not
  control the agent (control is a later, explicit addition).

=================================================================================================
## THE FRONTEND STACK (decide once, used by every tier)
=================================================================================================

- **Framework:** a single-page app in a mainstream framework with a strong component ecosystem
  (React or Svelte — pick one and commit; React has the larger component/charting ecosystem,
  Svelte the smaller bundle). Use TypeScript.
- **Styling:** a utility-first system (Tailwind) + a small component layer; follow an Apple-HIG-
  adjacent calm aesthetic (see motion rules below).
- **Charts/diagrams:** a mature charting lib for the cost/timeline visuals; a lightweight
  tree/graph renderer for the L2 span tree.
- **Transport client:** an `EventSource` (SSE) consumer with auto-reconnect; a typed event model
  shared with the backend.
- **The frontend is backend-agnostic:** it talks to a small local HTTP+SSE API (served by `hm` in
  the web tier, by the Tauri Rust backend in the desktop tier) via the SAME contract. Define that
  contract once (Section: API Contract) so both backends satisfy it.

=================================================================================================
## API CONTRACT (the seam both backends implement)
=================================================================================================

Define one HTTP+SSE contract that both the `hm` web server and the Tauri Rust backend implement,
so the frontend is identical across tiers. Endpoints (all bound to 127.0.0.1):

```
GET  /api/status            → { mode, providers:[{name,present,reachable}], roster:[...],
                                today_spend_usd, free_rpd_remaining:{...}, gpu_present }
GET  /api/config            → current non-secret config (mode, endpoints, profile)
POST /api/config            → write non-secret config (mode, VLLM_BASE_URL, etc.)
POST /api/keys/{provider}   → store a provider key in the OS KEYCHAIN (never returned, never logged)
GET  /api/keys/status       → per-provider { present:bool } ONLY (never the secret)
POST /api/test-connection   → probe an endpoint/key live → { ok, latency_ms, model }
GET  /api/projects/recent   → recent working directories
POST /api/run               → { cwd, prompt, mode } → launches the agent in cwd; returns run_id
GET  /api/events/{run_id}   → SSE stream of typed events (the live visualization feed)
GET  /api/cost              → ledger rollup (today/week/month, by provider/model/role, free-vs-paid)
```

The SSE event model (one channel, typed events — handles both token streams and discrete events):

```
event types:
  token        { run_id, span_id, text }            # streamed generation (when available)
  phase        { run_id, phase, status }             # plan | execute | verify | research | done
  plan         { run_id, items:[{id,text,status}] }  # the PLAN.md contract as a checklist
  plan_item    { run_id, id, status }                # an item advancing
  tool_call    { run_id, tool, server, input_summary, status, latency_ms, result_summary }
  file_op      { run_id, op, path, diff_summary }    # created | modified | deleted
  shell        { run_id, cmd, stream_chunk, exit_code }
  gate         { run_id, kind, status, detail }      # verify | property | metamorphic ; pass|fail
  checkpoint   { run_id, label, commit }             # verified-green git checkpoint
  escalation   { run_id, from_rung, to_rung, reason } # 429 fall, Opus fire, re-plan
  cost         { run_id, delta_usd, total_usd, provider, free }  # live spend tick
  narration    { run_id, plain_text }                # plain-language step narration (L0)
  heartbeat    {}                                    # every 15-30s; keep SSE alive through idle
```

The frontend renders token events incrementally and discrete events as cards. When token events
never arrive (streaming off), the timeline still animates from phase/tool_call/file_op/gate events
— the design MUST NOT depend on token streaming.

=================================================================================================
## THE THREE-LAYER VISUALIZATION (serves all three audiences from one view)
=================================================================================================

- **L0 — ambient / glanceable** (non-technical viewer + executive). One plain-language line
  ("Writing the rate limiter — step 4 of 7"), a determinate progress bar against the PLAN.md
  contract, live cost ($0.000000), and a calm "alive" pulse. No jargon, no spans, no tool names.
  Driven by `narration` + `plan`/`plan_item` + `cost` + `phase` events.
- **L1 — structured timeline** (operator). A vertical timeline of phases and steps with status,
  timing, tool-call cards (request → latency → result), the cheapest-first escalation ladder
  (LSP → repair → steer → re-plan → frontier) shown as a visible decision path, file-op badges,
  gate pass/fail, checkpoints as commit markers, and the live cost + free-vs-paid + remaining
  free RPD. This is the home screen.
- **L2 — full detail** (developer). Expand any L1 row to the raw span tree: exact tool I/O,
  full diffs, reasoning traces, shell output with exit codes, span attributes. This is the
  Phoenix/LangSmith-style tree, embedded.

Movement: expand/collapse an L1 row to drop into L2; a "follow the action" toggle auto-scrolls to
the current step (the focus indicator); turning it off lets the user scrub back through history
without losing the live tail. Never one averaged view — always glanceable-top-drilling-to-raw.

Per-step visual treatments (implement each):
- **Plan:** render PLAN.md as a contract checklist; items light up as worked/done; DONE_CONDITION
  is the finish line of the L0 progress bar.
- **Executor code-gen:** streamed syntax-highlighted code with the diff forming live when tokens
  stream; a "writing `file.py`…" skeleton card resolving to the completed diff when they don't.
- **Deep research fan-out:** parallel source cards appearing across providers (Groq/Cerebras/
  OpenRouter), citations accumulating, converging into a synthesis node — show breadth then
  convergence.
- **Tool calls:** request-latency-result cards; summarize bursts (collapse "10 tool calls" into one
  expandable card, never stream ten raw lines).
- **File ops:** created/modified/deleted badges, inline diffs, a working-directory tree that updates.
- **Shell:** streamed output + exit codes; a long-running process with steady output reads as
  HEALTHY ("server started" ≠ hang), not as a stall.
- **Verification gates:** red/green; cannot-declare-done-on-red is a hard visual gate; verified-
  green checkpoints are commit markers on the timeline.
- **Escalations:** animate a rung falling on 429 to the next provider, an Opus escalation firing —
  so the operator sees WHY cost moved.
- **Cost:** glanceable live `$0.000000`, free-vs-paid split, remaining free RPD — always in the chrome.

Motion & aesthetic (the "satisfying and elegant" requirement, done right):
- Determinate progress against the plan for the minutes-long run — NOT indefinite spinners
  (Nielsen's >10s rule: spinners are wrong for long waits).
- Brief, purposeful motion ONLY on meaningful state changes (a rung falling, a gate turning green,
  a checkpoint landing). Do not animate every token or every span.
- Calm aesthetic (steady "alive" pulse), not anxious (frantic spinners).
- Respect `prefers-reduced-motion` (crossfade instead of slide; never convey state by motion
  alone — always pair with color + icon + label). WCAG 4.5:1 contrast; never color-only signaling.

=================================================================================================
## SECRET HANDLING (non-negotiable — the biggest risk if done wrong)
=================================================================================================

- Keys (DeepSeek/DeepInfra/OpenRouter/Groq/Cerebras/Anthropic) are stored in the **OS keychain**
  (macOS Keychain, libsecret/gnome-keyring on Linux, Windows Credential Manager) via a cross-
  platform wrapper. In the Tauri tier, use a Tauri keychain/stronghold plugin; in the web tier,
  the `hm` backend uses the system keyring library.
- Gitignored `.env` with `chmod 600` is the fallback ONLY where no keychain exists (headless
  Linux/WSL2). The backend sets the permission and ensures `.env` is gitignored.
- The key is POSTed once to the local backend (`POST /api/keys/{provider}`) and immediately written
  to the keychain. It is **never** returned by any endpoint, **never** held in JS state, **never**
  in localStorage/sessionStorage/cookies, **never** logged, **never** in livelog/trajectory output
  (scrub/redact on capture), **never** transmitted anywhere except the provider endpoint.
- The UI only ever shows masked status: "✓ DeepSeek key configured" via `GET /api/keys/status`
  which returns `{present:bool}` only.
- **Localhost hardening (required even though it's local):** bind to 127.0.0.1 (never 0.0.0.0);
  require a one-time launch token that `hm` prints / embeds in the opened URL and the Tauri app
  passes internally; CSRF synchronizer token on all POSTs; validate `Origin` and `Host` headers
  (block DNS-rebinding and the 0.0.0.0-day class); SameSite=Strict cookies. Document this in the
  security section of the UI README.

=================================================================================================
## TRANSPORT & TELEMETRY TAP
=================================================================================================

- **Transport: SSE** (read-only server→browser), single channel of the typed events above.
  Heartbeat every 15-30s so idle gaps (agent thinking) don't trip timeouts. Auto-reconnect via
  `EventSource`. WebSocket is NOT needed in v1 (read-only); note it as the upgrade path IF/WHEN
  mid-run control (cancel/approve/steer) is added later.
- **Tap the existing signals — do not reinstrument:**
  - **livelog + cost ledger (Tier 1 source):** the `hm` backend tails the existing lib/livelog
    stream and the ledger and forwards them as `tool_call` / `cost` / `phase` events. This alone
    powers L0 + L1. Zero new instrumentation.
  - **OTel/Phoenix spans (Tier 3 source for L2):** add an OpenTelemetry Collector fan-out — one
    OTLP receiver, two exporters: Phoenix (unchanged, for deep inspection) AND a tiny custom
    OTLP/HTTP receiver in the `hm` backend (listening on the OTLP HTTP path, decoding the trace
    export protobuf) that rebroadcasts each span over SSE for the L2 tree. The fan-out means each
    exporter gets a copy; Phoenix is unaffected.
  - **Latency note (the live-feel lever):** the agent's OTel SDK default BatchSpanProcessor buffers
    ~5000ms. For the live UI, the agent should use a fast span processor in UI mode (SimpleSpan
    Processor, or BSP with ~200ms delay) so spans reach the UI promptly. Spans emit on span-END, so
    in-progress activity MUST come from the livelog/event path, not only spans — that's why Tier 1
    is livelog-driven and spans are an L2 enrichment.
- **Graceful degradation:** when token streaming is off, drive L0/L1 entirely from discrete
  phase/tool_call/file_op/gate/cost events. The timeline still animates; only token-by-token typing
  is lost. Plain-language narration works equally from discrete events.

=================================================================================================
## BUILD ORDER — FOUR TIERS
=================================================================================================

### TIER 1 — Minimum lovable web view (build FIRST)
`hm ui` (or `hm dev --web`) starts a localhost HTTP+SSE server and opens the browser. It serves
the frontend rendering **L0 ambient + L1 timeline + live cost**, fed by the **existing livelog +
ledger** over SSE. No keychain wizard yet (uses existing .env), no L2 span tree yet, no Tauri.
- Implement the API contract endpoints needed: `/api/status`, `/api/cost`, `/api/run`,
  `/api/events/{run_id}` (livelog+ledger-fed), `/api/projects/recent`.
- Implement the frontend L0 + L1 with the per-step treatments and motion rules.
- Localhost hardening (127.0.0.1, launch token, CSRF, Origin/Host checks) from day one.
- **Tier-1 DoD:** `hm ui` serves a browser view that shows a live run's plan progress, timeline,
  tool-call cards, file ops, gates, checkpoints, and live $0.000000 cost, driven by the existing
  livelog+ledger, degrading cleanly when token streaming is off. Localhost surface hardened.

### TIER 2 — Onboarding wizard
Add the first-run config surface to the same web frontend.
- Profile detect/recommend (GPU vs no-GPU) by probing hardware + VLLM_BASE_URL reachability.
- Per-provider key capture → `POST /api/keys/{provider}` → OS keychain (full secret discipline).
- Test-connection step (`POST /api/test-connection`) → green/red + latency + model inline.
- Working-config summary card (profile, endpoints green, cost tier).
- Directory picker (+ recent projects / drag-drop) → project prompt → `POST /api/run` in that cwd.
- "What's about to happen" expectation card (actions, rough time, cost tier, live-spend promise)
  before kickoff — especially for the non-technical user.
- **Tier-2 DoD:** a new user goes clone → `hm ui` → wizard (profile, keys to keychain, test-
  connection, pick dir, enter prompt) → running agent, without editing files by hand. Keys are in
  the keychain, never in browser storage or logs.

### TIER 3 — Full layered-disclosure visualization
Add **L2** and the streaming polish to the same frontend.
- OTel Collector fan-out + OTLP→SSE bridge in the `hm` backend → the L2 span tree (raw tool I/O,
  full diffs, reasoning traces, span attributes), expandable from any L1 row.
- Token-by-token streaming with tool-call interleaving (when the backend streams).
- Deep-research fan-out visualization (breadth → convergence), escalation-ladder animation,
  diff rendering, the "follow the action" focus toggle + scrub-back.
- Plain-language narration layer (L0) mapping event types → plain verbs ("Searching the web for…",
  "Writing the rate limiter", "Running the tests"); never hide failure/cost/stuck-ness behind it.
- **Tier-3 DoD:** any L1 row expands to the raw span tree; token streaming renders live with
  interleaved tool calls when available; deep-research fan-out, escalations, and diffs are
  visualized; the non-technical narration layer reads as a story without hiding failures.

### TIER 4 — Tauri desktop app (build from source)
Wrap the SAME frontend in a Tauri shell. This is packaging, not a rewrite. For now the app is
built from source by the user — clone, chmod +x, run the build script. No signing, no CI
distribution, no packaged binary, no GitHub Release artifacts. That is a future concern.

- Tauri 2.x project wrapping the existing frontend; the Rust backend implements the SAME API
  contract (or proxies to the `hm` engine) so the frontend is byte-identical to the web tier.
- Rust backend uses Tauri's capability system (deny-by-default), a keychain/stronghold plugin for
  secrets, and `Command`/sidecar to launch the hermes agent in the chosen cwd. Directory picker
  uses the native OS dialog.
- Provide a single build script `build-desktop.sh`:

  ```bash
  #!/usr/bin/env bash
  # hermes-max desktop app — build from source
  # Prerequisites: Rust (rustup), Node/npm, system WebKit (on Ubuntu: libwebkit2gtk-4.1-dev
  #   + libgtk-3-dev + libayatana-appindicator3-dev + librsvn2-dev — script checks and tells you)
  set -e
  scripts/check-tauri-deps.sh      # prints missing system packages + install command, exits if any
  cd ui/tauri
  npm install
  npm run tauri build              # produces target/release/bundle/
  echo "Built: $(ls src-tauri/target/release/hermes-max)"
  echo "Run:   ./src-tauri/target/release/hermes-max"
  ```

  `chmod +x build-desktop.sh` and it is the entire user-facing build surface.

- `scripts/check-tauri-deps.sh` checks for the required Ubuntu system packages
  (libwebkit2gtk-4.1-dev, libgtk-3-dev, libayatana-appindicator3-dev, librsvg2-dev, pkg-config,
  build-essential, curl) and prints the exact `apt install` command for anything missing —
  so the user never encounters a silent build failure from a missing package.
- Document in `docs/desktop.md`: prerequisites (Rust via rustup, Node, apt packages),
  the two commands (`chmod +x build-desktop.sh && ./build-desktop.sh`), and how to run the
  resulting binary. No mention of signing, notarization, or distribution — those are
  explicitly deferred.
- **Tier-4 DoD:** `./build-desktop.sh` on a clean Ubuntu install (with prerequisites satisfied
  per the dependency check) produces a runnable binary at the documented path; the binary launches
  the full UI with OS keychain secret storage and native directory picker; the frontend is the
  same code as the web tier. No signing, no packaging, no CI distribution.

=================================================================================================
## DEFINITION OF DONE (all tiers)
=================================================================================================
- One TypeScript frontend (React or Svelte, committed) renders L0/L1/L2 and is served BOTH by `hm`
  (web tier) and wrapped by Tauri (desktop tier) against ONE API contract.
- Tier 1 web view is live off the existing livelog+ledger with hardened localhost surface.
- Tier 2 wizard takes a new user clone → running-on-a-project with keychain-stored secrets.
- Tier 3 adds the L2 span tree (OTel fan-out + OTLP→SSE bridge), token streaming, fan-out/
  escalation/diff visualization, and the plain-language narration layer.
- Tier 4: `./build-desktop.sh` on Ubuntu produces a runnable Tauri binary; same frontend as the
  web tier; OS keychain secrets; native directory picker; no signing, no CI distribution.
- Secrets: OS keychain only; never in browser storage, logs, livelog, trajectories, or any
  committed file; UI shows masked status only.
- Transport: SSE single typed-event channel; heartbeat; degrades to discrete-event progress when
  token streaming is off; WebSocket noted as the future control-channel upgrade.
- Motion respects prefers-reduced-motion and never signals by motion/color alone.
- No WASM, no Electron, no separate TUI framework (the tmux `hm dev` cockpit remains the terminal
  view).

## Note on Tier 4 scope
Tier 4 is build-from-source on Ubuntu only. Signing, notarization, packaging, and distribution
are explicitly out of scope for now. The web view (Tier 1) is the primary delivery surface and
gives ~90% of the value; the desktop build is an opt-in for users who want an OS-native window.