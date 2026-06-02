# Hermes Studio — Architecture & Cross-Layer Analysis

*A structural read of the Tauri desktop layer as it stands, set against the web
UI and the base CLI / MCP / service layers it sits on top of. Raw Tauri source
is reproduced verbatim in **Appendix A**.*

This is a descriptive analysis of the code **as it is** — not a spec, not a
roadmap. Where a design is a consequence of a hard constraint (e.g. cross-origin
rules forcing config into Rust), that is called out.

---

## 0. The stack, bottom to top

`hermes-max` is a set of concentric layers wrapped around an external `hermes`
agent binary. Each layer is independently runnable; each upper layer *observes
and/or drives* the one below it rather than replacing it.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 8  studio/         Tauri 2 desktop appliance (Rust shell + React + webview)│  desktop
│ 7  ui/web/         React SPA — the Phase 0-7 visualization + actuation     │  browser
│ 6  ui/server/      stdlib-Python HTTP tap: SSE feed, run registry, history │  localhost
├──────────────────────────────────────────────────────────────────────────┤
│ 5  hm + scripts/   operator CLI (bash dispatch → *.sh + python helpers)    │  control plane
│ 4  skills/         ~33 workflow-* behaviour contracts loaded into hermes   │
│ 3  mcp-*/          14 stdlib-HTTP MCP servers (tools: verify/rag/kg/…)     │  tool plane
│ 2  plugins/        the conductor plugin (in-harness orchestration brain)   │
│ 1  hermes (extern) + lib/inference (role→provider seam, ledger, livelog)   │  agent core
└──────────────────────────────────────────────────────────────────────────┘
```

The shared **data planes** that cut across every layer:

| Plane | Path | Written by | Read by |
|---|---|---|---|
| Livelog (event spine) | `~/.hermes-max/logs/live.jsonl` | hermes / conductor / MCP via `lib/livelog.py` | `hm watch/observe`, `ui/server/feeds`, **studio `workshop.rs`** |
| Cost ledger | `~/.hermes-max/inference/ledger.jsonl` | `lib/inference/ledger.py` | `hm cost`, `ui` cost, **studio `workshop.rs`** |
| OTLP spans | in-proc hub | collector → `ui/server/otlp.py` | `ui` span tree (L2) |
| Run registry | `~/.hermes-max/runs/*.json` | `ui/server/runs.py`, `hm run` | `ui /api/runs`, **studio Projects/Workshop** |
| Run history (FTS) | `~/.hermes-max/ui/history.db` | `ui/server/history.py` (SQLite) | `ui` Runs index / replay |
| Studio config | `~/.hermes-max/studio.conf` + OS keychain + `studio/projects.json` | **studio `config.rs`/`keychain.rs`/`projects.rs`** | studio shell |

The studio layer is the only one written in **Rust**; everything below it is
**Python (stdlib) + bash**, and the two UI frontends share one **React/TS +
Tailwind** codebase and token system.

---

## 1. The Tauri layer (`studio/`)

### 1.1 Tree

```
studio/
├── package.json              React + @tauri-apps/* deps; scripts: dev/build/tauri
├── vite.config.ts            fixed port 1420, strictPort (Tauri attaches)
├── tsconfig.json/.node.json  strict TS; composite node project
├── index.html                #root + main.tsx
├── tailwind.config.js        ← copied verbatim from ui/web (shared tokens)
├── postcss.config.js
├── README.md                 architecture + build + honest verification status
├── src/                      THE SHELL (React) — first-run / projects / settings
│   ├── main.tsx              mounts <App/>, imports index.css
│   ├── index.css             ← copied from ui/web (OKLCH token system, Phase 0)
│   ├── App.tsx               shell router: loading → firstrun|projects|settings|workshop
│   ├── lib/
│   │   ├── tauri.ts          THE SEAM — only file importing @tauri-apps/api
│   │   ├── detect.ts         probe_capabilities / probe_endpoint / stack_health wrappers
│   │   ├── firstrun.ts       PROVIDERS preset + configure_endpoint / save_provider_key
│   │   ├── projects.ts       list/create/rename/delete/pick_directory wrappers
│   │   ├── workshop.ts       start/stop_workshop + onWorkshopStatus(event) wrapper
│   │   ├── studioConfig.ts   load_studio_config / save_studio_settings wrappers
│   │   └── shadow.ts         ← copied from ui/web (cost-shadow pricing model)
│   ├── screens/
│   │   ├── Loading.tsx       calm wordmark, waits on `stack-ready`
│   │   ├── FirstRun.tsx      3-state detect-and-bless (ready/connect/install)
│   │   ├── Projects.tsx      card grid + new-project modal + running cost total
│   │   ├── Workshop.tsx      studio bar + <iframe http://127.0.0.1:7080> (the web UI)
│   │   └── Settings.tsx      Your AI / Notifications / Display
│   └── components/
│       ├── ConnectAI.tsx     shared endpoint+key form (FirstRun + Settings)
│       ├── ProviderGrid.tsx  preset provider chips (free tiers first)
│       ├── ProjectCard.tsx   status + cost + savings-on-hover + ⋯ menu
│       ├── CompletionCard.tsx the S5 receipt (savings celebration)
│       └── StatusDot.tsx     colour+pulse dot
└── src-tauri/                THE NATIVE BACKEND (Rust)
    ├── Cargo.toml            tauri 2 (+tray-icon), shell/fs/dialog/notification,
    │                         ureq+rustls, keyring (pure-Rust secret-service), libc
    ├── build.rs              tauri_build::build()
    ├── tauri.conf.json       ai.hermesmax.studio, deb target + depends, icons
    ├── capabilities/default.json   core + plugin permission grants
    ├── icons/                generated placeholder PNGs
    └── src/
        ├── main.rs           Builder: plugins, manage(state), setup(tray+startup),
        │                     on_window_event(close→hide), invoke_handler, on Exit→stop_all
        ├── sidecar.rs        SidecarManager: start/health/stop the Python server + MCP
        ├── detect.rs         capability probe + endpoint probe (reads repo .env)
        ├── config.rs         studio.conf + agent_env (injects repo .env) + endpoint cmds
        ├── keychain.rs       keyring store/validate + provider base URLs
        ├── projects.rs       projects.json CRUD + dialog folder picker + update_stats
        ├── workshop.rs       livelog tailer → `workshop-status` events + notify + tray
        ├── notify.rs         native notifications, gated by settings + focus
        └── tray.rs           system tray (Open/New/Quit), tooltip, click-to-restore
```

### 1.2 The two modes of the one window

```
┌─ Tauri WebviewWindow "main" ─────────────────────────────────────────────┐
│                                                                           │
│  SHELL MODE (studio/src React app served from studio/dist)                │
│    Loading → FirstRun / Projects / Settings                               │
│                                                                           │
│  WORKSHOP MODE (a project is open)                                        │
│    ┌─ studio bar (React, shell origin) ──────────────────────────────┐    │
│    │ ← Projects · <name> · "Thinking…" · $0.03 · 4/7                  │    │
│    └──────────────────────────────────────────────────────────────────┘   │
│    ┌─ <iframe src="http://127.0.0.1:7080"> ─────────────────────────┐     │
│    │   the ENTIRE Phase 0-7 web UI, unmodified, same-origin to its   │     │
│    │   own Python backend                                            │     │
│    └──────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────┘
```

The shell and the embedded web UI are **two different origins** (`tauri://` /
`http://localhost:1420` vs `http://127.0.0.1:7080`). This single fact drives the
two most important design decisions in the layer:

1. **Config can't be POSTed to the backend from the shell.** `ui/server` rejects
   non-loopback `Origin` on POST and requires a same-origin CSRF cookie. The
   shell can't satisfy either. → All AI configuration is done **Rust-side**
   (`config.rs` writes `studio.conf`, `keychain.rs` stores keys) and **injected
   into the Python sidecar's environment** at spawn (`agent_env`), which the
   agent the backend spawns then inherits. The web UI *inside* the iframe is
   same-origin and keeps full POST access for its own actuation.
2. **The shell can't read the backend's SSE.** Cross-origin `EventSource` to
   `127.0.0.1:7080` is CORS-blocked. → The studio bar's live status comes from
   Rust tailing the livelog directly (`workshop.rs`) and emitting
   `workshop-status` tauri events — a parallel, no-CORS path to the same data the
   web UI's chrome shows.

### 1.3 Process & lifecycle model

```
app launch
  └─ main.rs setup()
       ├─ tray::build()                       system tray appears
       └─ sidecar::spawn_startup(app)         background thread:
            ├─ SidecarManager::start()
            │    ├─ spawn python3 -m ui.server --no-open --port 7080
            │    │    (cwd=repo_root, env += agent_env())   [setsid → own group]
            │    ├─ poll GET /healthz ≤5s
            │    └─ if `hm` present: spawn `hm dev` (MCP servers)
            └─ emit "stack-ready" → React Loading resolves

window close  → on_window_event: prevent_close + hide   (build keeps running)
tray "Quit"   → app.exit(0)
app Exit      → run(): SidecarManager::stop_all()
                 SIGTERM process groups → 300ms grace → SIGKILL → wait   (no orphans)
```

`repo_root()` resolution order: `$HERMES_MAX_ROOT` → walk up from
`current_exe()` looking for `ui/server` → compile-time
`CARGO_MANIFEST_DIR/../..` (correct for a `.deb` built on the same machine as the
repo).

### 1.4 Rust module relations

| Module | Owns | Calls | Called by |
|---|---|---|---|
| `main.rs` | Builder wiring, command registry, window-close, exit cleanup | all modules | Tauri runtime |
| `sidecar.rs` | Python+MCP child processes, health, repo_root, kill-group | `config::agent_env` | `main`, `config::*` (restart), commands |
| `config.rs` | `studio.conf`, `agent_env`, endpoint commands | `sidecar` (restart), `detect::probe_endpoint`, `keychain` | `main`, `sidecar` |
| `detect.rs` | capability/endpoint probes (ureq) | `sidecar::which`, `keychain::configured`, `config::repo_dotenv` | `main`, `config` |
| `keychain.rs` | OS keychain (keyring), provider base URLs, key validation | (ureq) | `config`, `detect` |
| `projects.rs` | `projects.json` CRUD, folder dialog, stats | (dialog plugin) | `main`, `workshop::update_stats` |
| `workshop.rs` | livelog tail, `workshop-status`, recent-project preset, stats persist | `notify`, `tray`, `projects::*` | `main` |
| `notify.rs` | native notifications, prefs, focus | (notification plugin) | `workshop` |
| `tray.rs` | tray icon/menu/tooltip | (tray-icon) | `main`, `workshop` |

### 1.5 The frontend seam

Every Tauri call funnels through `lib/tauri.ts` (`invoke`/`listen`), which mocks
out in a plain browser. The `lib/*.ts` wrappers are thin typed adapters over the
Rust commands; the `screens/` and `components/` never touch `@tauri-apps`
directly. This mirrors the web UI's `lib/api.ts` + `lib/events.ts` discipline.

---

## 2. The web UI layer (`ui/`)

### 2.1 Backend (`ui/server/`, stdlib Python — zero pip deps)

| File | Role |
|---|---|
| `__main__.py` | launcher: `ThreadingHTTPServer`, sticky port 7080, browser open |
| `app.py` | request router; all `/api/*` routes; SSE handler; static SPA; loopback+CSRF security gates |
| `feeds.py` | livelog JSONL → typed SSE translator; cost/plan polling; conductor passthrough; `id:`=byte-offset for replay-on-reconnect |
| `runs.py` | run registry + recent-projects; **launches `hermes`** (`POST /api/run`); signal/pause; PLAN.md read/write |
| `history.py` | SQLite + FTS5 index over completed runs (Phase 4); registry sync/backfill |
| `dashboards.py` | MCP TCP health probe; state-file inspector (Phase 6) |
| `config_api.py` | key capture, provider test, mode switch, dir picker |
| `secrets_store.py` | cross-platform secret store (keychain / libsecret / .env) |
| `security.py` | loopback bind, launch token, Origin/Host/CSRF |
| `otlp.py` | OTLP/HTTP decoder + span pub/sub hub (L2) |
| `run_watcher.py` | poll-based run discovery |

It is a **tap, not a brain**: it holds no UI state, reads the same livelog/ledger
the CLI does, and its one write path is spawning `hermes` and signalling it.

### 2.2 Frontend (`ui/web/`, React + TS + Tailwind, ~290 KB)

Phases 0–7 (see git log): OKLCH token system; live run polish (sparklines,
reasoning, diffs, minimap, semantic-zoom flow); the **Cost Shadow Meter**; the
**Conductor Swimlane**; SQLite-backed Runs index + replay/scrubbing; the control
surface (steer/interrupt/pending, PLAN.md editor); dashboards + a hand-rolled
Cmd-K + toasts; and the "unexplored edges" (memory-anchor overlay, counterfactual
intervention cards). The reducer (`lib/feed.ts`) folds one SSE stream into a
capped feed + flow graph + chrome HUD. **No UI framework beyond React** — router,
virtualization, popover, palette, sankey, sparkline, toasts all hand-rolled to
hold the bundle budget.

Studio embeds this layer **whole and unmodified** (it copies only `index.css`,
`tailwind.config.js`, and `shadow.ts` for its own shell chrome).

---

## 3. The base layer (hermes + CLI + MCP + skills + plugins + lib)

### 3.1 Agent core
- **`hermes`** — the external agent binary (not in this repo). Everything here is
  scaffolding *on top of* it. Studio merely validates its presence (`which hermes`).
- **`lib/inference/`** — the role→provider seam: `config.py`, `roles.py`,
  `roster.py`, `router.py`, `adapters.py`, `buckets.py`, `ledger.py` (the cost
  ledger every layer reads), `modes_cli.py`. The "inference fabric" that maps an
  abstract role (planner / executor / escalation) to a concrete provider+model.
- **`lib/livelog.py`** — the append-only JSONL event writer; the spine all
  observers tail.

### 3.2 Conductor plugin (`plugins/conductor/`)
The orchestration brain that runs **inside** hermes via lifecycle hooks
(`plugin.yaml` + `hermes.md.template`): deterministic plan→execute→verify, the
`pre_llm_call` execution-contract re-injection (the "memory anchor" the web UI
visualizes), conductor triggers/guidance, and escalation. It emits the
`conductor.*` livelog spans that both the web UI reducer and studio's tailer
parse. `plugins/free_uplift/` is a secondary policy plugin.

### 3.3 MCP tool plane (`mcp-*/`, 14 servers)
Each is a **stdlib-HTTP Python `server.py`** (the manifest forbids any server
importing torch/CUDA — models are reached over the network). Ports 9101–9115 per
`mcp-manifest.yaml`:

```
9101 verify   9102 rag      9103 kg          9104 observability  9105 escalation
9106 checkpoint 9107 watchdog 9108 search    9109 docs           9110 research
9111 repomap  9112 lsp      + scopemap, codegraph
```

`mcp-research/` is the richest (banyan/corpus/extract/rank/relevance/sources/
verify_gate). These are hermes's **tools**; the agent calls them. Studio surfaces
their health via the web UI's `dashboards.py` TCP probe (it does not manage them
beyond best-effort `hm dev`).

### 3.4 Skills (`skills/`, ~33 `workflow-*`)
Behaviour contracts loaded into hermes (plan-first, verify-enhanced, stuck-detect,
deep-research, effort-routing, edit-format, …). Pure markdown/policy; not a
running service.

### 3.5 Operator CLI (`hm` + `scripts/`)
`hm` is a **bash dispatch** mapping verbs to `scripts/*.sh` + python helpers
(`up/down/restart/status/watch/observe/logs/run/ui/studio/dev/cost/health/mode/
preflight/snapshot/…`). The "service layer" is **PID-file + port based** process
management (`start-all.sh` / `stop-all.sh` / `restart.sh`), **not Docker** — there
are no Dockerfiles or compose files in the repo; `bootstrap*.sh` and `install.sh`
handle host setup, and `serving/local_serve.py` serves local embed/rerank models.

> **On "docker":** the project deliberately has no container layer. Isolation and
> lifecycle are done with native processes, PID files, and `setsid` groups —
> the same primitive studio's `sidecar.rs` uses. This keeps the stack
> sovereign/host-native and is why studio can sidecar it with plain
> `std::process::Command` rather than orchestrating containers.

---

## 4. Cross-layer comparison

### 4.1 By dimension

| Dimension | Base (CLI/MCP/lib) | Web UI (`ui/`) | Studio (`studio/`) |
|---|---|---|---|
| **Language** | Python (stdlib) + bash | Python stdlib (server) + React/TS (web) | Rust (Tauri) + React/TS (shell) |
| **Dependencies** | stdlib only; no torch in MCP | zero pip; React-only frontend | Tauri/Rust crates; React-only shell |
| **Process model** | many long-lived procs, PID files | one `ThreadingHTTPServer` | Rust supervises Python+MCP children |
| **Coupling to hermes** | tight (conductor in-harness; MCP = tools) | loose (taps livelog; spawns hermes) | none direct (`which hermes`; wraps the web UI) |
| **State owned** | livelog, ledger, registry | + SQLite history | + studio.conf, keychain, projects.json |
| **Trust boundary** | host-native, sovereign | loopback-only, Origin/CSRF | OS keychain; no model bundling |
| **Distribution** | git clone + bootstrap | `hm ui` (served) | `.deb` (binary + assets only) |
| **User** | terminal operator | browser operator | non-terminal "idea→product" user |

### 4.2 How the layers reach the same data differently

The livelog is the clearest lens. Three consumers, three idioms:

- **CLI** (`hm watch`/`observe`): tails the JSONL in the terminal, raw.
- **Web UI** (`feeds.py` → SSE → `feed.ts` reducer): translates JSONL to typed
  SSE, same-origin `EventSource`, folds into a capped reducer for rich rendering.
- **Studio** (`workshop.rs`): tails the **same JSONL in Rust** (forced by the
  cross-origin CORS wall), translates conductor spans into **plain-language
  phrases** ("Thinking…", "Checking the work… ✓"), and pushes `workshop-status`
  tauri events to the shell bar. It does **not** re-implement the reducer — the
  embedded web UI still does the heavy rendering; Rust only feeds the thin bar.

So studio is a *parallel-path observer* of the web UI's own data source, not a
re-implementation — a direct consequence of embedding a different-origin UI.

### 4.3 Configuration flow, end to end

```
Base:    keys/endpoints in repo .env  →  lib/inference reads them  →  hermes
Web UI:  POST /api/config + /api/keys  →  .env / secrets_store      →  hermes
Studio:  studio.conf + OS keychain  ──┐
         repo .env (inherited whole) ─┼─ config::agent_env()  →  Python sidecar env
                                       │                          →  hermes (inherits)
         (shell can't POST backend: cross-origin)
```

Studio's `agent_env()` inheriting the **entire repo `.env`** (then layering
keychain + `studio.conf` on top) is what lets it reuse the base layer's existing
provider configuration without re-entry — the design point the operator asked for.

### 4.4 Shared vs duplicated code

| Artifact | Base | Web UI | Studio |
|---|---|---|---|
| OKLCH token system (`index.css`, `tailwind.config.js`) | — | source | **copied** |
| Cost-shadow model (`shadow.ts`) | ledger is source of truth | source | **copied** |
| Cost ledger (`ledger.py`) | source | reads | reads (via tailer) |
| Livelog format | source (`livelog.py`) | parses (`feeds.py`) | parses (`workshop.rs`) |
| Run registry / recent-projects | `runs.py` | `runs.py` | **writes** recent (Rust) to preset the iframe |
| The entire React run-view | — | source | **embedded unmodified** (iframe) |

The duplication is deliberate and small (three files copied for the shell's own
chrome); the large surface (the run view) is embedded, not forked.

### 4.5 Observations on the code as it is

- **Sovereignty is the through-line.** stdlib backend, no-torch MCP, no Docker,
  hand-rolled frontend, pure-Rust keyring backend (no `libsecret` build dep),
  `ureq`+rustls (no OpenSSL). Every layer avoids heavy/opaque dependencies. Studio
  is consistent with this even though Rust+Tauri is the heaviest toolchain in the
  repo (the binary budget is held at <50 MB; the shell at ~170 KB JS).
- **The cross-origin wall is the defining studio constraint.** It cleanly splits
  responsibilities (Rust owns config + status bridge; the iframe owns rich
  actuation) but it also means a few directive ideas (studio-bar pause/steer,
  plan-first toggle in the bar) can't drive the iframe and were intentionally not
  faked — those controls live in the embedded web UI's own Phase 5 surface.
- **Studio adds no new agent capability.** It is pure packaging + onboarding +
  native affordances (tray, notifications, keychain, walk-away builds) over an
  unchanged web UI over an unchanged base. The value is reach (non-terminal
  users), not new behaviour.
- **The `.deb` ships only the shell.** Python backend, MCP servers, skills, and
  hermes are *not* bundled (per constraint); an installed studio still needs the
  repo present and resolvable (`HERMES_MAX_ROOT` / adjacency). This is the main
  gap between "builds" and "installs cleanly on an arbitrary machine."
- **Verification reality:** `cargo check` and `npm run build` are green; GUI
  launch and `.deb` emission are authored but not exercised headlessly.

---

## Appendix A — Raw Tauri source

*Generated verbatim from `studio/` below this line.*


### A.1  Native backend (Rust — `src-tauri/`)

#### `studio/src-tauri/Cargo.toml`
```toml
[package]
name = "hermes-studio"
version = "0.1.0"
description = "Hermes Studio — desktop appliance for the hermes-max agent stack"
authors = ["hermes-max"]
edition = "2021"
rust-version = "1.77"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
tauri-plugin-shell = "2"
tauri-plugin-fs = "2"
tauri-plugin-dialog = "2"
tauri-plugin-notification = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["rt-multi-thread", "time", "macros"] }
# Lightweight blocking HTTP for endpoint/health probes (rustls — no OpenSSL).
ureq = { version = "2", features = ["json", "tls"] }
# OS keychain. On Linux the pure-Rust secret-service backend (zbus) is used — it
# talks to the org.freedesktop.secrets DBus service at RUNTIME (gnome-keyring /
# KWallet, pulled in via libsecret-1-0 in the .deb depends) and needs no C lib to
# BUILD. On macOS, add the "apple-native" feature.
keyring = { version = "3", default-features = false, features = ["sync-secret-service", "crypto-rust"] }

[target.'cfg(unix)'.dependencies]
libc = "0.2"

[features]
# Build a stripped, smaller binary in release to respect the 50MB bundle budget.
default = []

[profile.release]
opt-level = "s"
lto = true
strip = true
codegen-units = 1
panic = "abort"
```

#### `studio/src-tauri/build.rs`
```rust
fn main() {
    tauri_build::build();
}
```

#### `studio/src-tauri/tauri.conf.json`
```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Hermes Studio",
  "version": "0.1.0",
  "identifier": "ai.hermesmax.studio",
  "build": {
    "beforeDevCommand": "npm run dev",
    "devUrl": "http://localhost:1420",
    "beforeBuildCommand": "npm run build",
    "frontendDist": "../dist"
  },
  "app": {
    "windows": [
      {
        "label": "main",
        "title": "Hermes Studio",
        "width": 1100,
        "height": 760,
        "minWidth": 1024,
        "minHeight": 700,
        "decorations": true,
        "transparent": false,
        "resizable": true
      }
    ],
    "security": { "csp": null }
  },
  "bundle": {
    "active": true,
    "targets": ["deb"],
    "category": "DeveloperTool",
    "shortDescription": "Idea in, proven product out.",
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png", "icons/icon.png"],
    "linux": {
      "deb": {
        "depends": [
          "libwebkit2gtk-4.1-0",
          "libayatana-appindicator3-1",
          "python3",
          "libsecret-1-0"
        ]
      }
    }
  },
  "plugins": {}
}
```

#### `studio/src-tauri/capabilities/default.json`
```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Capabilities for the Hermes Studio shell window.",
  "windows": ["main", "*"],
  "permissions": [
    "core:default",
    "core:event:default",
    "core:window:default",
    "core:webview:default",
    "core:app:default",
    "shell:allow-open",
    "dialog:allow-open",
    "notification:default",
    "fs:default"
  ]
}
```

#### `studio/src-tauri/src/main.rs`
```rust
// Hermes Studio — Tauri 2 desktop appliance. The shell window hosts the React
// shell (first-run / projects / settings); when a project is opened, the full
// hermes-max web UI loads in a webview pointed at the Python backend that this
// process sidecars. The user never touches a terminal.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;
mod detect;
mod keychain;
mod config;
mod projects;
mod workshop;
mod notify;
mod tray;

use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(sidecar::SidecarManager::default())
        .manage(workshop::WorkshopTailer::default())
        .setup(|app| {
            // Start the Python sidecar (+ MCP servers) in the background and emit
            // `stack-ready` once /healthz answers; the loading screen waits on it.
            sidecar::spawn_startup(app.handle().clone());
            // System tray for walk-away builds.
            let _ = tray::build(app.handle());
            Ok(())
        })
        // Closing the window hides it (the build keeps running in the tray); Quit
        // from the tray menu actually exits.
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            sidecar::start_stack,
            sidecar::stop_stack,
            sidecar::stack_health,
            detect::probe_capabilities,
            detect::probe_endpoint,
            config::load_studio_config,
            config::save_studio_settings,
            config::configure_endpoint,
            config::save_provider_key,
            config::restart_stack,
            config::open_url,
            projects::list_projects,
            projects::create_project,
            projects::rename_project,
            projects::delete_project,
            projects::open_path,
            projects::pick_directory,
            workshop::start_workshop,
            workshop::stop_workshop,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Hermes Studio")
        .run(|app, event| match event {
            // No orphan sidecars survive the app — SIGTERM the group, then SIGKILL.
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                if let Some(mgr) = app.try_state::<sidecar::SidecarManager>() {
                    mgr.stop_all();
                }
            }
            _ => {}
        });
}
```

#### `studio/src-tauri/src/sidecar.rs`
```rust
// Sidecar lifecycle — the Python backend (ui.server) + MCP servers, managed
// entirely by the Rust side. The user never sees them start or stop. Each child
// is launched in its own session (setsid) so we can signal the whole process
// group on shutdown; SIGTERM then SIGKILL guarantees no orphans.
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

pub const PORT: u16 = 7080;

#[derive(Default)]
pub struct SidecarManager {
    children: Mutex<Vec<Child>>,
    started: Mutex<bool>,
}

#[derive(Serialize, Clone)]
pub struct StackStatus {
    pub python_server: bool,
    pub mcp_servers: Vec<(String, bool)>,
    pub hermes_present: bool,
    pub active_run: Option<String>,
}

// ── small helpers (shared with detect.rs) ────────────────────────────────────
pub fn which(bin: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths).map(|p| p.join(bin)).find(|p| p.is_file())
    })
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn log_path(name: &str) -> PathBuf {
    let dir = home().join(".hermes-max").join("studio").join("logs");
    let _ = std::fs::create_dir_all(&dir);
    dir.join(name)
}

/// Repo root containing `ui/server`: env override, then walk up from the binary,
/// then a compile-time dev fallback (studio/src-tauri -> repo root).
pub fn repo_root() -> PathBuf {
    if let Ok(r) = std::env::var("HERMES_MAX_ROOT") {
        let p = PathBuf::from(r);
        if p.join("ui").join("server").exists() {
            return p;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        while let Some(d) = dir {
            if d.join("ui").join("server").exists() {
                return d;
            }
            dir = d.parent().map(|p| p.to_path_buf());
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."))
}

fn healthz_ok() -> bool {
    let url = format!("http://127.0.0.1:{PORT}/healthz");
    matches!(
        ureq::get(&url).timeout(Duration::from_millis(800)).call(),
        Ok(r) if r.status() == 200
    )
}

fn tcp_open(port: u16) -> bool {
    use std::net::{SocketAddr, TcpStream};
    TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(80),
    )
    .is_ok()
}

fn spawn_logged(cmd: &mut Command, logfile: &str) -> std::io::Result<Child> {
    let log = std::fs::OpenOptions::new().create(true).append(true).open(log_path(logfile))?;
    let errlog = log.try_clone()?;
    cmd.stdin(Stdio::null()).stdout(Stdio::from(log)).stderr(Stdio::from(errlog));
    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        // own session → child becomes a process-group leader we can signal as a group
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    cmd.spawn()
}

#[cfg(unix)]
fn term_group(pid: u32) {
    unsafe {
        // negative pid → the whole group (children too); also hit the leader.
        libc::kill(-(pid as i32), libc::SIGTERM);
        libc::kill(pid as i32, libc::SIGTERM);
    }
}
#[cfg(not(unix))]
fn term_group(_pid: u32) {}

impl SidecarManager {
    pub fn hermes_present(&self) -> bool {
        which("hermes").is_some()
    }

    /// Start the stack (idempotent). Returns once /healthz answers or 5s elapse.
    pub fn start(&self) -> StackStatus {
        let mut started = self.started.lock().unwrap();
        if !*started {
            let root = repo_root();
            // 1. Python backend (only if not already serving, e.g. `hm ui` running)
            if !healthz_ok() {
                if let Ok(child) = spawn_logged(
                    Command::new("python3")
                        .args(["-m", "ui.server", "--no-open", "--port", &PORT.to_string()])
                        .current_dir(&root)
                        .env("PYTHONPATH", &root)
                        // inject the configured endpoint + stored provider keys so the
                        // backend (and the agent it spawns) can reach the AI
                        .envs(crate::config::agent_env()),
                    "python-server.log",
                ) {
                    self.children.lock().unwrap().push(child);
                }
            }
            // 2. poll health up to ~5s
            for _ in 0..25 {
                if healthz_ok() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
            // 3. MCP servers via `hm dev` (best-effort; only if hm is on PATH)
            if which("hm").is_some() {
                if let Ok(child) = spawn_logged(Command::new("hm").arg("dev").current_dir(&root), "mcp.log") {
                    self.children.lock().unwrap().push(child);
                }
            }
            *started = true;
        }
        self.status()
    }

    pub fn status(&self) -> StackStatus {
        let mcp = (9101..=9115u16).map(|p| (format!(":{p}"), tcp_open(p))).collect();
        StackStatus {
            python_server: healthz_ok(),
            mcp_servers: mcp,
            hermes_present: self.hermes_present(),
            active_run: None,
        }
    }

    /// Stop then start — used after the AI source changes so the backend reloads
    /// its environment (endpoint / keys).
    pub fn restart(&self) -> StackStatus {
        self.stop_all();
        self.start()
    }

    pub fn stop_all(&self) {
        let mut kids = self.children.lock().unwrap();
        for child in kids.iter() {
            term_group(child.id());
        }
        std::thread::sleep(Duration::from_millis(300)); // grace period
        for child in kids.iter_mut() {
            let _ = child.kill(); // SIGKILL survivors
            let _ = child.wait();
        }
        kids.clear();
        if let Ok(mut s) = self.started.lock() {
            *s = false;
        }
    }
}

/// Start the stack in a background thread and emit `stack-ready` when up.
pub fn spawn_startup(app: AppHandle) {
    std::thread::spawn(move || {
        let status = app.state::<SidecarManager>().start();
        let _ = app.emit("stack-ready", status);
    });
}

// ── tauri commands ───────────────────────────────────────────────────────────
#[tauri::command]
pub fn start_stack(mgr: tauri::State<SidecarManager>) -> StackStatus {
    mgr.start()
}

#[tauri::command]
pub fn stop_stack(mgr: tauri::State<SidecarManager>) {
    mgr.stop_all();
}

#[tauri::command]
pub fn stack_health(mgr: tauri::State<SidecarManager>) -> StackStatus {
    mgr.status()
}
```

#### `studio/src-tauri/src/detect.rs`
```rust
// Capability detection — the silent first-run probe (hermes presence, configured
// endpoint reachability, provider keys) and a standalone endpoint test. Pure
// read-only probes with short timeouts; results drive the first-run screen.
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::sidecar::which;

#[derive(Serialize)]
pub struct DetectResult {
    pub hermes_present: bool,
    pub hermes_version: Option<String>,
    pub endpoint_configured: bool,
    pub endpoint_url: Option<String>,
    pub endpoint_reachable: Option<bool>,
    pub endpoint_model: Option<String>,
    pub keys_configured: Vec<String>,
    pub suggested_mode: String, // "Local" | "Cloud" | "NeedsSetup"
}

#[derive(Serialize)]
pub struct EndpointProbe {
    pub ok: bool,
    pub latency_ms: Option<u64>,
    pub model: Option<String>,
    pub error: Option<String>,
}

const PROVIDER_KEYS: &[(&str, &str)] = &[
    ("Anthropic", "ANTHROPIC_API_KEY"),
    ("OpenAI", "OPENAI_API_KEY"),
    ("Groq", "GROQ_API_KEY"),
    ("Cerebras", "CEREBRAS_API_KEY"),
    ("Gemini", "GEMINI_API_KEY"),
    ("DeepSeek", "DEEPSEEK_API_KEY"),
    ("DeepInfra", "DEEPINFRA_API_KEY"),
    ("OpenRouter", "OPENROUTER_API_KEY"),
    ("Together", "TOGETHER_API_KEY"),
];

/// Look a var up in the process env first, then the repo's .env.
fn lookup(key: &str, dotenv: &std::collections::HashMap<String, String>) -> Option<String> {
    std::env::var(key)
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| dotenv.get(key).filter(|v| !v.trim().is_empty()).cloned())
}

fn endpoint_from(dotenv: &std::collections::HashMap<String, String>) -> Option<String> {
    ["VLLM_BASE_URL", "OPENAI_BASE_URL", "HERMES_ENDPOINT"]
        .iter()
        .find_map(|k| lookup(k, dotenv))
}

/// GET {base}/models — returns (reachable, first model id). A generous timeout
/// (remote/Tailscale endpoints are slow to first byte) and 401/403 counts as
/// REACHABLE: the server is there, it just wants a key.
fn probe_models(base: &str) -> (Option<bool>, Option<String>) {
    let url = format!("{}/models", base.trim_end_matches('/'));
    match ureq::get(&url).timeout(Duration::from_secs(6)).call() {
        Ok(resp) => {
            let model = resp
                .into_json::<serde_json::Value>()
                .ok()
                .and_then(|j| {
                    j.get("data")
                        .and_then(|d| d.get(0))
                        .and_then(|m| m.get("id"))
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string())
                });
            (Some(true), model)
        }
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => (Some(true), None),
        Err(_) => (Some(false), None),
    }
}

#[tauri::command]
pub fn probe_capabilities() -> DetectResult {
    let hermes_present = which("hermes").is_some();
    let hermes_version = if hermes_present {
        std::process::Command::new("hermes")
            .arg("--version")
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    } else {
        None
    };

    let dotenv = crate::config::repo_dotenv();
    let endpoint_url = endpoint_from(&dotenv);
    let endpoint_configured = endpoint_url.is_some();
    let (endpoint_reachable, endpoint_model) = match &endpoint_url {
        Some(u) => probe_models(u),
        None => (None, None),
    };

    // A provider counts as configured if its key is in the process env, the
    // repo's .env, OR the OS keychain (where Studio stores keys).
    let stored = crate::keychain::configured();
    let keys_configured: Vec<String> = PROVIDER_KEYS
        .iter()
        .filter(|(_, env)| lookup(env, &dotenv).is_some() || stored.iter().any(|s| s == env))
        .map(|(name, _)| name.to_string())
        .collect();

    let suggested_mode = if endpoint_reachable == Some(true) {
        "Local"
    } else if !keys_configured.is_empty() {
        "Cloud"
    } else {
        "NeedsSetup"
    }
    .to_string();

    DetectResult {
        hermes_present,
        hermes_version,
        endpoint_configured,
        endpoint_url,
        endpoint_reachable,
        endpoint_model,
        keys_configured,
        suggested_mode,
    }
}

#[tauri::command]
pub fn probe_endpoint(url: String) -> EndpointProbe {
    let t0 = Instant::now();
    match probe_models(&url) {
        (Some(true), model) => EndpointProbe {
            ok: true,
            latency_ms: Some(t0.elapsed().as_millis() as u64),
            model,
            error: None,
        },
        _ => EndpointProbe {
            ok: false,
            latency_ms: None,
            model: None,
            error: Some("Couldn't reach an OpenAI-compatible /models endpoint there.".into()),
        },
    }
}
```

#### `studio/src-tauri/src/config.rs`
```rust
// Studio configuration (~/.hermes-max/studio.conf) + the first-run write paths.
// Studio is the source of truth for the AI source: the endpoint URL lives in
// studio.conf, provider keys live in the OS keychain. Both are injected into the
// Python sidecar's environment at start (agent_env), and the agent the backend
// spawns inherits them — so the shell never cross-origin POSTs to the backend.
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::keychain;
use crate::sidecar::SidecarManager;

#[derive(Serialize, Deserialize, Default, Clone)]
pub struct StudioConfig {
    #[serde(default)]
    pub endpoint_url: Option<String>,
    #[serde(default)]
    pub provider: Option<String>, // active cloud provider id (e.g. "groq")
    // Display / notification prefs (S4) — kept here so one file holds all of it.
    #[serde(default)]
    pub settings: serde_json::Value,
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn conf_path() -> PathBuf {
    let dir = home().join(".hermes-max");
    let _ = std::fs::create_dir_all(&dir);
    dir.join("studio.conf")
}

pub fn load() -> StudioConfig {
    std::fs::read_to_string(conf_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save(cfg: &StudioConfig) -> Result<(), String> {
    let json = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(conf_path(), json).map_err(|e| e.to_string())
}

/// Parse the repo's .env (KEY=VALUE, `export ` and quotes tolerated) so Studio
/// inherits whatever the repo already has configured — endpoint + provider keys.
pub fn repo_dotenv() -> std::collections::HashMap<String, String> {
    let mut m = std::collections::HashMap::new();
    let path = crate::sidecar::repo_root().join(".env");
    if let Ok(s) = std::fs::read_to_string(path) {
        for line in s.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((k, v)) = line.split_once('=') {
                let k = k.trim().trim_start_matches("export ").trim();
                let v = v.trim().trim_matches('"').trim_matches('\'');
                if !k.is_empty() {
                    m.insert(k.to_string(), v.to_string());
                }
            }
        }
    }
    m
}

/// The environment the Python sidecar (and the agent it spawns) should run with.
/// The repo's ENTIRE .env is inherited (the stdlib backend may not load it
/// itself), then Studio's own choices are layered ON TOP: keychain keys and the
/// studio.conf endpoint win. So a user never re-enters what the repo already
/// holds, but anything they set in Studio overrides it.
pub fn agent_env() -> Vec<(String, String)> {
    let mut map = repo_dotenv(); // base: everything already in the repo's .env
    let cfg = load();

    for e in keychain::PROVIDER_ENVS {
        if let Some(v) = keychain::get(e) {
            map.insert(e.to_string(), v);
        }
    }
    if let Some(url) = cfg.endpoint_url.filter(|u| !u.trim().is_empty()) {
        map.insert("VLLM_BASE_URL".to_string(), url.clone());
        map.insert("OPENAI_BASE_URL".to_string(), url);
    }
    map.into_iter().collect()
}

#[derive(Serialize)]
pub struct ApplyResult {
    pub ok: bool,
    pub error: Option<String>,
    pub model: Option<String>,
}

// ── tauri commands ───────────────────────────────────────────────────────────
#[tauri::command]
pub fn load_studio_config() -> StudioConfig {
    load()
}

#[tauri::command]
pub fn save_studio_settings(settings: serde_json::Value) -> Result<(), String> {
    let mut cfg = load();
    cfg.settings = settings;
    save(&cfg)
}

#[tauri::command]
pub fn configure_endpoint(url: String, force: bool, mgr: State<SidecarManager>) -> ApplyResult {
    let probe = crate::detect::probe_endpoint(url.clone());
    if !probe.ok && !force {
        // couldn't confirm it — let the UI offer "use it anyway"
        return ApplyResult { ok: false, error: probe.error, model: None };
    }
    let mut cfg = load();
    cfg.endpoint_url = Some(url);
    cfg.provider = None;
    if let Err(e) = save(&cfg) {
        return ApplyResult { ok: false, error: Some(e), model: None };
    }
    mgr.restart(); // backend picks up the new endpoint
    // Saved either way; on a forced save we couldn't confirm a model list, which
    // is fine (the server may need a key or be slow) — the endpoint is used.
    ApplyResult { ok: true, error: None, model: probe.model }
}

#[tauri::command]
pub fn save_provider_key(provider: String, env: String, key: String, mgr: State<SidecarManager>) -> ApplyResult {
    match keychain::validate_key(&env, &key) {
        Ok(model) => {
            if let Err(e) = keychain::store(&env, &key) {
                return ApplyResult { ok: false, error: Some(e), model: None };
            }
            let mut cfg = load();
            cfg.provider = Some(provider);
            let _ = save(&cfg);
            mgr.restart(); // backend + agent inherit the new key
            ApplyResult { ok: true, error: None, model }
        }
        Err(e) => ApplyResult { ok: false, error: Some(e), model: None },
    }
}

#[tauri::command]
pub fn open_url(url: String) {
    #[cfg(target_os = "linux")]
    let _ = std::process::Command::new("xdg-open").arg(&url).spawn();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg(&url).spawn();
}

#[tauri::command]
pub fn restart_stack(mgr: State<SidecarManager>) -> crate::sidecar::StackStatus {
    mgr.restart()
}
```

#### `studio/src-tauri/src/keychain.rs`
```rust
// OS keychain (keyring crate) + provider key validation. On Linux this uses the
// pure-Rust secret-service backend (the org.freedesktop.secrets DBus service);
// on macOS the native keychain (with the apple-native feature). Keys are stored
// under one service, accounted by the env var the agent reads — so injecting
// them into the Python sidecar's environment (config::agent_env) is a lookup.
use std::time::Duration;

use keyring::Entry;
use serde_json::Value;

const SERVICE: &str = "hermes-max";

/// All provider env vars Studio knows how to store/inject.
pub const PROVIDER_ENVS: &[&str] = &[
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPINFRA_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
];

pub fn store(account: &str, secret: &str) -> Result<(), String> {
    Entry::new(SERVICE, account)
        .and_then(|e| e.set_password(secret))
        .map_err(|e| e.to_string())
}

pub fn get(account: &str) -> Option<String> {
    Entry::new(SERVICE, account).ok().and_then(|e| e.get_password().ok())
}

/// env vars (and the provider name behind them) that have a stored key.
pub fn configured() -> Vec<String> {
    PROVIDER_ENVS.iter().filter(|e| get(e).is_some()).map(|e| e.to_string()).collect()
}

fn provider_base(env: &str) -> Option<&'static str> {
    match env {
        "ANTHROPIC_API_KEY" => Some("https://api.anthropic.com/v1"),
        "OPENAI_API_KEY" => Some("https://api.openai.com/v1"),
        "GROQ_API_KEY" => Some("https://api.groq.com/openai/v1"),
        "CEREBRAS_API_KEY" => Some("https://api.cerebras.ai/v1"),
        "GEMINI_API_KEY" => Some("https://generativelanguage.googleapis.com/v1beta/openai"),
        "DEEPSEEK_API_KEY" => Some("https://api.deepseek.com/v1"),
        "DEEPINFRA_API_KEY" => Some("https://api.deepinfra.com/v1/openai"),
        "OPENROUTER_API_KEY" => Some("https://openrouter.ai/api/v1"),
        "TOGETHER_API_KEY" => Some("https://api.together.xyz/v1"),
        _ => None,
    }
}

fn first_model_id(j: &Value) -> Option<String> {
    j.get("data")
        .and_then(|d| d.get(0))
        .and_then(|m| m.get("id"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string())
}

/// Validate a key by listing models with it. Returns the first model id on
/// success, an error string on failure.
pub fn validate_key(env: &str, key: &str) -> Result<Option<String>, String> {
    let base = provider_base(env).ok_or_else(|| "Unknown provider.".to_string())?;
    let url = format!("{base}/models");
    let req = if env == "ANTHROPIC_API_KEY" {
        ureq::get(&url).set("x-api-key", key).set("anthropic-version", "2023-06-01")
    } else {
        ureq::get(&url).set("Authorization", &format!("Bearer {key}"))
    }
    .timeout(Duration::from_secs(8));

    match req.call() {
        Ok(resp) => Ok(resp.into_json::<Value>().ok().as_ref().and_then(first_model_id)),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            Err("That key was rejected — double-check you pasted the whole thing.".into())
        }
        Err(ureq::Error::Status(code, _)) => Err(format!("The provider returned an error (HTTP {code}).")),
        Err(_) => Err("Couldn't reach the provider — check your internet connection.".into()),
    }
}
```

#### `studio/src-tauri/src/projects.rs`
```rust
// Projects — a project is a working directory + a name + run history. Stored in
// ~/.hermes-max/studio/projects.json (the user never hears "working directory").
// The actual build files live wherever the user chose; Studio just remembers the
// path and opens the agent there.
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

#[derive(Serialize, Deserialize, Clone)]
pub struct Project {
    pub id: String,
    pub name: String,
    pub dir: String,
    #[serde(default)]
    pub prompt: Option<String>,
    pub created_ts: f64,
    #[serde(default)]
    pub last_run_ts: Option<f64>,
    #[serde(default)]
    pub last_status: Option<String>,
    #[serde(default)]
    pub last_step: Option<i64>,
    #[serde(default)]
    pub last_total: Option<i64>,
    #[serde(default)]
    pub lifetime_cost_usd: f64,
    #[serde(default)]
    pub lifetime_tokens: i64,
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn studio_dir() -> PathBuf {
    let d = home().join(".hermes-max").join("studio");
    let _ = std::fs::create_dir_all(&d);
    d
}

fn projects_json() -> PathBuf {
    studio_dir().join("projects.json")
}

fn now_ts() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn slug(name: &str) -> String {
    let s: String = name
        .trim()
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect();
    let s = s.trim_matches('-').replace("--", "-");
    if s.is_empty() { "project".into() } else { s }
}

fn load_all() -> Vec<Project> {
    std::fs::read_to_string(projects_json())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_all(list: &[Project]) -> Result<(), String> {
    let json = serde_json::to_string_pretty(list).map_err(|e| e.to_string())?;
    std::fs::write(projects_json(), json).map_err(|e| e.to_string())
}

fn unique_id(base: &str, existing: &[Project]) -> String {
    let mut id = base.to_string();
    let mut n = 2;
    while existing.iter().any(|p| p.id == id) {
        id = format!("{base}-{n}");
        n += 1;
    }
    id
}

// ── tauri commands ───────────────────────────────────────────────────────────
#[tauri::command]
pub fn list_projects() -> Vec<Project> {
    let mut list = load_all();
    list.sort_by(|a, b| {
        b.last_run_ts
            .unwrap_or(b.created_ts)
            .partial_cmp(&a.last_run_ts.unwrap_or(a.created_ts))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    list
}

#[tauri::command]
pub fn create_project(name: String, dir: Option<String>, new_folder: bool) -> Result<Project, String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err("Give your project a name.".into());
    }
    let mut list = load_all();

    let target: PathBuf = if new_folder || dir.as_deref().map(|s| s.trim().is_empty()).unwrap_or(true) {
        // create ~/Projects/<slug> (uniquified)
        let root = home().join("Projects");
        let mut base = root.join(slug(&name));
        let mut n = 2;
        while base.exists() {
            base = root.join(format!("{}-{n}", slug(&name)));
            n += 1;
        }
        std::fs::create_dir_all(&base).map_err(|e| format!("couldn't create the folder: {e}"))?;
        base
    } else {
        let p = PathBuf::from(dir.unwrap());
        if !p.is_dir() {
            return Err("That folder doesn't exist.".into());
        }
        p
    };

    let project = Project {
        id: unique_id(&slug(&name), &list),
        name,
        dir: target.to_string_lossy().to_string(),
        prompt: None,
        created_ts: now_ts(),
        last_run_ts: None,
        last_status: Some("ready".into()),
        last_step: None,
        last_total: None,
        lifetime_cost_usd: 0.0,
        lifetime_tokens: 0,
    };
    list.push(project.clone());
    save_all(&list)?;
    Ok(project)
}

#[tauri::command]
pub fn rename_project(id: String, name: String) -> Result<Project, String> {
    let mut list = load_all();
    let p = list.iter_mut().find(|p| p.id == id).ok_or("unknown project")?;
    p.name = name.trim().to_string();
    let updated = p.clone();
    save_all(&list)?;
    Ok(updated)
}

/// Forget a project (removes the entry only — never deletes the user's files).
#[tauri::command]
pub fn delete_project(id: String) -> Result<(), String> {
    let mut list = load_all();
    list.retain(|p| p.id != id);
    save_all(&list)
}

pub fn name_for_dir(dir: &str) -> Option<String> {
    load_all().into_iter().find(|p| p.dir == dir).map(|p| p.name)
}

/// Update a project's last-run summary from a completed workshop run (matched by
/// directory). Drives the project card status/cost (S2) and the cost total (S5).
pub fn update_stats(dir: &str, step: i64, total: i64, cost: f64, tokens: i64) {
    let mut list = load_all();
    if let Some(p) = list.iter_mut().find(|p| p.dir == dir) {
        p.last_run_ts = Some(now_ts());
        p.last_status = Some("done".into());
        if step > 0 {
            p.last_step = Some(step);
        }
        if total > 0 {
            p.last_total = Some(total);
        }
        p.lifetime_cost_usd += cost;
        p.lifetime_tokens += tokens;
        let _ = save_all(&list);
    }
}

#[tauri::command]
pub fn open_path(path: String) {
    #[cfg(target_os = "linux")]
    let _ = std::process::Command::new("xdg-open").arg(&path).spawn();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg(&path).spawn();
}

#[tauri::command]
pub fn pick_directory(app: AppHandle) -> Option<String> {
    app.dialog()
        .file()
        .blocking_pick_folder()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().to_string())
}
```

#### `studio/src-tauri/src/workshop.rs`
```rust
// Workshop status bridge. The embedded web UI talks to its own same-origin
// Python backend (fully functional); the Studio shell, on a different origin,
// can't read that SSE stream cross-origin. So Rust tails the livelog directly
// (no CORS) and forwards a plain-language status + live cost to the shell via
// `workshop-status` tauri events — keeping the studio bar in sync with the web
// UI's chrome with no second poll loop.
//
// On entering a project we also preset the backend's recent-projects file with
// the project's directory, so the web UI launcher defaults to it and the user
// never types a working directory.
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

#[derive(Default)]
pub struct WorkshopTailer {
    stop: Mutex<Option<Arc<AtomicBool>>>,
}

#[derive(Serialize, Clone, Default)]
pub struct WorkshopStatus {
    pub phrase: String,
    pub step: i64,
    pub total: i64,
    pub cost_usd: f64,
    pub tokens: i64,
    pub running: bool,
    pub event: String,
    pub done: bool,
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn livelog() -> PathBuf {
    let dir = std::env::var("HERMES_MAX_LOG_DIR")
        .or_else(|_| std::env::var("HMX_LOG_DIR"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| home().join(".hermes-max").join("logs"));
    dir.join("live.jsonl")
}

fn ledger_path() -> PathBuf {
    std::env::var("INFERENCE_LEDGER_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| home().join(".hermes-max").join("inference").join("ledger.jsonl"))
}

/// The plain-language phrase for the studio bar — the user's vocabulary, not the
/// system's (S3.4).
fn status_phrase(event: &str, has_guidance: bool) -> &'static str {
    match event {
        "llm_call" if has_guidance => "Applying a correction…",
        "llm_call" => "Thinking…",
        "verify_pass" => "Checking the work… ✓",
        "verify_fail" => "Tests didn't pass — fixing…",
        "trigger" => "The planner is stepping in…",
        "guidance" => "Applying a correction…",
        "step_advance" => "Moving to the next part…",
        "run_complete" => "All done ✓",
        "session_end" => "Done — your turn.",
        "done_rejected" => "Almost there — one more check…",
        _ => "Working…",
    }
}

/// Sum cost + tokens recorded since the workshop opened (the live total).
fn ledger_since(start: f64) -> (f64, i64) {
    let (mut cost, mut tok) = (0.0, 0i64);
    if let Ok(f) = std::fs::File::open(ledger_path()) {
        for line in BufReader::new(f).lines().map_while(Result::ok) {
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if v.get("ts").and_then(Value::as_f64).unwrap_or(0.0) >= start {
                    cost += v.get("cost_usd").and_then(Value::as_f64).unwrap_or(0.0);
                    tok += v.get("in_tok").and_then(Value::as_i64).unwrap_or(0)
                        + v.get("out_tok").and_then(Value::as_i64).unwrap_or(0);
                }
            }
        }
    }
    (cost, tok)
}

/// Preset the backend's recent-projects so the web UI launcher defaults to this
/// project's directory (the user never types a path).
fn write_recent(dir: &str) {
    let p = home().join(".hermes-max").join("ui");
    let _ = std::fs::create_dir_all(&p);
    let path = p.join("recent_projects.json");
    let mut items: Vec<Value> = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    items.retain(|it| it.get("path").and_then(Value::as_str) != Some(dir));
    items.insert(0, json!({ "path": dir, "last_used": now() }));
    items.truncate(50);
    if let Ok(s) = serde_json::to_string_pretty(&items) {
        let _ = std::fs::write(path, s);
    }
}

fn start(app: AppHandle, dir: String) {
    write_recent(&dir);
    let tailer = app.state::<WorkshopTailer>();
    if let Some(prev) = tailer.stop.lock().unwrap().take() {
        prev.store(true, Ordering::SeqCst); // stop any prior tail
    }
    let stop = Arc::new(AtomicBool::new(false));
    *tailer.stop.lock().unwrap() = Some(stop.clone());
    let start_ts = now();

    std::thread::spawn(move || {
        let path = livelog();
        let mut offset = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        let mut st = WorkshopStatus { running: false, phrase: "Ready when you are.".into(), ..Default::default() };
        let mut persisted = false;
        let name = crate::projects::name_for_dir(&dir).unwrap_or_else(|| "Your project".into());
        let mut fail_streak = 0i64;
        while !stop.load(Ordering::SeqCst) {
            if let Ok(mut f) = std::fs::File::open(&path) {
                let len = f.metadata().map(|m| m.len()).unwrap_or(0);
                if len > offset {
                    let _ = f.seek(SeekFrom::Start(offset));
                    let reader = BufReader::new(&mut f);
                    for line in reader.lines().map_while(Result::ok) {
                        offset += line.len() as u64 + 1;
                        let Ok(v) = serde_json::from_str::<Value>(&line) else { continue };
                        if v.get("kind").and_then(Value::as_str) != Some("span") {
                            continue;
                        }
                        let Some(ev) = v.get("span").and_then(Value::as_str).and_then(|s| s.strip_prefix("conductor.")) else { continue };
                        let has_guidance = v.get("has_guidance").and_then(Value::as_bool).unwrap_or(false);
                        st.event = ev.to_string();
                        st.phrase = status_phrase(ev, has_guidance).to_string();
                        if let Some(s) = v.get("step").and_then(Value::as_i64) { st.step = s; }
                        if let Some(t) = v.get("total").and_then(Value::as_i64) { st.total = t; }
                        if ev == "run_complete" || ev == "session_end" {
                            st.running = false;
                            st.done = ev == "run_complete";
                        } else {
                            st.running = true;
                            persisted = false; // a new turn started
                        }
                        // S4.1 — native notifications on milestone events.
                        match ev {
                            "verify_fail" => {
                                fail_streak += 1;
                                if fail_streak == 3 && crate::notify::prefs().attention {
                                    crate::notify::send(&app, &format!("{name} needs attention"),
                                        "Tests haven't passed — you may want to steer it.");
                                }
                            }
                            "verify_pass" | "step_advance" => fail_streak = 0,
                            "trigger" => {
                                if crate::notify::prefs().conductor && !crate::notify::focused(&app) {
                                    crate::notify::send(&app, &format!("Planner stepped in on {name}"),
                                        "The cloud planner is correcting the build.");
                                }
                            }
                            "done_rejected" => {
                                if crate::notify::prefs().complete {
                                    crate::notify::send(&app, &format!("{name}: almost done"),
                                        "One more check before it's ready.");
                                }
                            }
                            _ => {}
                        }
                    }
                }
            }
            let (cost, tok) = ledger_since(start_ts);
            st.cost_usd = cost;
            st.tokens = tok;
            let _ = app.emit("workshop-status", st.clone());
            // keep the tray tooltip in step with the build (walk-away mode)
            let tip = if st.running {
                format!("Building {name}… step {}/{}", st.step.max(1), st.total.max(1))
            } else if st.done {
                format!("Hermes Studio — {name} is ready")
            } else {
                "Hermes Studio — idle".to_string()
            };
            crate::tray::set_tooltip(&app, &tip);
            if st.done && !persisted {
                crate::projects::update_stats(&dir, st.step, st.total, st.cost_usd, st.tokens);
                if crate::notify::prefs().complete {
                    crate::notify::send(&app, &format!("✓ {name} is ready"),
                        &format!("Built · ${:.2}", st.cost_usd));
                }
                persisted = true;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    });
}

#[tauri::command]
pub fn start_workshop(app: AppHandle, dir: String) {
    start(app, dir);
}

#[tauri::command]
pub fn stop_workshop(app: AppHandle) {
    if let Some(prev) = app.state::<WorkshopTailer>().stop.lock().unwrap().take() {
        prev.store(true, Ordering::SeqCst);
    }
}
```

#### `studio/src-tauri/src/notify.rs`
```rust
// Native notifications (tauri-plugin-notification → libnotify on Linux). Fired
// from the workshop livelog tailer on milestone events, gated by per-event
// settings (studio.conf) and, for some, by whether the window is focused — so a
// walk-away build pings you when it's done or needs a decision.
use serde_json::Value;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

pub struct NotifyPrefs {
    pub master: bool,
    pub complete: bool,
    pub attention: bool,
    pub conductor: bool,
}

pub fn prefs() -> NotifyPrefs {
    let s = crate::config::load().settings;
    let b = |k: &str| s.get(k).and_then(Value::as_bool).unwrap_or(true);
    NotifyPrefs {
        master: b("notifications"),
        complete: b("notify_complete"),
        attention: b("notify_attention"),
        conductor: b("notify_conductor"),
    }
}

pub fn focused(app: &AppHandle) -> bool {
    app.get_webview_window("main")
        .and_then(|w| w.is_focused().ok())
        .unwrap_or(false)
}

pub fn send(app: &AppHandle, title: &str, body: &str) {
    if !prefs().master {
        return;
    }
    let _ = app.notification().builder().title(title).body(body).show();
}
```

#### `studio/src-tauri/src/tray.rs`
```rust
// System tray — keeps Studio alive in the background for walk-away builds. The
// window can be closed (hidden) while the sidecar keeps building; the tray
// tooltip reflects build state, and clicking the icon brings the window back.
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};

pub const TRAY_ID: &str = "main";

fn show(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "tray_open", "Open", true, None::<&str>)?;
    let newp = MenuItem::with_id(app, "tray_new", "New Project…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "tray_quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &newp, &quit])?;

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip("Hermes Studio — idle")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "tray_open" => show(app),
            "tray_new" => {
                show(app);
                let _ = app.emit("tray-new-project", ());
            }
            "tray_quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show(tray.app_handle());
            }
        });

    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
    }
    builder.build(app)?;
    Ok(())
}

pub fn set_tooltip(app: &AppHandle, text: &str) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(text));
    }
}
```

### A.2  Shell build config

#### `studio/package.json`
```json
{
  "name": "hermes-studio",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "description": "Hermes Studio — a Tauri 2 desktop appliance wrapping the hermes-max web UI as a friendly, zero-terminal engine.",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "tauri": "tauri"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "@tauri-apps/api": "^2.1.1",
    "@tauri-apps/plugin-notification": "^2.2.0",
    "@tauri-apps/plugin-fs": "^2.2.0",
    "@tauri-apps/plugin-shell": "^2.2.0",
    "@tauri-apps/plugin-dialog": "^2.2.0"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2.1.0",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.6.3",
    "vite": "^5.4.11"
  }
}
```

#### `studio/vite.config.ts`
```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed dev port and no auto-clear so its CLI can attach.
// The shell is a tiny SPA; the heavy lifting is the embedded web UI served by
// the Python backend, which Studio points a separate webview at.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: { target: "es2021", outDir: "dist", emptyOutDir: true },
});
```

#### `studio/tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2021",
    "useDefineForClassFields": true,
    "lib": ["ES2021", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

#### `studio/tsconfig.node.json`
```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

#### `studio/index.html`
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="dark" />
    <title>Hermes Studio</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

#### `studio/postcss.config.js`
```js
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
```

### A.3  Shell source — seam + libs (`src/lib/`, `src/main.tsx`, `src/App.tsx`)

#### `studio/src/main.tsx`
```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

#### `studio/src/App.tsx`
```tsx
// The Studio shell router. Two worlds (S architecture):
//   • SHELL mode — first-run / project list / settings (this React app)
//   • WORKSHOP mode — a thin studio bar over the embedded Phase 0-7 web UI
// Switching to a project enters the workshop; ← Projects returns to the shell.
//
// On launch we show the Loading screen until the Rust side emits `stack-ready`
// (the Python sidecar is up), then probe capabilities to decide first-run vs the
// project list. A fallback timer guarantees we never hang on Loading, and in a
// plain browser (no Tauri) we drop straight to first-run so the shell is dev-able.
import { useEffect, useState } from "react";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { IS_TAURI, listen } from "./lib/tauri";
import { probeCapabilities, type DetectResult } from "./lib/detect";
import type { Project } from "./lib/projects";
import { Loading } from "./screens/Loading";
import { FirstRun } from "./screens/FirstRun";
import { Projects } from "./screens/Projects";
import { Settings } from "./screens/Settings";
import { Workshop } from "./screens/Workshop";

type Screen = "loading" | "firstrun" | "projects" | "settings";

export default function App() {
  const [screen, setScreen] = useState<Screen>("loading");
  const [active, setActive] = useState<Project | null>(null);
  const [detect, setDetect] = useState<DetectResult | null>(null);

  useEffect(() => {
    let un: UnlistenFn | undefined;
    let settled = false;
    const decide = async () => {
      if (settled) return;
      settled = true;
      try {
        const d = await probeCapabilities();
        setDetect(d);
        setScreen(d.suggested_mode === "NeedsSetup" || !d.hermes_present ? "firstrun" : "projects");
      } catch {
        setScreen("firstrun"); // browser dev or backend not ready — keep the shell usable
      }
    };
    listen("stack-ready", () => decide()).then((u) => (un = u));
    const fb = setTimeout(decide, IS_TAURI ? 6000 : 300);
    return () => { if (un) un(); clearTimeout(fb); };
  }, []);

  // Tray "New Project…" brings us back to the project list (S4.2).
  useEffect(() => {
    let un: UnlistenFn | undefined;
    listen("tray-new-project", () => { setActive(null); setScreen("projects"); }).then((u) => (un = u));
    return () => { if (un) un(); };
  }, []);

  const refreshDetect = () => probeCapabilities().then(setDetect).catch(() => void 0);

  if (active) return <Workshop project={active} detect={detect} onExit={() => { setActive(null); setScreen("projects"); }} />;
  if (screen === "loading") return <Loading />;
  if (screen === "firstrun") return <FirstRun detect={detect} onReady={() => { refreshDetect(); setScreen("projects"); }} />;
  if (screen === "settings") return <Settings detect={detect} onBack={() => setScreen("projects")} onChanged={refreshDetect} />;
  return <Projects onOpen={setActive} onSettings={() => setScreen("settings")} />;
}
```

#### `studio/src/lib/tauri.ts`
```ts
// tauri.ts — the SINGLE seam to Tauri's API. Every invoke()/listen() in the
// shell goes through here and nowhere else, so the shell can run in a plain
// browser during development (the mock rejects/no-ops) and against the real app
// unchanged. This is the same discipline as the web UI's lib/api + lib/events.
import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen, type EventCallback, type UnlistenFn } from "@tauri-apps/api/event";

export const IS_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export function invoke<T = unknown>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!IS_TAURI) return Promise.reject(new Error(`mock invoke (no Tauri): ${cmd}`));
  return tauriInvoke<T>(cmd, args);
}

export function listen<T = unknown>(event: string, cb: EventCallback<T>): Promise<UnlistenFn> {
  if (!IS_TAURI) return Promise.resolve((() => {}) as UnlistenFn);
  return tauriListen<T>(event, cb);
}
```

#### `studio/src/lib/detect.ts`
```ts
// Capability detection + stack health — thin typed wrappers over the Rust
// commands (detect.rs / sidecar.rs), all routed through the single Tauri seam.
import { invoke } from "./tauri";

export type SuggestedMode = "Local" | "Cloud" | "NeedsSetup";

export interface DetectResult {
  hermes_present: boolean;
  hermes_version: string | null;
  endpoint_configured: boolean;
  endpoint_url: string | null;
  endpoint_reachable: boolean | null;
  endpoint_model: string | null;
  keys_configured: string[];
  suggested_mode: SuggestedMode;
}

export interface EndpointProbe {
  ok: boolean;
  latency_ms: number | null;
  model: string | null;
  error: string | null;
}

export interface StackStatus {
  python_server: boolean;
  mcp_servers: [string, boolean][];
  hermes_present: boolean;
  active_run: string | null;
}

export const probeCapabilities = () => invoke<DetectResult>("probe_capabilities");
export const probeEndpoint = (url: string) => invoke<EndpointProbe>("probe_endpoint", { url });
export const stackHealth = () => invoke<StackStatus>("stack_health");
export const startStack = () => invoke<StackStatus>("start_stack");
export const stopStack = () => invoke("stop_stack");
```

#### `studio/src/lib/firstrun.ts`
```ts
// First-run actions — typed wrappers over the Rust commands that configure the
// AI source. Endpoint + keys are stored Rust-side (studio.conf + OS keychain)
// and injected into the Python sidecar's environment, which the agent inherits —
// so the shell never needs to cross-origin POST to the backend.
import { invoke } from "./tauri";

export interface Provider {
  id: string;
  name: string;
  env: string;        // the env var the agent reads
  keyUrl: string;     // where to get a key
  pricingUrl: string;
  free?: boolean;
}

// Preset providers — endpoints/models are configured repo-side; here it's just
// "add a key if needed". Free tiers are surfaced first.
export const PROVIDERS: Provider[] = [
  { id: "groq", name: "Groq", env: "GROQ_API_KEY", keyUrl: "https://console.groq.com/keys", pricingUrl: "https://groq.com/pricing", free: true },
  { id: "cerebras", name: "Cerebras", env: "CEREBRAS_API_KEY", keyUrl: "https://cloud.cerebras.ai/", pricingUrl: "https://cerebras.ai/inference", free: true },
  { id: "gemini", name: "Gemini", env: "GEMINI_API_KEY", keyUrl: "https://aistudio.google.com/apikey", pricingUrl: "https://ai.google.dev/pricing", free: true },
  { id: "openrouter", name: "OpenRouter", env: "OPENROUTER_API_KEY", keyUrl: "https://openrouter.ai/keys", pricingUrl: "https://openrouter.ai/models", free: true },
  { id: "deepseek", name: "DeepSeek", env: "DEEPSEEK_API_KEY", keyUrl: "https://platform.deepseek.com/api_keys", pricingUrl: "https://api-docs.deepseek.com/quick_start/pricing" },
  { id: "deepinfra", name: "DeepInfra", env: "DEEPINFRA_API_KEY", keyUrl: "https://deepinfra.com/dash/api_keys", pricingUrl: "https://deepinfra.com/pricing" },
  { id: "anthropic", name: "Anthropic", env: "ANTHROPIC_API_KEY", keyUrl: "https://console.anthropic.com/settings/keys", pricingUrl: "https://www.anthropic.com/pricing" },
  { id: "openai", name: "OpenAI", env: "OPENAI_API_KEY", keyUrl: "https://platform.openai.com/api-keys", pricingUrl: "https://openai.com/api/pricing" },
  { id: "together", name: "Together", env: "TOGETHER_API_KEY", keyUrl: "https://api.together.ai/settings/api-keys", pricingUrl: "https://www.together.ai/pricing" },
];

export interface ApplyResult { ok: boolean; error?: string; model?: string | null }

// Validate + persist a local OpenAI-compatible endpoint, then restart the stack
// so the backend (and the agent it spawns) pick it up. `force` saves even when
// the probe can't confirm a model list (slow/remote endpoint, or one behind a
// key) — the endpoint is still used.
export const configureEndpoint = (url: string, force = false) =>
  invoke<ApplyResult>("configure_endpoint", { url, force });

// Validate + store a provider key in the OS keychain, then restart the stack.
export const saveProviderKey = (provider: string, env: string, key: string) =>
  invoke<ApplyResult>("save_provider_key", { provider, env, key });

export const openUrl = (url: string) => invoke("open_url", { url });
```

#### `studio/src/lib/projects.ts`
```ts
// Project state — typed wrappers over the Rust project commands (projects.rs).
// A "project" is a working directory + a name + run history; the user never
// hears "working directory" or "cwd".
import { invoke } from "./tauri";

export interface Project {
  id: string;
  name: string;
  dir: string;                 // the actual build directory (never shown raw to users)
  prompt?: string | null;      // most recent prompt
  created_ts: number;
  last_run_ts?: number | null;
  last_status?: string | null; // "ready" | "building" | "done" | "attention"
  last_step?: number | null;
  last_total?: number | null;
  lifetime_cost_usd?: number;
  lifetime_tokens?: number;
}

export const listProjects = () => invoke<Project[]>("list_projects");
export const createProject = (name: string, dir: string | null, newFolder: boolean) =>
  invoke<Project>("create_project", { name, dir, newFolder });
export const renameProject = (id: string, name: string) =>
  invoke<Project>("rename_project", { id, name });
export const deleteProject = (id: string) => invoke("delete_project", { id });
export const openProjectFolder = (dir: string) => invoke("open_path", { path: dir });
export const pickDirectory = () => invoke<string | null>("pick_directory");
```

#### `studio/src/lib/workshop.ts`
```ts
// Workshop status — typed wrappers over the Rust livelog bridge. The studio bar
// stays in sync with the embedded web UI's chrome via these `workshop-status`
// events (Rust tails the livelog; the cross-origin shell can't read the SSE).
import { invoke, listen } from "./tauri";

export interface WorkshopStatus {
  phrase: string;
  step: number;
  total: number;
  cost_usd: number;
  tokens: number;
  running: boolean;
  event: string;
  done: boolean;
}

export const startWorkshop = (dir: string) => invoke("start_workshop", { dir });
export const stopWorkshop = () => invoke("stop_workshop");
export const onWorkshopStatus = (cb: (s: WorkshopStatus) => void) =>
  listen<WorkshopStatus>("workshop-status", (e) => cb(e.payload));
```

#### `studio/src/lib/studioConfig.ts`
```ts
// studio.conf access — the endpoint/provider + display/notification settings.
import { invoke } from "./tauri";

export interface StudioConfig {
  endpoint_url?: string | null;
  provider?: string | null;
  settings?: Record<string, boolean | string>;
}

export const loadStudioConfig = () => invoke<StudioConfig>("load_studio_config");
export const saveStudioSettings = (settings: Record<string, boolean | string>) =>
  invoke("save_studio_settings", { settings });
```

### A.4  Shell source — screens (`src/screens/`)

#### `studio/src/screens/Loading.tsx`
```tsx
// The launch screen shown until the Rust side emits `stack-ready`. A calm,
// centered wordmark with a soft pulse — NOT a spinner, NOT log output. It
// resolves to first-run or the project list within a few seconds.
export function Loading({ detail }: { detail?: string }) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-bg-base">
      <div className="flex items-center gap-3">
        <span className="inline-block h-3 w-3 animate-pulse2 rounded-full bg-conductor" aria-hidden />
        <span className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Hermes Studio</span>
      </div>
      <p className="text-xs text-mist-500">{detail ?? "Warming up the workshop…"}</p>
    </div>
  );
}
```

#### `studio/src/screens/FirstRun.tsx`
```tsx
// First-run: detect-and-bless. ONE welcoming screen that branches on what was
// probed — not a wizard. Three states:
//   A  endpoint already reachable → "Your AI is ready" → open a project
//   B  nothing configured → connect a local endpoint OR a cloud provider key
//   C  hermes binary missing → install prompt + re-check
import { useState } from "react";
import { openUrl } from "../lib/firstrun";
import { probeCapabilities, type DetectResult } from "../lib/detect";
import { ConnectAI } from "../components/ConnectAI";
import { StatusDot } from "../components/StatusDot";

function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try { return new URL(url).host; } catch { return url; }
}

export function FirstRun({ detect, onReady }: { detect: DetectResult | null; onReady: () => void }) {
  const [d, setD] = useState<DetectResult | null>(detect);
  // recheck after the user installs hermes / changes something
  const recheck = () => probeCapabilities().then(setD).catch(() => void 0);

  const state: "A" | "B" | "C" =
    d && !d.hermes_present ? "C" : d?.endpoint_reachable ? "A" : "B";

  return (
    <div className="flex h-screen items-center justify-center bg-bg-base px-6">
      <div className="w-full max-w-lg">
        <div className="mb-6 flex items-center gap-2">
          <StatusDot tone="accent" />
          <span className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Hermes Studio</span>
        </div>

        {state === "A" && <ReadyState d={d!} onReady={onReady} />}
        {state === "B" && <ConnectState onConnected={onReady} />}
        {state === "C" && <InstallState onRecheck={recheck} />}
      </div>
    </div>
  );
}

function ReadyState({ d, onReady }: { d: DetectResult; onReady: () => void }) {
  return (
    <div className="space-y-3">
      <h1 className="text-lg font-medium text-mist-100">Your AI is ready.</h1>
      {d.endpoint_model && <p className="font-mono text-sm text-mist-300">{d.endpoint_model}</p>}
      <p className="text-sm text-mist-400">Running at {hostOf(d.endpoint_url) || "your endpoint"}</p>
      <button type="button" onClick={onReady}
        className="mt-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">
        Open a project →
      </button>
    </div>
  );
}

function ConnectState({ onConnected }: { onConnected: () => void }) {
  const [done, setDone] = useState(false);
  if (done) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-good"><StatusDot tone="good" /> Connected</div>
        <button type="button" onClick={onConnected}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">Open a project →</button>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <h1 className="text-lg font-medium text-mist-100">Connect your AI to get started.</h1>
      <ConnectAI onConnected={() => setDone(true)} />
    </div>
  );
}

function InstallState({ onRecheck }: { onRecheck: () => void }) {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-medium text-mist-100">One thing to install first.</h1>
      <p className="text-sm text-mist-400">
        Hermes Agent needs to be installed — it's the AI engine that does the work.
      </p>
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => openUrl("https://github.com/patrickbdevaney/hermes-max#install")}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">Install Hermes →</button>
        <button type="button" onClick={onRecheck} className="text-xs text-mist-300 hover:text-mist-100">Already installed? Check again</button>
      </div>
    </div>
  );
}
```

#### `studio/src/screens/Projects.tsx`
```tsx
// Projects — the primary loop. A card grid (the "+ New Project" card is always
// first). Opening a project enters the workshop. An exist-OK fix-it banner shows
// at the top when the configured AI has gone unreachable (S1.3) — advisory, not
// a wall.
import { useEffect, useState } from "react";
import { listProjects, createProject, pickDirectory, type Project } from "../lib/projects";
import { probeCapabilities, type DetectResult } from "../lib/detect";
import { computeShadow, fmtMoney } from "../lib/shadow";
import { ProjectCard } from "../components/ProjectCard";

export function Projects({ onOpen, onSettings }: { onOpen: (p: Project) => void; onSettings: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [detect, setDetect] = useState<DetectResult | null>(null);
  const [creating, setCreating] = useState(false);

  const refresh = () => listProjects().then(setProjects).catch(() => setProjects([]));
  useEffect(() => { refresh(); probeCapabilities().then(setDetect).catch(() => void 0); }, []);

  const aiDown = detect && detect.suggested_mode === "NeedsSetup";

  // A gentle running total across all projects (S5.2).
  const totalCost = projects.reduce((s, p) => s + (p.lifetime_cost_usd ?? 0), 0);
  const totalTokens = projects.reduce((s, p) => s + (p.lifetime_tokens ?? 0), 0);
  const totalShadow = computeShadow(totalCost, totalTokens);
  const built = projects.filter((p) => p.last_run_ts).length;

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-2 flex items-center justify-between">
        <h1 className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Your projects</h1>
        <button type="button" onClick={onSettings}
          className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-300 hover:bg-ink-850">Settings</button>
      </header>
      {built > 0 && (
        <p className="mb-6 text-xs text-mist-500">
          You've built {built} project{built === 1 ? "" : "s"} for{" "}
          <span className="font-mono text-good">{fmtMoney(totalCost)}</span>
          {totalShadow.savedUsd > 0 && <> — saved ~<span className="font-mono text-conductor">{fmtMoney(totalShadow.savedUsd)}</span> vs premium AI</>}
        </p>
      )}

      {aiDown && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-warn/40 bg-warn-soft/15 px-3 py-2 text-xs text-warn">
          <span>Your AI isn't responding — projects still open, but builds won't run until it's reconnected.</span>
          <button type="button" onClick={onSettings} className="shrink-0 underline">Check settings</button>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <button type="button" onClick={() => setCreating(true)}
          className="flex min-h-[120px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-ink-700 text-sm text-mist-300 transition-colors hover:border-accent hover:text-mist-100">
          <span className="text-2xl">+</span> New Project
        </button>
        {projects.map((p) => <ProjectCard key={p.id} project={p} onOpen={onOpen} onChanged={refresh} />)}
      </div>

      {creating && <NewProject onCancel={() => setCreating(false)} onCreated={(p) => { setCreating(false); refresh(); onOpen(p); }} />}
    </div>
  );
}

function NewProject({ onCancel, onCreated }: { onCancel: () => void; onCreated: (p: Project) => void }) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [dir, setDir] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function pick() {
    const d = await pickDirectory();
    if (d) setDir(d);
  }
  async function go() {
    setBusy(true); setErr(null);
    try {
      const p = await createProject(name.trim(), mode === "existing" ? dir : null, mode === "new");
      onCreated(p);
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-6" onClick={onCancel}>
      <div className="w-full max-w-md rounded-xl border border-ink-700 bg-ink-overlay p-5" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 font-display text-lg font-semibold text-mist-100">What are you building?</h2>

        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
          placeholder="e.g. A todo app with user accounts"
          className="w-full rounded-md border border-ink-700 bg-ink-input px-3 py-2 text-sm text-mist-100 outline-none focus:border-accent" />

        <p className="mt-4 mb-1 text-xs text-mist-400">Where should I put the files?</p>
        <div className="flex flex-col gap-2 text-sm">
          <label className="flex items-center gap-2 text-mist-200">
            <input type="radio" checked={mode === "new"} onChange={() => setMode("new")} className="accent-current text-accent" />
            Create a new folder for me
          </label>
          <label className="flex items-center gap-2 text-mist-200">
            <input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")} className="accent-current text-accent" />
            Use an existing folder
          </label>
          {mode === "existing" && (
            <div className="flex gap-2 pl-6">
              <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="/path/to/folder"
                className="flex-1 rounded-md border border-ink-700 bg-ink-input px-2 py-1.5 font-mono text-xs text-mist-100 outline-none focus:border-accent" />
              <button type="button" onClick={pick} className="rounded-md border border-ink-700 px-2 py-1.5 text-xs text-mist-200 hover:bg-ink-850">Browse…</button>
            </div>
          )}
        </div>

        {err && <p className="mt-3 text-xs text-bad">{err}</p>}

        <div className="mt-5 flex items-center justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-md px-3 py-1.5 text-xs text-mist-400 hover:text-mist-100">Cancel</button>
          <button type="button" onClick={go} disabled={busy || !name.trim() || (mode === "existing" && !dir.trim())}
            className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-ink-950 hover:opacity-90 disabled:opacity-40">
            {busy ? "Setting up…" : "Let's go →"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

#### `studio/src/screens/Workshop.tsx`
```tsx
// The workshop: a thin studio bar over the full Phase 0-7 web UI. The web UI
// loads in an iframe pointed at the Python backend it's served by, so it talks
// to its own same-origin backend and is FULLY functional and unmodified — the
// composer, conductor swimlane, cost shadow, controls, everything. The studio
// bar adds the friendly chrome: ← Projects, an editable project name, a plain-
// language status phrase, and the live cost — fed by Rust's livelog bridge.
import { useEffect, useRef, useState } from "react";
import { renameProject, openProjectFolder, type Project } from "../lib/projects";
import { startWorkshop, stopWorkshop, onWorkshopStatus, type WorkshopStatus } from "../lib/workshop";
import { computeShadow, fmtMoney, fmtMultiple } from "../lib/shadow";
import { StatusDot } from "../components/StatusDot";
import { CompletionCard } from "../components/CompletionCard";
import type { DetectResult } from "../lib/detect";

const WEB_UI_URL = "http://127.0.0.1:7080";

export function Workshop({ project, detect, onExit }:
  { project: Project; detect: DetectResult | null; onExit: () => void }) {
  void detect;
  const [status, setStatus] = useState<WorkshopStatus | null>(null);
  const [name, setName] = useState(project.name);
  const [receipt, setReceipt] = useState<WorkshopStatus | null>(null);
  const wasRunning = useRef(false);

  useEffect(() => {
    startWorkshop(project.dir).catch(() => void 0);
    const un = onWorkshopStatus((s) => {
      setStatus(s);
      // S5: when a run settles into completion, surface the receipt once.
      if (wasRunning.current && !s.running && s.done) setReceipt(s);
      wasRunning.current = s.running;
    });
    return () => { un.then((f) => f()); stopWorkshop().catch(() => void 0); };
  }, [project.dir]);

  function exit() {
    if (status?.running && !confirm("A build is still running. Leave the workshop anyway?")) return;
    onExit();
  }
  function commitName() {
    const n = name.trim();
    if (n && n !== project.name) renameProject(project.id, n).catch(() => void 0);
  }

  const running = !!status?.running;
  const phrase = running ? status!.phrase : status?.done ? "All done ✓" : "What should I build?";
  const cost = status?.cost_usd ?? 0;
  const shadow = computeShadow(cost, status?.tokens ?? 0);

  return (
    <div className="flex h-screen flex-col bg-bg-base">
      <div className="flex h-9 shrink-0 items-center gap-3 border-b border-ink-800 px-3 text-xs">
        <button type="button" onClick={exit} className="shrink-0 text-mist-300 hover:text-mist-100">← Projects</button>
        <StatusDot tone={running ? "accent" : status?.done ? "good" : "muted"} pulse={running} />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={commitName}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          className="w-40 shrink-0 truncate bg-transparent font-medium text-mist-100 outline-none focus:text-accent"
          aria-label="project name"
        />
        <span className="text-mist-600">•</span>
        <span className="min-w-0 flex-1 truncate text-mist-300">{phrase}</span>
        {status && status.total > 0 && (
          <span className="shrink-0 font-mono text-mist-500">{status.step}/{status.total}</span>
        )}
        <span className="shrink-0 font-mono text-mist-300" title={shadow.savedUsd > 0 ? `saved ${fmtMoney(shadow.savedUsd)} (${fmtMultiple(shadow.multiple)}) vs premium AI` : undefined}>
          {fmtMoney(cost)}
        </span>
      </div>

      {/* The full web UI — unmodified, talking to its own same-origin backend. */}
      <iframe title="hermes-max" src={WEB_UI_URL} className="min-h-0 flex-1 border-0" />

      {receipt && (
        <CompletionCard
          name={project.name}
          status={receipt}
          onClose={() => setReceipt(null)}
          onOpenFolder={() => openProjectFolder(project.dir)}
        />
      )}
    </div>
  );
}
```

#### `studio/src/screens/Settings.tsx`
```tsx
// Settings (the studio shell — distinct from the web UI's own settings). Three
// sections: Your AI (change source + test), Notifications (per-event toggles),
// Display (reduced motion). Persisted to studio.conf via save_studio_settings.
import { useEffect, useState } from "react";
import { probeCapabilities, type DetectResult } from "../lib/detect";
import { loadStudioConfig, saveStudioSettings } from "../lib/studioConfig";
import { ConnectAI } from "../components/ConnectAI";

type Prefs = Record<string, boolean>;
const DEFAULTS: Prefs = {
  notifications: true, notify_complete: true, notify_attention: true, notify_conductor: true, reduce_motion: false,
};

function host(url?: string | null): string {
  if (!url) return "";
  try { return new URL(url).host; } catch { return url; }
}

export function Settings({ detect, onBack, onChanged }:
  { detect: DetectResult | null; onBack: () => void; onChanged: () => void }) {
  const [prefs, setPrefs] = useState<Prefs>(DEFAULTS);
  const [d, setD] = useState<DetectResult | null>(detect);
  const [changing, setChanging] = useState(false);

  useEffect(() => {
    loadStudioConfig().then((c) => setPrefs({ ...DEFAULTS, ...(c.settings as Prefs) })).catch(() => void 0);
  }, []);
  useEffect(() => { applyDisplay(prefs.reduce_motion); }, [prefs.reduce_motion]);

  function set(key: string, val: boolean) {
    const next = { ...prefs, [key]: val };
    setPrefs(next);
    saveStudioSettings(next).catch(() => void 0);
  }
  function applyDisplay(reduce: boolean) {
    if (typeof document !== "undefined") document.documentElement.dataset.reduceMotion = reduce ? "1" : "0";
  }
  function retest() {
    probeCapabilities().then((r) => { setD(r); onChanged(); }).catch(() => void 0);
  }

  const aiSummary = d?.endpoint_url
    ? `Using your own model at ${host(d.endpoint_url)}${d.endpoint_model ? ` · ${d.endpoint_model}` : ""}`
    : d?.keys_configured.length
      ? `Using ${d.keys_configured[0]} — pay-as-you-go per project`
      : "No AI connected yet";

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <button type="button" onClick={onBack} className="mb-4 text-xs text-mist-400 hover:text-mist-100">← Projects</button>
      <h1 className="mb-6 font-display text-2xl font-semibold tracking-tight2 text-mist-100">Settings</h1>

      <Section title="Your AI">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-mist-300">{aiSummary}</p>
          <div className="flex shrink-0 gap-2">
            <button type="button" onClick={retest} className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 hover:bg-ink-850">Test connection</button>
            <button type="button" onClick={() => setChanging((c) => !c)} className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 hover:bg-ink-850">{changing ? "Cancel" : "Change"}</button>
          </div>
        </div>
        {changing && <div className="mt-4"><ConnectAI onConnected={() => { setChanging(false); retest(); }} /></div>}
      </Section>

      <Section title="Notifications">
        <Toggle label="Notifications" hint="Master switch for desktop alerts." checked={prefs.notifications} onChange={(v) => set("notifications", v)} />
        <Toggle label="Build complete" checked={prefs.notify_complete} onChange={(v) => set("notify_complete", v)} />
        <Toggle label="Needs attention" hint="Tests failing repeatedly." checked={prefs.notify_attention} onChange={(v) => set("notify_attention", v)} />
        <Toggle label="Planner stepped in" hint="When the window isn't focused." checked={prefs.notify_conductor} onChange={(v) => set("notify_conductor", v)} />
      </Section>

      <Section title="Display">
        <Toggle label="Reduced motion" hint="Freeze animations (status is always colour + icon + label)." checked={prefs.reduce_motion} onChange={(v) => set("reduce_motion", v)} />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-4 rounded-lg border border-ink-800 bg-ink-900 p-4">
      <h2 className="mb-3 text-sm font-medium text-mist-200">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Toggle({ label, hint, checked, onChange }:
  { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div><div className="text-sm text-mist-100">{label}</div>{hint && <div className="text-[11px] text-mist-500">{hint}</div>}</div>
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${checked ? "bg-accent" : "bg-ink-700"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-mist-100 transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </button>
    </div>
  );
}
```

### A.5  Shell source — components (`src/components/`)

#### `studio/src/components/ConnectAI.tsx`
```tsx
// The connect-your-AI form — a local OpenAI-compatible endpoint OR a cloud
// provider key. Shared by first-run (State B) and Settings ("change AI"). On
// success it persists Rust-side (studio.conf + keychain), restarts the stack so
// the backend reloads, and calls onConnected.
import { useState } from "react";
import { configureEndpoint, saveProviderKey, openUrl, type Provider } from "../lib/firstrun";
import { ProviderGrid } from "./ProviderGrid";
import { StatusDot } from "./StatusDot";

export function ConnectAI({ onConnected }: { onConnected: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<{ model: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [provider, setProvider] = useState<Provider | null>(null);
  const [key, setKey] = useState("");

  async function testEndpoint(force = false) {
    setBusy(true); setErr(null);
    try {
      const r = await configureEndpoint(url.trim(), force);
      if (r.ok) { setOk({ model: r.model ?? null }); onConnected(); }
      else setErr(r.error ?? "Couldn't connect.");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }
  async function connectKey() {
    if (!provider) return;
    setBusy(true); setErr(null);
    try {
      const r = await saveProviderKey(provider.id, provider.env, key.trim());
      if (r.ok) { setOk({ model: r.model ?? null }); onConnected(); }
      else setErr(r.error ?? "Couldn't connect.");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (ok) {
    return (
      <div className="flex items-center gap-2 text-good">
        <StatusDot tone="good" /> Connected{ok.model ? <span className="font-mono text-mist-300">· {ok.model}</span> : null}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <p className="text-sm text-mist-300">I have my own AI running <span className="text-mist-500">(local model, LM Studio, Ollama…)</span></p>
        <div className="flex gap-2">
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:11434/v1"
            className="flex-1 rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />
          <button type="button" onClick={() => testEndpoint(false)} disabled={busy || !url.trim()}
            className="rounded-md border border-ink-700 px-3 py-2 text-xs text-mist-200 hover:bg-ink-850 disabled:opacity-40">
            {busy ? "Testing…" : "Test connection"}
          </button>
        </div>
        {err && url.trim() && (
          <button type="button" onClick={() => testEndpoint(true)} disabled={busy}
            className="text-[11px] text-accent hover:underline disabled:opacity-40">
            Use this endpoint anyway →
          </button>
        )}
      </div>

      <div className="flex items-center gap-2 text-[11px] text-mist-500">
        <span className="h-px flex-1 bg-ink-800" /> or use a cloud AI service <span className="h-px flex-1 bg-ink-800" />
      </div>

      <ProviderGrid selected={provider?.id} onSelect={(p) => { setProvider(p); setErr(null); }} />

      {provider && (
        <div className="space-y-2">
          <input type="password" value={key} onChange={(e) => setKey(e.target.value)}
            placeholder={`Paste your ${provider.name} API key`}
            className="w-full rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />
          <div className="flex items-center gap-3 text-[11px]">
            <button type="button" onClick={connectKey} disabled={busy || !key.trim()}
              className="rounded-md bg-accent px-3 py-1.5 font-medium text-ink-950 hover:opacity-90 disabled:opacity-40">
              {busy ? "Connecting…" : "Connect"}
            </button>
            <button type="button" onClick={() => openUrl(provider.keyUrl)} className="text-accent hover:underline">Where do I get a key? →</button>
            <button type="button" onClick={() => openUrl(provider.pricingUrl)} className="text-mist-400 hover:text-mist-200">
              {provider.free ? "Free tier available →" : "How much does it cost? →"}
            </button>
          </div>
        </div>
      )}

      {err && <p className="text-xs text-bad">{err}</p>}
    </div>
  );
}
```

#### `studio/src/components/ProviderGrid.tsx`
```tsx
// A grid of cloud AI providers for key entry. Clicking one selects it (the
// FirstRun screen then shows a single password field). Groq is flagged free.
import { PROVIDERS, type Provider } from "../lib/firstrun";

export function ProviderGrid({ selected, onSelect }:
  { selected?: string; onSelect: (p: Provider) => void }) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {PROVIDERS.map((p) => (
        <button key={p.id} type="button" onClick={() => onSelect(p)}
          className={`flex flex-col items-start gap-0.5 rounded-md border px-3 py-2 text-left transition-colors ${
            selected === p.id ? "border-accent bg-accent-soft/15" : "border-ink-700 hover:bg-ink-850"}`}>
          <span className="text-sm font-medium text-mist-100">{p.name}</span>
          {p.free && <span className="text-[10px] text-good">free tier available</span>}
        </button>
      ))}
    </div>
  );
}
```

#### `studio/src/components/ProjectCard.tsx`
```tsx
// A project card — plain-language status, last build, cost, and a Continue
// action. The ⋯ menu opens project actions (open folder, rename, forget).
import { useState } from "react";
import { openProjectFolder, renameProject, deleteProject, type Project } from "../lib/projects";
import { computeShadow, fmtMoney, fmtMultiple } from "../lib/shadow";
import { StatusDot } from "./StatusDot";

const STATUS: Record<string, { tone: "good" | "accent" | "warn" | "muted"; label: string; pulse?: boolean }> = {
  ready: { tone: "muted", label: "Ready" },
  building: { tone: "accent", label: "Building…", pulse: true },
  done: { tone: "good", label: "Done ✓" },
  attention: { tone: "warn", label: "Needs attention ⚠" },
};

function ago(ts?: number | null): string {
  if (!ts) return "Not built yet";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "Last built just now";
  if (s < 3600) return `Last built ${Math.round(s / 60)}m ago`;
  if (s < 86400) return `Last built ${Math.round(s / 3600)}h ago`;
  return `Last built ${Math.round(s / 86400)}d ago`;
}

export function ProjectCard({ project, onOpen, onChanged }:
  { project: Project; onOpen: (p: Project) => void; onChanged: () => void }) {
  const [menu, setMenu] = useState(false);
  const st = STATUS[project.last_status ?? "ready"] ?? STATUS.ready;

  const cost = project.lifetime_cost_usd ?? 0;
  const sv = computeShadow(cost, project.lifetime_tokens ?? 0);
  const savedHint = sv.savedUsd > 0
    ? `saved ~${fmtMoney(sv.savedUsd)} (${fmtMultiple(sv.multiple)} cheaper) vs premium AI`
    : undefined;
  const sub = [
    ago(project.last_run_ts),
    project.last_step && project.last_total ? `step ${project.last_step}/${project.last_total}` : null,
    cost > 0 ? `$${cost.toFixed(2)}` : null,
  ].filter(Boolean).join(" · ");

  async function rename() {
    setMenu(false);
    const name = prompt("Rename project", project.name);
    if (name && name.trim()) { await renameProject(project.id, name.trim()); onChanged(); }
  }
  async function forget() {
    setMenu(false);
    if (confirm(`Forget "${project.name}"? This only removes it from Studio — your files are kept.`)) {
      await deleteProject(project.id); onChanged();
    }
  }

  return (
    <div className="flex flex-col rounded-lg border border-ink-800 bg-ink-900 p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="truncate font-medium text-mist-100" title={project.name}>{project.name}</h3>
        <span className="flex shrink-0 items-center gap-1.5 text-[11px] text-mist-400">
          <StatusDot tone={st.tone} pulse={st.pulse} />{st.label}
        </span>
      </div>
      {project.prompt && <p className="mt-1 line-clamp-2 text-sm text-mist-400">“{project.prompt}”</p>}
      <p className="mt-2 font-mono text-[11px] text-mist-500" title={savedHint}>{sub}</p>

      <div className="mt-3 flex items-center gap-2">
        <button type="button" onClick={() => onOpen(project)}
          className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 hover:opacity-90">
          {project.last_run_ts ? "▶ Continue" : "▶ Open"}
        </button>
        <div className="relative">
          <button type="button" onClick={() => setMenu((m) => !m)} aria-label="project menu"
            className="rounded-md border border-ink-700 px-2 py-1.5 text-xs text-mist-300 hover:bg-ink-850">⋯</button>
          {menu && (
            <div className="absolute left-0 z-10 mt-1 w-40 rounded-md border border-ink-700 bg-ink-overlay py-1 text-xs shadow-lg">
              <button type="button" onClick={() => { setMenu(false); openProjectFolder(project.dir); }}
                className="block w-full px-3 py-1.5 text-left text-mist-200 hover:bg-ink-850">Open folder</button>
              <button type="button" onClick={rename} className="block w-full px-3 py-1.5 text-left text-mist-200 hover:bg-ink-850">Rename</button>
              <button type="button" onClick={forget} className="block w-full px-3 py-1.5 text-left text-bad hover:bg-ink-850">Forget</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

#### `studio/src/components/CompletionCard.tsx`
```tsx
// S5.1 — the completion receipt. A brief, dismissable celebration when a build
// finishes: what it cost, what it would have cost on a frontier model, and the
// savings. Same honest data as the web UI's shadow meter (real ledger tokens
// re-priced at the configured frontier rate), framed as delight not a metric.
import { useEffect } from "react";
import { computeShadow, rateLabel, fmtMoney, FRONTIER } from "../lib/shadow";
import type { WorkshopStatus } from "../lib/workshop";

export function CompletionCard({ name, status, onClose, onOpenFolder }:
  { name: string; status: WorkshopStatus; onClose: () => void; onOpenFolder: () => void }) {
  const r = computeShadow(status.cost_usd, status.tokens);

  // auto-dismiss after a few seconds (dismissable sooner)
  useEffect(() => {
    const t = setTimeout(onClose, 8000);
    return () => clearTimeout(t);
  }, [onClose]);

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-6">
      <div className="pointer-events-auto w-full max-w-lg animate-risein rounded-xl border border-conductor/40 bg-ink-overlay p-5 shadow-2xl">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <span className="text-conductor" aria-hidden>✓</span>
            <h3 className="text-sm font-semibold text-mist-100">{name} is ready</h3>
          </div>
          <button type="button" onClick={onClose} className="text-mist-500 hover:text-mist-100" aria-label="dismiss">✕</button>
        </div>

        <div className="mt-3 space-y-0.5 text-sm">
          <p className="text-mist-200">Built for <span className="font-mono text-good">{fmtMoney(r.actualUsd)}</span></p>
          {r.shadowUsd > 0 && (
            <p className="text-mist-400">
              The same build would cost ~<span className="font-mono text-mist-200">{fmtMoney(r.shadowUsd)}</span> on {FRONTIER.model}
            </p>
          )}
          {r.savedUsd > 0 && (
            <p className="text-conductor">You saved {r.savedPct.toFixed(0)}% 🎉</p>
          )}
        </div>

        <div className="mt-4 flex items-center gap-2">
          <button type="button" onClick={onOpenFolder}
            className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 hover:bg-ink-850">Open the project folder</button>
          <button type="button" onClick={onClose}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 hover:opacity-90">Build something else</button>
        </div>

        <p className="mt-3 text-[10px] text-mist-500">{status.tokens.toLocaleString()} tokens · priced against {rateLabel()}</p>
      </div>
    </div>
  );
}
```

#### `studio/src/components/StatusDot.tsx`
```tsx
// A status dot — colour + (optional) pulse. Always paired with a text label by
// callers, never colour alone (the Phase 0 accessibility contract).
type Tone = "good" | "warn" | "bad" | "accent" | "muted";

const BG: Record<Tone, string> = {
  good: "bg-good", warn: "bg-warn", bad: "bg-bad", accent: "bg-accent", muted: "bg-mist-500",
};

export function StatusDot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${BG[tone]} ${pulse ? "animate-pulse2" : ""}`} aria-hidden />;
}
```
