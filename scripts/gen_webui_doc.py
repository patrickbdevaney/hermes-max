#!/usr/bin/env python3
"""Generate docs/WEBUI.md — a single self-contained reference for the whole hermes-max
web UI: an architectural narrative + every source file's real contents (read from disk,
so the code is always exact) preceded by a per-file explanation. Run from the repo root:
    python3 scripts/gen_webui_doc.py
"""
from __future__ import annotations
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "archive", "docs", "WEBUI.md")

LANG = {".tsx": "tsx", ".ts": "ts", ".js": "js", ".py": "python",
        ".css": "css", ".html": "html", ".json": "json"}

# Per-file architectural note (authored). Keyed by repo-relative path.
NOTE: dict[str, str] = {
    # ── build & entry ──
    "ui/web/index.html":
        "The Vite HTML entry: a single `#root` div and a module `<script>` loading "
        "`main.tsx`. It carries **no** secret or config — it is a static asset served "
        "without a token; any launch token is read from the URL the user opened, at runtime.",
    "ui/web/package.json":
        "The dependency manifest. Deliberately minimal: **React + React-DOM are the only "
        "runtime deps** — no component kit, no state library, no graph/virtualization "
        "library. Everything (router, virtual list, flow graph, popovers) is hand-rolled, "
        "so the bundle stays ~67 KB gzipped. `build` = `tsc -b && vite build` (type-check "
        "gates the build).",
    "ui/web/vite.config.ts":
        "Vite config: the React plugin plus the dev-server proxy so `npm run dev` forwards "
        "`/api` and `/v1` to the running Python backend. The production build emits to "
        "`web/dist`, which the Python server serves directly (so there is no separate web "
        "host in production).",
    "ui/web/tailwind.config.js":
        "The design system expressed as tokens (PART II). Remaps the semantic color names "
        "the components use — `ink` (backgrounds 950→600), `mist` (text 100→600), `accent`, "
        "and `good`/`warn`/`bad` (status) — onto the exact premium palette, with ≤4px radii, "
        "Inter/Geist fonts, and the keyframes (`pulse2`, `risein`, `flash`, and the "
        "marching-ants `dash` used by the Flow graph's active edge).",
    "ui/web/postcss.config.js":
        "Wires Tailwind + autoprefixer into the PostCSS pipeline Vite runs over `index.css`.",
    "ui/web/tsconfig.json":
        "Strict TypeScript config for `src/` (DOM + ESNext libs, `strict`, bundler module "
        "resolution). `tsc -b` runs it before every build, so a type error fails the build.",
    "ui/web/tsconfig.node.json":
        "The companion TS project for the build tooling itself (the Vite config file).",
    "ui/web/src/main.tsx":
        "The React bootstrap: mount `<App/>` into `#root` in StrictMode. The app is one SPA "
        "with hash-based routing, so this is the only DOM mount point.",
    "ui/web/src/index.css":
        "Tailwind layers + the raw CSS custom properties for the palette, base typography, "
        "scrollbar styling, and the `prefers-reduced-motion` guard. State is always conveyed "
        "by **color + icon + label**, so freezing animation under reduced-motion loses no "
        "meaning.",

    # ── app shell & routing ──
    "ui/web/src/App.tsx":
        "**The shell and single source of truth.** It owns the two reducers — the turn-based "
        "`view` (`state.ts`) and the Part-2 `feed` (`feed.ts`) — opens exactly **one** SSE "
        "stream per active run, and fans every frame to BOTH: immediately to the turn "
        "reducer, and **buffered + flushed every `BATCH_FLUSH_MS` (100ms)** to the feed "
        "reducer (the batching that keeps a fast run from thrashing React or growing the "
        "heap). It polls `/api/status` (mode/providers/driver/spend) and `/api/runs` (live-run "
        "discovery) on intervals, routes first-run users to Setup, and exposes the "
        "launch/continue/new-run actuation. Because state is lifted here, a run keeps "
        "streaming in the background while you visit other tabs.",
    "ui/web/src/lib/router.ts":
        "A near-zero-dep **hash router**: parses `location.hash` into a typed `Route` "
        "(`#/run`, `#/run/:id`, `#/activity`, `#/providers`, `#/cost`, `#/setup`) and exposes "
        "`useRoute()` + `navigate()`. Hash-based so deep links, the back button, and "
        "bookmarks all work with no server route table.",
    "ui/web/src/lib/runjournal.ts":
        "A `localStorage`-backed run history: records `{run_id, prompt, cwd, mode, start_ts}` "
        "and turn bumps, so a deep-linked run still shows what was asked and Activity can "
        "replay it. Stores **no secrets** — only working directories and prompts.",

    # ── types & data layer ──
    "ui/web/src/types.ts":
        "**The contract shared with the backend** (`ui/server/feeds.py`). The discriminated "
        "`EventType` union and every event payload interface (Token/Phase/Plan/ToolCall/"
        "FileOp/Shell/Gate/Checkpoint/Escalation/Cost/Narration/Heartbeat/Span and the "
        "Part-2 `ConductorEvt`), plus the REST payload shapes (StatusPayload, KeysStatus, "
        "CostReport, RunSummary, DriverStatus, normalized Span) and the reduced L1 "
        "`TimelineEntry`. Keeping an event shape in sync here and in `feeds.py` is the only "
        "coordination the two halves need.",
    "ui/web/src/lib/api.ts":
        "The typed REST client — the one module that knows the endpoint URLs and the auth "
        "dance: it attaches the launch token (query for GETs incl. EventSource, "
        "`X-HMX-Token` for POSTs) and the CSRF double-submit header, builds the EventSource "
        "URL, and wraps status / keys / run / continue / setMode / test-connection / "
        "browse-dir / recent / runs / cost.",
    "ui/web/src/lib/events.ts":
        "The **SSE consumer**. Opens an `EventSource`, registers a listener per event type "
        "(including the Part-2 `conductor` event), surfaces a typed `onEvent` + a connection "
        "state (connecting/live/reconnecting), and **caps reconnection at "
        "`MAX_SSE_RECONNECTS=10`** (reset on a successful open) so a dead backend can't spin "
        "the browser forever.",
    "ui/web/src/lib/token.ts":
        "Reads the one-time launch token from the opened URL and preserves it across hash "
        "navigations so EventSource and API calls keep authenticating. The token lives only "
        "in JS memory — never a cookie — so it can't be replayed cross-site.",
    "ui/web/src/lib/modes.ts":
        "The static catalog of the six fabric postures (free / free-full-local / full-local "
        "/ …) with their human taglines, used by the mode switcher and the launcher copy.",

    # ── state reducers ──
    "ui/web/src/state.ts":
        "**The turn-based reducer.** Folds the SSE stream into a list of conversational "
        "TURNS (user message → the agent's working flow → an explicit handback). Each turn "
        "owns its own timeline/plan/narration; cost and the raw OTLP span tree are "
        "run-global. Pure and framework-agnostic (trivially testable, reusable in Tauri). "
        "Also holds the L1/L2 selectors — plan progress, research fan-out grouping, and the "
        "span-tree walk.",
    "ui/web/src/lib/feed.ts":
        "**The Part-2 reducer — the heart of the world-class Run view.** One pure function "
        "folds the SAME stream into THREE memory-bounded states: a flat typed `FeedItem[]` "
        "(the virtualized feed), a derived `FlowState` (the step chain + conductor nodes the "
        "graph draws), and `ChromeMetrics` (the HUD: step/turns/cost and live tok-s via an "
        "EWMA over `llm_response` token deltas). Every structure is hard-capped "
        "(`MAX_FEED_ITEMS=500` circular buffer, `MAX_GRAPH_NODES=200`) and ingestion is "
        "**batched** (`BATCH_FLUSH_MS=100`), so an arbitrarily long run has constant memory. "
        "This is where the conductor plugin's `conductor.*` events become feed rows and flow "
        "nodes.",

    # ── chrome & nav ──
    "ui/web/src/components/SideNav.tsx":
        "The persistent left rail: the five surfaces (Run / Activity / Providers / Cost / "
        "Setup) with a live-run dot driven by the `/api/runs` poll.",
    "ui/web/src/components/TopChrome.tsx":
        "The top bar: the **driver chip** (local/remote/cloud/none from `status.driver`, "
        "never hardcoded), a named connection-state dot, the mode-switcher and rung "
        "popovers, and calm cost (the active run's live cost, else today's ledger total).",
    "ui/web/src/components/ui.tsx":
        "Shared primitives — `Badge`, `Dot` (with optional pulse), and small layout atoms — "
        "so tone/pulse semantics are defined once and reused everywhere.",
    "ui/web/src/components/Popover.tsx":
        "A click-outside-dismissable popover, used by the mode and rung switchers.",

    # ── run view ──
    "ui/web/src/components/RunPage.tsx":
        "**The Run surface.** With no active run it is the launcher (cwd + Browse + composer "
        "+ driver/mode/cost hints). With a run it renders the persistent `RunChrome` HUD "
        "above three tabs — **Feed** (the virtualized event feed, default), **Flow** (the SVG "
        "graph), and **Turns** (the original conversation with its timeline/graph lens) — "
        "plus the bottom `Composer` that actuates the agent (launch, then continue the same "
        "conversation over one stream).",
    "ui/web/src/components/run/Composer.tsx":
        "The input box that actuates the agent: Enter-to-send, disabled while the agent is "
        "working, and on the first message it carries the chosen cwd.",
    "ui/web/src/components/run/VirtualFeed.tsx":
        "**Fix A — the memory-safe virtualized feed.** A single tall spacer establishes the "
        "scrollbar; only the rows in (and just around) the viewport are absolutely "
        "positioned into the DOM, so node count is **constant** regardless of buffer size. "
        "Auto-scroll sticks to the tail unless the user scrolls up (then a *jump to latest* "
        "affordance appears). Fixed row height is the invariant that lets a scroll offset map "
        "to a slice without measuring. No `react-window` dependency.",
    "ui/web/src/components/run/FlowGraph.tsx":
        "**Fix B — the n8n/ComfyUI-style Flow view**, pure SVG (+`foreignObject` for HTML "
        "node bodies): a sticky **PLAN** head, a vertical **step chain** colored by status "
        "(pending/active/complete/failed), **conductor nodes** that branch to the right of "
        "their triggering step via orange dashed edges, and a **marching-ants** animated edge "
        "into the active step. No graph library; node count is capped upstream.",
    "ui/web/src/components/run/RunChrome.tsx":
        "**Fix C — the persistent run HUD.** A single always-visible strip: step/total, "
        "turns, cumulative cost, live **tok/s**, planner model, and a progress bar — driven "
        "by `ChromeMetrics`, so it updates at the same batched cadence as the feed.",
    "ui/web/src/components/run/GraphLens.tsx":
        "The Turns-tab graph lens: a staged DAG of one turn's flow "
        "(plan→research→build→verify→checkpoint), hand-rolled with zero graph library.",
    "ui/web/src/components/L0Ambient.tsx":
        "The **L0 'glance'** layer for a turn: a one-line narration ticker plus ambient "
        "phase/plan progress — the calm summary that sits above the detailed timeline.",
    "ui/web/src/components/Timeline.tsx":
        "The **L1 timeline** for a turn: the ordered tool/file/shell/gate/checkpoint/stream "
        "entries with status, latency, progress, and an expand-to-L2 affordance.",
    "ui/web/src/components/L2Panels.tsx":
        "The **L2** panels: the research fan-out visualization and the full raw OTLP trace, "
        "rendered on demand.",
    "ui/web/src/components/SpanTree.tsx":
        "Renders a correlated OTLP span subtree (matched by name + time) under an expanded "
        "L1 tool row, and the full-trace tree.",

    # ── other pages ──
    "ui/web/src/components/ActivityPage.tsx":
        "Lists known/recent runs (journal ∪ `/api/runs`) and lets you open or replay one.",
    "ui/web/src/components/ProvidersPage.tsx":
        "The provider/driver dashboard: driver state, the synth rungs, the role→rung roster, "
        "and key presence (booleans only — never the values).",
    "ui/web/src/components/CostPage.tsx":
        "The cost-ledger view: today/week/month/all, broken down by provider/model/role, "
        "plus free-budget remaining — straight from `lib.inference.ledger.report()`.",
    "ui/web/src/components/providers/KeyManager.tsx":
        "Shared key-entry UI: capture a provider key and POST it — the value goes straight to "
        "the secret store and only a `{present}` boolean ever comes back.",
    "ui/web/src/components/wizard/Wizard.tsx":
        "The onboarding wizard (Tier 2): profile detect → key capture → live test-connection "
        "→ review → launch, with distinct first-run and edit modes.",

    # ── backend (Python stdlib server) ──
    "ui/server/__main__.py":
        "The entry point for `python3 -m ui.server` (what `hm ui` runs). Picks a **sticky "
        "port** (`~/.hermes-max/ui.conf`, next free if taken), binds `127.0.0.1`, optionally "
        "opens the browser, prints the one URL line, and starts the threaded HTTP server. "
        "`--token` is opt-in, for exposing the UI beyond loopback (e.g. over Tailscale).",
    "ui/server/__init__.py":
        "The package marker for the `ui.server` module.",
    "ui/server/app.py":
        "**The HTTP + SSE request handler.** Threaded, so an open SSE stream never blocks the "
        "REST endpoints. It routes the Tier-1 API, streams `/api/events/{run_id}`, serves the "
        "built React bundle from `web/dist` (tokenless static assets), and enforces the "
        "localhost hardening (token + loopback Host/Origin) on every `/api` call.",
    "ui/server/security.py":
        "**Defense-in-depth for a loopback server.** Constant-time launch-token check, "
        "Origin/Host validation (blocks DNS-rebinding and the 0.0.0.0-day class), and the "
        "token-as-CSRF-synchronizer on POSTs. No provider key ever passes through here.",
    "ui/server/runs.py":
        "**The run registry + recent-projects store.** A 'run' is a lightweight handle: the "
        "byte offset into the single global livelog at the moment it started, plus "
        "cwd/prompt/mode; the events endpoint tails from that offset to scope the stream. "
        "`POST /api/run` launches `hermes` in the chosen cwd; the synthetic `live` id "
        "attaches to an already-running agent without launching anything. No secrets stored.",
    "ui/server/run_watcher.py":
        "The discovery seam for **universal SSE**: `snapshot()` reads `~/.hermes-max/runs/` "
        "so a run launched **anywhere** — here, in `hm dev`, or bare in a terminal — appears "
        "in the UI within a poll interval. Poll-based, no inotify, no background thread; a "
        "locked/missing descriptor is simply skipped.",
    "ui/server/feeds.py":
        "**The telemetry tap and the core of the SSE contract.** It tails the existing "
        "livelog from a run's offset and **translates** each record into the typed SSE event "
        "model (`_translate`), polls the ledger for live cost ticks and an optional `PLAN.md` "
        "for L0 progress, emits keep-alive heartbeats, computes `driver_status()`, and — "
        "**Part 2** — passes the conductor plugin's `conductor.*` spans through as typed "
        "`conductor` events. Zero new instrumentation: it reads signals that already exist "
        "and never fabricates events the log doesn't carry.",
    "ui/server/otlp.py":
        "The Tier-3 **OTLP/HTTP receiver**: a ~40-line stdlib protobuf wire reader (so there "
        "is **no `opentelemetry-proto` dependency**) plus an OTLP/JSON path, normalizing each "
        "span to flat JSON and publishing it to the SSE generators via a ring-buffer pub/sub "
        "hub (so a late-connecting client still gets the recent tree). Loopback-only; a "
        "collector fans out to both Phoenix and here.",
    "ui/server/config_api.py":
        "The Tier-2 **config surface**: key capture (`POST /api/keys/{provider}` → straight "
        "to the secret store), masked key status (present booleans only), non-secret config "
        "writes (mode, `VLLM_BASE_URL`), a live connection probe (with a real User-Agent so "
        "Cloudflare-fronted providers don't 403), and the native directory picker "
        "(zenity/kdialog/yad, graceful when headless).",
    "ui/server/secrets_store.py":
        "**The single place a provider key is written and read back.** Backend selection, "
        "best-available-wins: macOS `security` → Linux `secret-tool` → python `keyring` → a "
        "chmod-600 `.env` fallback. `set_secret` returns only status; there is no public "
        "getter that hands a secret out; the private `_resolve` is used solely to inject into "
        "the spawned agent's env or the connection probe. Nothing here ever logs a value.",
}

# Ordered sections: (title, intro-or-None, [repo-relative paths])
SECTIONS: list[tuple[str, str | None, list[str]]] = [
    ("1 · Build & entry", None, [
        "ui/web/index.html", "ui/web/package.json", "ui/web/vite.config.ts",
        "ui/web/tailwind.config.js", "ui/web/postcss.config.js",
        "ui/web/tsconfig.json", "ui/web/tsconfig.node.json",
        "ui/web/src/main.tsx", "ui/web/src/index.css",
    ]),
    ("2 · App shell & routing", None, [
        "ui/web/src/App.tsx", "ui/web/src/lib/router.ts", "ui/web/src/lib/runjournal.ts",
    ]),
    ("3 · Types & data layer", None, [
        "ui/web/src/types.ts", "ui/web/src/lib/api.ts", "ui/web/src/lib/events.ts",
        "ui/web/src/lib/token.ts", "ui/web/src/lib/modes.ts",
    ]),
    ("4 · State reducers", None, [
        "ui/web/src/state.ts", "ui/web/src/lib/feed.ts",
    ]),
    ("5 · Chrome & navigation", None, [
        "ui/web/src/components/SideNav.tsx", "ui/web/src/components/TopChrome.tsx",
        "ui/web/src/components/ui.tsx", "ui/web/src/components/Popover.tsx",
    ]),
    ("6 · The Run view (Part 2 lives here)", None, [
        "ui/web/src/components/RunPage.tsx", "ui/web/src/components/run/Composer.tsx",
        "ui/web/src/components/run/VirtualFeed.tsx", "ui/web/src/components/run/FlowGraph.tsx",
        "ui/web/src/components/run/RunChrome.tsx", "ui/web/src/components/run/GraphLens.tsx",
        "ui/web/src/components/L0Ambient.tsx", "ui/web/src/components/Timeline.tsx",
        "ui/web/src/components/L2Panels.tsx", "ui/web/src/components/SpanTree.tsx",
    ]),
    ("7 · Other surfaces", None, [
        "ui/web/src/components/ActivityPage.tsx", "ui/web/src/components/ProvidersPage.tsx",
        "ui/web/src/components/CostPage.tsx", "ui/web/src/components/providers/KeyManager.tsx",
        "ui/web/src/components/wizard/Wizard.tsx",
    ]),
    ("8 · Backend — the stdlib Python server", None, [
        "ui/server/__main__.py", "ui/server/__init__.py", "ui/server/app.py",
        "ui/server/security.py", "ui/server/runs.py", "ui/server/run_watcher.py",
        "ui/server/feeds.py", "ui/server/otlp.py", "ui/server/config_api.py",
        "ui/server/secrets_store.py",
    ]),
]

OVERVIEW = """\
# hermes-max Web UI — complete code & architecture

> Generated by `scripts/gen_webui_doc.py` (reads every file from disk — the code below
> is exact). One document: the architecture, then every source file with a per-file
> explanation followed by its full contents.

## What this is

A single TypeScript SPA that gives hermes-max a live, graphical face — launched with
`hm ui` (friction-free, localhost) and later wrappable by Tauri without changing a line
of the frontend. It is built on three deliberate constraints:

1. **No runtime dependencies beyond React.** The router, the virtualized list, the flow
   graph, the popovers, the SSE client — all hand-rolled. The bundle is ~67 KB gzipped.
2. **The backend is the Python standard library only.** Zero pip installs — a
   sovereignty choice. It serves the built bundle and exposes one typed event channel.
3. **Zero new instrumentation.** The UI is a *tap* on signals that already exist: the
   livelog JSONL the agent/MCP servers write, the cost ledger, and (Tier 3) the OTLP
   spans the agent already emits to Phoenix. Nothing is fabricated.

## The shape of the system

```
                ┌─────────────────────── browser (SPA) ───────────────────────┐
                │  App.tsx  (single source of truth)                           │
   hash route ──▶  ├─ reduce()      turn-based view   (state.ts)               │
                │  ├─ reduceFeed()  feed/flow/chrome  (feed.ts, batched 100ms) │
                │  └─ one EventSource per run  ◀───────────────┐               │
                └──────────────────────────────────────────────┼──────────────┘
                                                                │  SSE (typed events)
   ┌────────────────────────── ui/server (stdlib) ─────────────┼──────────────┐
   │  app.py  HTTP+SSE handler  ── security.py (token + Origin/Host + CSRF)    │
   │  feeds.py  TAP + _translate ─┬─ lib.livelog  (~/.hermes-max/logs/live.jsonl)
   │                              ├─ lib.inference.ledger  (cost ticks)         │
   │                              └─ PLAN.md      (L0 progress)                 │
   │  otlp.py   OTLP/HTTP receiver ◀── collector fan-out ◀── agent OTel spans   │
   │  runs.py / run_watcher.py   run registry + universal discovery            │
   │  config_api.py / secrets_store.py   keys → OS keychain (never to browser) │
   └───────────────────────────────────────────────────────────────────────────┘
```

## Two reducers, one stream

Every run opens exactly one `EventSource`. `App.tsx` fans each frame to two pure
reducers:

* **`state.ts`** models the conversation as TURNS for the *Turns* tab (the original
  view): user message → working flow → handback.
* **`feed.ts`** (Part 2) folds the SAME frames into three memory-bounded structures —
  a flat `FeedItem[]` (the virtualized **Feed**), a derived `FlowState` (the **Flow**
  graph), and `ChromeMetrics` (the persistent HUD). It is fed in **100ms batches** and
  every structure is hard-capped, so memory is constant no matter how long the run.

## The conductor → UI path (Part 2)

The in-harness *conductor plugin* (Hermes lifecycle hooks) emits `conductor.*` records
to the livelog: `llm_call`, `llm_response` (token counts → tok/s), `verify_pass`/
`verify_fail`, `trigger`, `guidance`, `step_advance`, `run_complete`. `feeds._translate`
passes these through as a single typed `conductor` SSE event; `feed.ts` turns each into
feed rows, flow nodes (steps + branching conductor nodes), and HUD metrics.

## Memory & robustness limits (Fix D)

| Limit | Value | Where |
|---|---|---|
| Feed buffer (circular) | `MAX_FEED_ITEMS = 500` | `lib/feed.ts` |
| Flow graph nodes | `MAX_GRAPH_NODES = 200` | `lib/feed.ts` |
| SSE batch flush window | `BATCH_FLUSH_MS = 100` | `lib/feed.ts` + `App.tsx` |
| SSE reconnect ceiling | `MAX_SSE_RECONNECTS = 10` | `lib/events.ts` |
| Virtualized DOM rows | viewport + overscan only | `run/VirtualFeed.tsx` |

## Security posture

The server binds `127.0.0.1` only. Every `/api` request is checked in `security.py`:
constant-time launch-token compare, loopback Host/Origin validation (DNS-rebinding +
0.0.0.0-day defense), and the token doubles as a CSRF synchronizer on POSTs. Provider
keys go **in** via `POST /api/keys/{provider}` straight to the OS keychain
(`secrets_store.py`) and only a `{present}` boolean ever comes back out — the value is
never returned to the browser and never logged.
"""


def emit_file(path: str) -> list[str]:
    abspath = os.path.join(REPO, path)
    note = NOTE.get(path, "")
    ext = os.path.splitext(path)[1]
    lang = LANG.get(ext, "")
    lines = [f"### `{path}`", ""]
    if note:
        lines += [note, ""]
    if not os.path.exists(abspath):
        lines += [f"_(file not found at generation time)_", ""]
        return lines
    with open(abspath, "r", errors="replace") as f:
        code = f.read().rstrip("\n")
    n = code.count("\n") + 1
    # 4-backtick fence so any 3-backtick content inside code can't break out
    lines += [f"````{lang}", code, "````", "", f"<sub>{n} lines · `{path}`</sub>", ""]
    return lines


def main() -> int:
    out: list[str] = [OVERVIEW, ""]
    # table of contents
    out += ["## File index", ""]
    for title, _intro, paths in SECTIONS:
        out.append(f"**{title}**")
        out.append("")
        for p in paths:
            out.append(f"- `{p}`")
        out.append("")
    # sections
    for title, intro, paths in SECTIONS:
        out += [f"---", "", f"## {title}", ""]
        if intro:
            out += [intro, ""]
        for p in paths:
            out += emit_file(p)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")
    total = sum(1 for _ in open(OUT))
    print(f"wrote {OUT} — {total} lines, {len(NOTE)} files documented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
