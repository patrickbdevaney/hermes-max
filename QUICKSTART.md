# QUICKSTART — clone to a built project in 5 minutes

## Pick your mode

| You have… | Use | What it does | Cost |
|-----------|-----|--------------|------|
| **a local GPU** | `hm mode free-full-local` | free OpenRouter planner cascade ($0) → DeepInfra V4-Pro fallback if all free rungs 429 → local model executes | **$0–3/mo** (recommended) |
| a local GPU, want reliability | `hm mode full-local` | V4-Pro plans (paid, tried first) → local model executes | ~$1–3/mo |
| **no GPU** | `hm mode full` | V4-Pro plans + V4-Flash executes — both API | ~$17/mo |
| **want $0 always** | `hm mode free` | free cascade plans → local executes (no paid fallback; quality varies) | $0 |

`hm` with no arguments prints this table. Add `--free-uplift` to any GPU mode for an
on-demand reasoning escalation (a larger free model reviews each verified file).

Two profiles, side by side. Pick the one that matches your hardware.

```bash
# 1. Prerequisite: install the Hermes agent
#    → github.com/nousresearch/hermes-agent  (the harness wraps it; not bundled)

# 2. Clone hermes-max and enter it
git clone https://github.com/patrickbdevaney/hermes-max && cd hermes-max

# 3. Bootstrap once (idempotent — builds venvs, installs deps, copies the config
#    trinity into ~/.hermes-max/, registers the MCP servers with Hermes)
./install.sh

# 4. Copy the env template and add the keys you have
cp .env.example .env
#    Then edit .env — you need EITHER a local endpoint OR a DeepSeek/DeepInfra key.
```

### ── PROFILE A: you own a GPU (DGX / Thor / RTX / Mac Studio) ──

```bash
# In .env, set:
#   VLLM_BASE_URL=http://<your-endpoint>:8001/v1     # your local model serves the executor
#   OPENROUTER_API_KEY=...                           # Kimi K2.6 free conductor (the planner)
#   GROQ_API_KEY=...   CEREBRAS_API_KEY=...          # free research fan-out (optional)

hm up --free
#   (optional — if you deposited $10 on OpenRouter for 1000 free requests/day:)
hm up --free --free-uplift
```

### ── PROFILE B: no GPU (laptop / mini-pc / vps) ──

```bash
# In .env, set:
#   DEEPINFRA_API_KEY=...    (or DEEPSEEK_API_KEY=...)   # V4-Flash drives, V4-Pro plans
#   GROQ_API_KEY=...   CEREBRAS_API_KEY=...   OPENROUTER_API_KEY=...   # free accelerators

hm up --full
```

### ── 5. Launch the agent and build something ──

```bash
hermes
#   > then type your prompt, e.g.:
#   > "Build a tested Python rate limiter with token-bucket and sliding-window strategies."
```

That's the whole quickstart. Everything else is optional depth.

---

## The five commands you'll actually use

| Command | What it does |
|---|---|
| `hm up [--free\|--full]` | start the stack in a profile/mode |
| `hm down` | tear everything down |
| `hm status` | what's running + the active mode + today's spend |
| `hm dev` | the cockpit: agent + live activity + status, one window |
| `hm cost` | spend breakdown ($0.000000 precision, free-vs-paid split) |

Switch profile/mode live without restarting: `hm mode <name>` (or `hm mode --list`).
Check endpoints, providers, and the model roster: `hm health`. The full command
surface is `hm help --all`.

---

## If something doesn't start

- **No GPU but you ran `--free`?** `hm up` detects the missing local endpoint,
  warns, and falls back gracefully — it never hard-fails on absent hardware. Use
  `hm up --full` (Profile B) instead, or point `VLLM_BASE_URL` at a cloud endpoint.
- **A provider key is missing?** That rung silently drops; the next present
  provider in the chain is used. Zero keys = pure local. This is by design.
- **More:** [docs/troubleshooting.md](docs/troubleshooting.md) ·
  [docs/deployment.md](docs/deployment.md) (which profile fits your box).
