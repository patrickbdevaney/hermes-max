# Hermes Studio

A Tauri 2 desktop appliance that turns the hermes-max agent stack into a
friendly, zero-terminal experience: **idea in → running, proven product out**.
For someone who has a GPU endpoint (or a cheap API key) and an idea, but who
isn't a terminal power user.

Studio does **not** rewrite anything. It embeds the existing Phase 0–7 web UI as
its engine and wraps it in a native shell with friendly vocabulary.

## Architecture

```
Studio window
├── Shell (React, studio/src)         first-run · projects · settings
└── Workshop                          a thin studio bar + the full web UI
    ├── studio bar                    ← Projects · name · status · cost
    └── <iframe http://127.0.0.1:7080>  the unmodified Phase 0–7 web UI
```

- **Rust side (`src-tauri`)** sidecars the Python backend (`python3 -m ui.server`),
  health-checks it, starts MCP servers via `hm dev`, and tears them down cleanly
  on quit. It owns the AI source: the endpoint lives in `~/.hermes-max/studio.conf`,
  keys live in the OS keychain, and both are injected into the sidecar's
  environment (which the agent inherits) — so the shell never has to cross-origin
  POST to the backend.
- **Status bridge**: the cross-origin shell can't read the backend's SSE, so Rust
  tails the livelog directly and forwards a plain-language status + live cost to
  the studio bar via `workshop-status` events.
- **Native features**: desktop notifications on milestones, a system tray that
  keeps builds running when the window is closed, and a completion receipt with
  the real cost-vs-frontier savings.

## Requirements

- `hermes` on `PATH` (Studio validates and links to install docs; it does not
  install Hermes).
- An OpenAI-compatible endpoint URL **or** a cloud provider API key (Studio
  detects, validates, and configures — it does not download or run models).
- System libraries (Ubuntu 22.04+/24.04): `libwebkit2gtk-4.1-0`,
  `libayatana-appindicator3-1`, `python3`, `libsecret-1-0`. The `.deb` declares
  these as dependencies.

## Run it

```bash
hm studio          # dev build — opens the native window, sidecars the backend
hm studio build    # produce the Linux .deb (target/release/bundle/deb)
```

`hm studio` sets `HERMES_MAX_ROOT` to the repo so the app finds `ui/server`.

### Installed builds

The `.deb` bundles the Tauri binary + the Studio shell assets only — **not** the
Python backend, MCP servers, or any model (per the directive's constraints). An
installed Studio therefore needs the hermes-max repo available and
`HERMES_MAX_ROOT` pointing at it (or the binary installed adjacent to the repo so
the walk-up resolver in `sidecar.rs` finds `ui/server`).

## Verification status

Built and verified in CI/sandbox:

- `cargo check` — clean (the full GTK/webkit2gtk/tray dependency tree compiles).
- `npm run build` — clean (the shell bundles to ~170 KB JS; the 50 MB binary
  budget has ample room).

Not exercised headlessly (require a desktop session / packaging host, but the
code + config for them is complete): launching the GUI window, producing and
installing the `.deb`. `tauri-cli` and `dpkg-deb` are both present, so
`hm studio build` produces the `.deb` on a real Ubuntu desktop.
