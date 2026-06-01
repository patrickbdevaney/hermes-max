# hermes-max UI

A hybrid interface over the hermes-max harness: the **same** TypeScript frontend is
served by `hm` for the localhost **web tier** and (later) wrapped by **Tauri** for an
OS-native **desktop tier**. One frontend, one API contract, two delivery surfaces.

This directory currently implements **Tier 1 (live web view)**, **Tier 2 (onboarding
wizard)**, and **Tier 3 (layered-disclosure L2 + streaming)**:

```
hm ui            # builds the frontend on first run, serves it on 127.0.0.1, opens your browser
```

A fresh install lands on the **wizard** (detect machine → capture provider keys → test
each connection → review), then hands off to the launcher. Once a cloud provider is
configured, `hm ui` opens straight to the launcher; re-enter the wizard any time via the
**⚙ Setup** button.

It renders three layers from a single live view:

- **L0 — ambient**: one plain-language line, a determinate progress bar against the
  `PLAN.md` contract (or honest activity when there's no plan), and the live cost
  `$0.000000`. Glanceable; no jargon.
- **L1 — timeline**: the operator's home screen — tool-call cards (request → latency →
  result), the cheapest-first escalation ladder made visible, verification gates
  (red/green), checkpoints, and the live free-vs-paid cost split.
- **L2 — full detail** (developer). Expand any L1 tool row to its correlated raw span
  subtree — exact tool I/O, attributes, diffs, span events, status. A collapsible
  "full trace" panel embeds the entire OTLP tree (the Phoenix-style view). Fed by the
  OTLP→SSE bridge below.

Everything is fed by the **existing** telemetry — the livelog (`~/.hermes-max/logs/live.jsonl`)
and the cost ledger — with **zero new instrumentation**. The UI is **read-only**: it
visualizes the agent, it does not control it (mid-run control is a deliberate, later
addition that would upgrade the transport to WebSocket).

## Layout

```
ui/
  server/          Python stdlib HTTP+SSE backend (zero pip deps) — taps lib.livelog + lib.inference
    feeds.py         livelog → typed SSE events; ledger → cost ticks; status/cost/config payloads
    config_api.py    Tier 2: key status, key capture, non-secret config writes, live connection probe
    secrets_store.py Tier 2: cross-platform secret store (keychain → .env 600 fallback)
    otlp.py          Tier 3: OTLP/HTTP decoder (protobuf + JSON) + span pub/sub hub → L2 tree
    runs.py          run registry (offset into the global log) + recent-projects store
    security.py      Origin/Host checks, CSRF/token comparison (token optional)
    app.py           routing + SPA static serving + SameSite CSRF cookie
    __main__.py      bootstrap: sticky port (ui.conf), bind 127.0.0.1, auto-open, one-line print
  web/             React + TypeScript + Vite + Tailwind frontend (built to web/dist/)
```

## Running it

```bash
hm ui                 # opens your browser; prints one line:  hermes-max UI  →  http://localhost:7080
hm ui --port 9000     # override the port (otherwise sticky from ~/.hermes-max/ui.conf)
hm ui --no-open       # don't auto-open a browser (the printed URL is the fallback)
hm ui --token         # opt-in: add a bearer token (in the printed URL) for remote/Tailscale exposure
```

The port defaults to **7080** and is **sticky** — persisted to `~/.hermes-max/ui.conf`
so the address is always the same. If 7080 is taken, the next free port is used and
printed. No token is needed on localhost; the page just opens. `hm ui` builds `web/dist`
on first run if it's missing (needs Node/npm once); the API works even if the build
fails — the page shows a "run `npm run build`" hint.

### Developing the frontend

```bash
hm ui --no-open                 # terminal 1: backend on :7080
cd ui/web && npm run dev        # terminal 2: Vite HMR on :5173, proxies /api → :7080
```

The CSRF cookie is set on the first proxied API call, so POSTs work in dev too — no
token to copy.

## API contract (the seam both tiers implement)

All endpoints bind `127.0.0.1`. Tier 1 implements:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | mode, providers (present), roster, today's spend, free RPD, GPU presence |
| `GET /api/config` | non-secret config (mode, profile, vLLM endpoint) |
| `POST /api/config` | write non-secret config — `{mode?, vllm_base_url?}` |
| `GET /api/cost?window=today\|week\|month` | ledger rollup |
| `GET /api/projects/recent` | recent working directories |
| `POST /api/run` | `{cwd, prompt, mode}` → launches the agent in `cwd`, returns `run_id` |
| `GET /api/events/{run_id}` | **SSE** stream of typed events (the live feed); `run_id="live"` attaches to a running agent |
| `GET /api/keys/status` | per-provider `{present:bool}` **only** (+ backend label) — never the secret |
| `POST /api/keys/{provider}` | store a provider key in the secret store — `{value}` in, status out, value never returned |
| `POST /api/test-connection` | live probe — `{provider}` → `{ok, latency_ms, model}` (key sent as a header, never echoed) |
| `POST /v1/traces` | OTLP/HTTP trace ingest (protobuf **or** JSON) → decoded, fanned out over SSE as `span` events. Loopback-only, tokenless (a collector/agent posts here, not the browser). |
| `GET /healthz` | tokenless readiness probe |

The SSE channel now also carries `span` (the L2 tree, time-filtered to the run) and
`token` (live generation, when the backend streams) — in addition to the Tier-1
discrete events.

The SSE channel is one typed stream (`event:` names): `phase`, `plan`, `plan_item`,
`tool_call`, `escalation`, `gate`, `checkpoint`, `file_op`, `shell`, `cost`,
`narration`, `heartbeat`, `token`. Tier 1 emits the subset the livelog can produce
(`tool_call`/`heartbeat`/`escalation`/`gate`/`narration`/`phase`/`plan`/`cost`); the
rest are typed for Tier 3 so the contract doesn't change. **The design never depends on
token streaming** — when tokens are off (local vLLM), the timeline animates entirely
from the discrete events.

## Security model (localhost hardening)

The server is local, but a malicious local web page (or a DNS-rebinding attack against a
browser pointed at the loopback) is a real threat class. Defenses, all enforced from day
one (see `server/security.py`):

- **Loopback bind only.** Without `--token` the server refuses to bind anything but
  `127.0.0.1`/`localhost`/`::1`.
- **No token on localhost.** The page just opens — friction-free. The hardening that
  protects it is the loopback bind + Origin/Host validation + the CSRF cookie below, not
  a token.
- **Origin/Host validation.** The `Host` header must name a loopback authority; a present
  `Origin` must be a loopback origin. POSTs additionally **require** a loopback `Origin`.
  This blocks DNS-rebinding and the `0.0.0.0`-day class, and is the primary CSRF guard
  (a cross-site page cannot forge a loopback Origin).
- **SameSite=Strict CSRF cookie.** The server sets a per-process `hmx_csrf` cookie
  (`SameSite=Strict`, not HttpOnly) on page load; the frontend echoes it as the
  `X-HMX-CSRF` header on every POST (double-submit). A cross-site page can neither read
  the cookie nor forge the header.
- **Opt-in bearer token (`--token`).** For exposing the UI beyond loopback (e.g. over
  Tailscale), `hm ui --token` mints/accepts a token, embeds it in the printed URL, and
  then requires it (`?token=…` for GETs/EventSource, `X-HMX-Token` for POSTs) on every
  `/api` call. It lives only in the page's JS memory — never in `localStorage`/cookies.
- **Path-traversal-safe static serving.** Requests are normalized and contained to
  `web/dist`; nothing outside it can be read.
## Secret handling (Tier 2)

Provider keys are captured by the wizard and written to a **secret store** chosen at
runtime (`server/secrets_store.py`), best-available first:

| Platform | Backend |
| --- | --- |
| macOS | the `security` CLI (login Keychain) |
| Linux | the `secret-tool` CLI (libsecret / gnome-keyring) |
| any | the `keyring` Python library, if importable |
| fallback | a gitignored **`.env` with `chmod 600`** — only where no keychain exists (headless Linux / WSL2) |

The discipline (enforced in `secrets_store.py` + `config_api.py`, verified by tests):

- The key is **POSTed once** to the local backend and written straight to the store.
- It is **never returned** by any endpoint — `GET /api/keys/status` yields `{present:bool}`
  only; the UI shows masked status (`✓ configured`).
- It is **never held in browser storage** — no `localStorage`/`sessionStorage`/cookies; the
  input is a `type="password"` field whose value lives in component memory and is cleared
  the instant it's saved.
- It is **never logged** — `set_secret` returns status (backend + env-var name) only; the
  connection probe sends the key as a request header and redacts it from any error text.
- The value is read back only to (a) inject keychain-held keys into a launched agent's
  environment and (b) run the live connection probe against the provider — never elsewhere.
- The `.env` fallback is written **atomically at mode 0600** and the file is ensured to be
  gitignored.

Non-secret config (mode, `VLLM_BASE_URL`) is written to `.env` via `POST /api/config` —
it is not a secret, but it must live where `lib.inference` and `hm` already read it.

## Onboarding wizard (Tier 2)

Three steps in the same app (`web/src/components/wizard/`):

1. **Profile** — detect GPU (from `/api/status`) and probe the local vLLM endpoint live,
   then recommend a profile/mode (GPU → local executes, free planner; no-GPU → cloud
   drives). Apply a mode with one click via `POST /api/config`.
2. **Keys** — one row per provider, ordered free → paid → frontier. Paste a key → it's
   POSTed straight to the secret store; a **Test** button runs the live probe inline
   (green/red + latency + model). The row shows masked presence only.
3. **Review** — mode, GPU, ready rungs, and where the secrets are stored — then
   *Continue to launch →*, which drops you on the launcher's "what's about to happen"
   kickoff card.

## Layered disclosure & streaming (Tier 3)

- **L2 span tree.** Spans reach the UI through an **OTLP→SSE bridge**: the
  `POST /v1/traces` receiver decodes an OTLP `ExportTraceServiceRequest` (protobuf or
  JSON, via a ~40-line stdlib wire reader — no `opentelemetry-proto` dependency),
  normalizes each span, and fans it out over SSE. Wire it with the **collector fan-out**
  in `config/otel-collector.yaml`: one OTLP receiver → two exporters (Phoenix *unchanged*
  + this bridge), so Phoenix is unaffected and each gets its own copy. The batch timeout
  is 200ms for live feel (the spec's "fast span processor in UI mode" lever).
- **Token streaming.** `token` events accumulate into one in-place "generating" card, so
  tool calls that arrive between tokens interleave by order. Activates when the backend
  streams (local vLLM with streaming off → the timeline still animates from discrete
  events; nothing depends on token streaming).
- **Deep-research fan-out.** Parallel source cards (breadth) converging into a synthesis
  node, derived from the research/search/synth tool steps.
- **Escalation ladder.** Each escalation shows the cheapest-first ladder
  (LSP → repair → steer → re-plan → frontier) with the destination rung animated, so the
  operator sees *why* cost moved.
- **Diffs.** File-op and span `diff`/`patch` attributes render as coloured +/- diffs.
- **Narration.** A rolling plain-language story at L0 (warnings in amber); failures, cost,
  and stuck-ness are never hidden behind a happy verb — they remain distinct gate /
  escalation / cost cards.
- **Follow / scrub.** "Follow the action" pins the live tail; turning it off lets you
  scroll back through history without losing the stream.

## What's still deferred

- The directory step is a text field + recent-projects dropdown; a **native OS folder
  picker** and drag-drop arrive with the Tauri shell (Tier 4).
- No agent control — read-only by design (mid-run cancel/approve/steer is the documented
  WebSocket upgrade if/when control is added).
