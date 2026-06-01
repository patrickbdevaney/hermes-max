# CLAUDE_repo_elegance.md — Repo, Docs & Ergonomics Refactor

## Source of truth (read this first)

The CURRENT repo on disk is the only source of truth for what exists — the hermes-max harness:
the Hermes agent wrapped by 14 MCP servers, ~33 skills, the presence-gated conductor, the `hm`
CLI, and the inference fabric (lib/inference, modes.yaml, roles.yaml) from the build specs in
archive/specs/. Before rewriting any doc, inventory the actual repo (servers, skills, scripts,
configs, the real `hm` verbs) and describe THAT.

Do NOT treat any older "repo dump" or a predecessor README (e.g. an "Autopoietic Compounding
Intelligence Engine" built on LangGraph/LiteLLM/Postgres/Neo4j/Qdrant/Telegram/namespaces) as
current — that is a historical ancestor of this project, not the system being documented. If such
an artifact is present, ignore its CONTENT. Its only legitimate use is as a TONE/STRUCTURE
reference: that older README's shape (one-line summary, architecture diagram, numbered quickstart,
prerequisites block, services table, migration section) is a good template — reuse the shape,
describe the real current system.

## The mandate

hermes-max is feature-rich and powerful but currently presents to a new user like an airplane
cockpit: a wall of MCP servers, telemetry sources, flags, and provider options with no on-ramp.
This directive does NOT change features. It reorganizes the repo, rewrites the documentation, and
simplifies the operator surface so that the system is as easy to start and understand as it is
powerful — Apple-style progressive disclosure: the simple thing is immediate, the powerful thing
is discoverable but never in your face.

Two hard rules for this entire refactor:
1. **Subtract nothing functional.** Every feature, MCP server, skill, and telemetry source stays.
   This is reorganization and documentation, not deletion of capability.
2. **Progressive disclosure.** A first-time user sees a quickstart and two profiles. A power user
   opens folders for the deep detail. Nobody is forced through the complexity to get running.

When done: a Mac/Linux/WSL2 user with Hermes installed can clone, read one screen, bring a few
keys, set a mode, and build a project — without ever opening the advanced docs.

=================================================================================================
## PART 1 — THE TWO PROFILES (the spine of everything)
=================================================================================================

The system has many granular modes, but a user thinks in TWO profiles. Make these the headline
everywhere — README, quickstart, CLI help. The six modes become the advanced layer underneath.

**Profile A — Bring-Your-Own-GPU (local driver + free conductor)**
For owners of a DGX Spark, Jetson Thor, RTX 6000/5090/4090, or Mac Studio. The local model drives
(big context, many turns, free minus electricity); a free OpenRouter model (Kimi K2.6) plans.
Near-zero marginal cost. Maps to `--free` (and `--free --free-uplift` if the $10 OpenRouter
deposit is made).

**Profile B — No-GPU (economic API driver + API conductor)**
For laptops, mini PCs, Mac minis, VPSes — anyone without a capable GPU. DeepSeek V4-Flash drives,
V4-Pro plans. ~$17/month, no rate limits, ~10% of a Claude Code Max subscription. Maps to `--full`.

Everything else (full-local, frontier, frontier-local, local) is an advanced variation documented
in `docs/modes.md`, not in the README quickstart.

=================================================================================================
## PART 2 — REPO DIRECTORY STRUCTURE (how the best shops organize)
=================================================================================================

Restructure to mirror how Anthropic, Google, OpenAI, and top-tier infra projects lay out a repo:
a clean root, a single entry document, source under clear namespaces, and docs in a navigable
tree. Target structure:

```
hermes-max/
  README.md                  ← the one document a new user reads (Part 3)
  QUICKSTART.md              ← 5-minute path to a running build (Part 4)
  LICENSE
  .env.example               ← three labeled tiers, minimum-viable = 2 lines
  install.sh                 ← optional one-line bootstrap (Part 6); keep simple
  hm                         ← the single CLI entrypoint (Part 7)

  docs/
    architecture.md          ← the mental model + config trinity (was ARCHITECTURE.md)
    profiles.md              ← Profile A vs B, deep version
    modes.md                 ← all six modes, role chains, the advanced layer
    providers.md             ← the honest backend table (cost/ctx/limits/throughput)
    hardware.md              ← local model hardware tiers + decode-speed estimates (Part 5)
    deployment.md            ← mini-pc / laptop / desktop / DGX deploy profiles (Part 8)
    research-engine.md       ← deep research fan-out design + honest tradeoffs
    cost.md                  ← how the ledger and hm cost work
    roster.md                ← keeping model IDs current when providers deprecate
    mcp-servers.md           ← the 14 servers, one section each, reference not tutorial
    skills.md                ← the skill catalogue, reference
    troubleshooting.md       ← common failures, the degrade-gracefully behaviors

  src/                       ← (or keep existing layout) all implementation
    lib/inference/           ← the single provider seam
    mcp-*/                   ← the servers
    skills/                  ← the markdown skills
    plugins/                 ← optional capabilities (free_uplift, etc.)

  config/
    inference.example.yaml   ← the provider registry (Part 9 of the fabric spec)
    modes.example.yaml       ← the six modes
    conductor.example.yaml   ← conductor policy + plugin toggles

  scripts/                   ← the *.sh that hm dispatches to
  docker/                    ← compose files + per-service Dockerfiles

  archive/
    specs/                   ← all the CLAUDE_*.md build specs, kept for reference
      CLAUDE_inference_fabric.md
      CLAUDE_max_orchestration.md
      CLAUDE_plan_execute.md
      CLAUDE_innovations.md
      CLAUDE_query_rationing.md
      ... (every prior spec)
      README.md              ← "these are the build specs that produced this system,
                                archived for provenance; not needed to use hermes-max"
```

Actions:
- Move every stray top-level markdown into the right `docs/` file. The root should contain only
  README, QUICKSTART, LICENSE, .env.example, install.sh, hm, and the standard dirs.
- Move all `CLAUDE_*.md` build specs into `archive/specs/` with an index README explaining what
  they are and that a user does not need them.
- If the current repo has scattered design notes, fold them into the appropriate `docs/` file;
  do not leave loose `.md` files in the root or in random subdirs.
- Grep for broken doc links after moving and fix them.

=================================================================================================
## PART 3 — THE README (Apple-style: rich but not dense)
=================================================================================================

Rewrite README.md as the single document a new user reads. Structure, in order:

1. **One-line what-it-is.** "A local-first agentic coding harness that wraps the Hermes agent with
   verification, memory, research, and a cost-aware multi-provider brain — Claude-Code-class
   engineering at a fraction of the cost." One sentence. No jargon wall.

2. **A 4-6 line what-it-does paragraph.** The plan/execute split, the verify gate, the compounding
   memory, the multi-provider fabric. Plain language. What the user GETS, not how it works
   internally.

3. **Quickstart, front and center** (the actual commands — see Part 4). This must appear within
   the first screen of scrolling. A user should see "here's how I run it" almost immediately.

4. **The two profiles** (Part 1), each as a short card: who it's for, what it costs, the one
   command. Profile A (own a GPU) and Profile B (no GPU). A reader self-selects in 10 seconds.

5. **A single architecture diagram or 5-line mental model.** "MCP servers request roles; the
   fabric picks a provider from your config; missing keys drop silently; zero keys runs pure
   local." One glance, the whole concept.

6. **What you need** (prerequisites): Hermes installed, Python, Docker (optional), and "bring any
   subset of these keys" — a short table of the providers with a one-word purpose each. Honest
   that you need at least ONE driver path (a local endpoint OR a DeepSeek key) and that
   OpenRouter+Groq+Cerebras are the free-tier accelerators.

7. **Links into docs/** for everything deep: "Full provider table → docs/providers.md", "Hardware
   tiers → docs/hardware.md", "All modes → docs/modes.md", etc. The README POINTS to depth; it
   does not CONTAIN all of it.

Tone rules (Apple design ethos applied to technical writing):
- Confident, clear, declarative. Short sentences. White space.
- Technically rich but not turgid — depth lives in docs/, the README is the lobby.
- **Not opinionated in poor taste about providers.** Do NOT write "Groq is no longer good" or
  disparage any provider. State what each is GOOD FOR. Groq's role is high-RPM research fan-out —
  present it as the fan-out workhorse, which is accurate and useful, not as a downgrade.
- Honest about tradeoffs without editorializing. "Local driving is free but slower; API driving
  costs pennies but is faster" — state the tradeoff, let the user choose.
- No marketing hyperbole, no benchmark-trophy-case. Sober, precise, respectable — the register of
  a top-tier infrastructure project's README.

=================================================================================================
## PART 4 — QUICKSTART (5 minutes to a running build)
=================================================================================================

QUICKSTART.md (and the quickstart section of the README) must get a user from clone to a built
project with the absolute minimum steps. Both profiles, side by side:

```
# 1. Prerequisite: install the Hermes agent (link to nousresearch/hermes-agent)
# 2. Clone hermes-max and enter it
git clone https://github.com/patrickbdevaney/hermes-max && cd hermes-max

# 3. Copy the env template and add the keys you have
cp .env.example .env
#    Then edit .env — you need EITHER a local endpoint OR a DeepSeek key:

# ── PROFILE A: you own a GPU (DGX/Thor/RTX/Mac Studio) ──
#    Set in .env:   VLLM_BASE_URL=http://<your-endpoint>:8001/v1
#                   OPENROUTER_API_KEY=...   (Kimi K2.6 free conductor)
#                   GROQ_API_KEY=...  CEREBRAS_API_KEY=...   (free research fan-out)
hm up --free
#    (optional, if you deposited $10 on OpenRouter for 1000 free req/day:)
hm up --free --free-uplift

# ── PROFILE B: no GPU (laptop/mini-pc/vps) ──
#    Set in .env:   DEEPINFRA_API_KEY=...   (or DEEPSEEK_API_KEY=...)
#                   GROQ_API_KEY=...  CEREBRAS_API_KEY=...   OPENROUTER_API_KEY=...
hm up --full

# 4. Launch the agent and build something
hermes
#    > then type your prompt, e.g.:
#    > "Build a tested Python rate limiter with token-bucket and sliding-window strategies."
```

That is the whole quickstart. Five commands. Everything else is optional depth.

The quickstart must also state, in one line each:
- `hm down` tears everything down.
- `hm status` / `hm health` shows what's running and what's reachable.
- `hm mode <name>` switches profile/mode live.
- `hm cost` shows what you've spent ($0.000000 precision, free-vs-paid split).

=================================================================================================
## PART 5 — HARDWARE TIERS & DECODE-SPEED ESTIMATES (docs/hardware.md)
=================================================================================================

The existing README hardware tiers are good and should move to docs/hardware.md, expanded with
honest decode-speed estimates. Research and present realistic single-stream tok/s figures.
Anchor to known measured points and extrapolate by memory bandwidth (decode is bandwidth-bound:
tok/s ≈ memory_bandwidth_GBps / active_param_bytes_per_token).

Recommended minimum: **24-32B class is the floor for an effective local executor.** Below that,
quality degrades enough that cloud inference (Profile B) is the better choice. State this clearly
and without shame — a 14B local model leaning on cloud uplift is a valid, honest configuration.

Present a table like this (fill with researched/estimated values, label estimates as estimates):

| Hardware tier (examples) | Approx VRAM | Suggested local driver | Est. single-stream decode |
|---|---|---|---|
| DGX Spark / Jetson Thor / RTX 6000 Pro | 96-128GB+ unified/VRAM | Large MoE (Qwen3.6 ~122B-A10B, **Nemotron 3 Super 120B-A10B** for the 96-128GB tier) | ~12-25 tok/s (MoE, bandwidth-bound) |
| RTX 5090 / 4090 | 24-32GB | Mid driver (Qwen3.6 ~35B-A3B, Nemotron, Gemma-4 ~27-31B) | ~40-60 tok/s (A3B); ~15-30 tok/s (dense 27-31B) |
| RTX 3090 / 4080 | 16-24GB | Qwen3.6 ~35B-A3B quantized, or ~14-32B dense | ~30-50 tok/s (A3B q); ~10-25 tok/s (dense) |
| M4 Max/Ultra Studio (MLX/GGUF) | 36-128GB unified | Qwen3.6 35B-A3B / larger MoE via MLX or llama.cpp | ~20-50 tok/s (MLX, varies by tier) |
| RTX 4060 Ti / 3060 / gaming laptop | 8-16GB | Smaller GGUF (~14B class) + lean on free/full cloud | ~15-35 tok/s (14B q); recommend cloud uplift |
| Jetson Orin / small edge | 8-32GB | Small driver + heavier cloud uplift | ~5-20 tok/s; recommend Profile B for serious work |
| No GPU / VPS | — | Cloud-only driver (Profile B, V4-Flash via conductor) | n/a — API speed |

Every estimate must be labeled as an estimate with its basis (measured anchor or bandwidth
extrapolation). Where a real measured number exists (e.g. Qwen3.6-35B-A3B-NVFP4 on Thor ~50 tok/s
with MTP), cite it as measured. Be explicit that decode speed is bandwidth-bound and that long
context inflates time-to-first-token substantially on edge hardware.

Worked example for the methodology (show this so users can estimate their own hardware):
"A 35B-A3B model activates ~3B params per token at ~2 bytes/param (NVFP4-ish) ≈ 6GB read per
token. On a 273 GB/s device that is a ~46 tok/s ceiling; measured ~50 tok/s with MTP speculative
decode confirms the estimate. Scale by your device's memory bandwidth."

=================================================================================================
## PART 6 — INSTALL.SH (optional one-line bootstrap; keep it honest)
=================================================================================================

Provide an `install.sh` that does the safe, simple parts of setup and clearly hands off the parts
that require human judgment. Do NOT over-engineer this — if a step is too environment-specific to
automate safely, print clear instructions instead of guessing.

install.sh should:
- Check prerequisites (python version, docker presence, hermes presence) and report what's missing.
- `cp .env.example .env` if no .env exists, then print the "edit these keys" guidance.
- Create the `~/.hermes-max/` config dir and copy the example yamls there if absent.
- Make `hm` executable and suggest adding it to PATH.
- NOT auto-install Hermes (link to it), NOT guess the user's GPU/endpoint, NOT write keys.
- End by printing the two-profile quickstart so the next step is obvious.

A curl-pipe-bash one-liner can be offered in the README but only if it's genuinely safe and
self-contained; if there's any doubt, ship `install.sh` to be run after clone and document that.

=================================================================================================
## PART 7 — THE hm CLI (one entrypoint, discoverable, not a cockpit)
=================================================================================================

`hm` is the single operator surface. It must be self-documenting and organized so a user never
needs to memorize or keep a cheatsheet of dozens of verbs. Apply progressive disclosure to the
CLI itself.

`hm` with no args, or `hm help`, prints a SHORT grouped help — the common verbs only:

```
hermes-max — agentic coding harness

  Getting started
    hm up [--free|--full]     start the stack in a profile/mode
    hm down                   stop everything
    hm status                 what's running + active mode + today's spend

  Using it
    hm run "task"             run the agent on a task (or just launch `hermes`)
    hm dev                    the cockpit: agent + live activity + status, one window
    hm cost                   spend breakdown ($0.000000, free-vs-paid)

  Health & modes
    hm health                 check endpoints, providers, roster
    hm mode [name|--list]     show or switch profile/mode

  More:  hm help --all        every command, including advanced/diagnostic verbs
```

`hm help --all` reveals the full surface (restart, preflight, watch, observe, smoke, regression,
eval, bottleneck, attach, roster, plugins, etc.). The point: common path is 8 verbs, the airplane
cockpit is one flag away when you actually need it.

Each verb supports `hm <verb> --help` with a one-paragraph explanation.

`hm up` ergonomics:
- `--free` and `--full` are the two headline flags (the two profiles).
- The other modes (`--full-local`, `--frontier`, `--frontier-local`, `--local`) work but are
  documented under `hm help --all` and docs/modes.md, not in the short help.
- On start, print a clean one-screen summary: active mode, which providers are present, which
  roles are satisfiable, the Hermes config backend that was set, and any roster warnings. One
  screen, the user knows the whole state.

=================================================================================================
## PART 8 — DEPLOYMENT PROFILES (docs/deployment.md) — respect every environment
=================================================================================================

Document and IMPLEMENT graceful behavior across deployment environments. The stack must not assume
a beefy machine. Specifically:

- **Mini PC / Mac mini / VPS (cloud-everything):** no local model, no GPU embedding. The embedding
  and rerank services (Qwen3-Embed/Reranker) must be OPTIONAL — if not started, mcp-codebase-rag
  falls back to BM25 lexical retrieval (it already supports this; make the fallback automatic and
  documented). Docker profiles must allow starting WITHOUT the embedding containers.
- **Laptop (cloud driver + local embedding):** can run the small embedding model locally but drives
  via API. A docker profile for "embedding only, no driver."
- **Desktop / DGX / Jetson (full local):** local driver + local embedding + full services, or
  selectively cloud. The full profile.

Implement this via docker compose profiles (e.g. `--profile lean` skips embedding/rerank, `--profile
full` includes them) wired to `hm up`:
- `hm up --free` on a no-GPU box should detect the absence of a local endpoint and either warn
  clearly or auto-select the lean profile + Profile B suggestion.
- `hm up` should never fail hard because a GPU service isn't available — it degrades to BM25 +
  cloud and tells the user what it did.

docs/deployment.md presents a short matrix: environment → what runs locally → what runs in cloud →
which docker profile → which hm mode. A user finds their row and knows exactly what to do.

=================================================================================================
## PART 9 — THE COCKPIT (hm dev): one window, not twelve
=================================================================================================

The current monitoring story requires too many terminal windows — live tool calls, status, spend,
trajectories, health, all separate. Consolidate into a single tmux cockpit that gives the whole
operational picture in one window.

`hm dev` opens a tmux session with a sensible default layout (do not require the user to arrange
panes):
- **Main pane:** the Hermes agent (where you type prompts and watch it work).
- **Side pane (top):** live activity stream — tool calls, role resolutions, rung fall-throughs
  (the `hm watch` content), condensed to the essential events, not raw firehose.
- **Side pane (bottom):** a compact live status line — active mode, today's spend ($0.000000),
  remaining free RPD per key model, current task phase (plan/execute/verify), and any health
  warning. This is the "one glance and I know what's happening" pane.

Design rules:
- One window shows everything an operator needs during a normal session. No second terminal
  required for routine use.
- The deep diagnostic views (full OTel waterfall via `hm observe`, raw trajectories, per-server
  logs) remain available as separate commands for when something breaks — but they are NOT part of
  the default cockpit. Progressive disclosure again: normal use is one window; debugging opens more.
- The activity stream must be readable — human-paced, essential events, color-coded by severity —
  not an unfiltered log dump. If a turn fires ten internal tool calls, summarize them as a line,
  not ten lines.
- `hm dev` must tear down cleanly (the tmux session ends on `hm down` or on detach+stop).

Document the cockpit in QUICKSTART ("`hm dev` gives you everything in one window") and in
docs/architecture.md (the deeper telemetry story).

=================================================================================================
## PART 10 — TELEMETRY CONSOLIDATION (don't make the user chase signals)
=================================================================================================

The system has many telemetry sources (OTel/Phoenix spans, livelog, ledger, trajectories, health,
bucket status). A user should not need to know all of them exist. Consolidate the SURFACE without
removing the sources:
- `hm status` is the single "is everything OK and what's it costing" answer (rolls up health +
  mode + spend + roster warnings + free-RPD-remaining).
- `hm dev` is the single live operational view (Part 9).
- `hm cost` is the single spend view.
- `hm observe` / `hm watch` / raw Phoenix remain for deep debugging, documented in
  docs/troubleshooting.md, not surfaced in the common path.

The principle: three commands cover 95% of operational needs (`status`, `dev`, `cost`). Everything
else is for when you're specifically debugging, and lives in the troubleshooting doc.

=================================================================================================
## DEFINITION OF DONE
=================================================================================================
- Root contains only: README.md, QUICKSTART.md, LICENSE, .env.example, install.sh, hm, and the
  standard dirs (docs/, src or existing src layout, config/, scripts/, docker/, archive/).
- All CLAUDE_*.md build specs moved to archive/specs/ with an index README.
- All stray markdown folded into the docs/ tree; no loose design notes in root or random subdirs;
  all internal doc links fixed.
- README follows Part 3: one-line what-it-is, short what-it-does, quickstart within the first
  screen, the two profiles as cards, a 5-line mental model, prerequisites, and links into docs/.
  No provider disparagement; honest tradeoffs; Apple-style clarity.
- QUICKSTART gets a user from clone to a built project in the documented five commands for both
  profiles.
- docs/hardware.md has the hardware tier table with labeled decode-speed estimates, the 24-32B
  minimum recommendation, Nemotron 3 Super 120B-A10B for the 96-128GB tier, and the worked
  bandwidth-extrapolation example.
- install.sh does the safe setup steps, hands off judgment steps with clear instructions, and ends
  by printing the two-profile quickstart.
- `hm help` shows the short grouped common-verb help; `hm help --all` reveals the full surface;
  every verb has `--help`. `--free` and `--full` are the headline flags.
- docs/deployment.md has the environment matrix; docker compose profiles allow lean (no embedding,
  BM25 fallback) vs full; `hm up` degrades gracefully on a no-GPU box and reports what it did.
- `hm dev` opens a single-window cockpit (agent + condensed activity + compact status) with a
  sane default layout; deep diagnostics remain separate commands.
- Telemetry surface consolidated: status, dev, cost cover the common path; observe/watch/Phoenix
  remain for debugging in the troubleshooting doc.
- Nothing functional removed. Every MCP server, skill, plugin, and telemetry source still works.
  This was reorganization and documentation only.

## Anti-Frankenstein / taste check (final)
- The repo root reads like a professional infra project (Anthropic/Google/OpenAI register), not a
  research scratchpad.
- A newcomer understands what the system is and how to start it from the first screen of the README.
- The two profiles are the spine; the six modes are discoverable depth.
- No provider is disparaged; each is described by what it's good for.
- The cockpit is one window for normal use; complexity is one flag/command away when needed.
- Power-user richness is fully preserved — just filed, not flattened.