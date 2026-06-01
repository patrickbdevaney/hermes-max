# Modes — the cost/quality posture toggle

A **mode** is one word that reassigns the coding role chains (who plans, who
executes, who researches) *and* sets a hard spend ceiling. Switch live with
`hm mode <name>` — no restart. `hm mode --list` prints the table; the active mode
and today's spend show in `hm status` and the `hm dev` cockpit.

The two [profiles](profiles.md) map onto the two headline modes (`free`, `full`).
The other four are real, supported variations — here for when you want them.

```
MODE            COST/MO   GPU?  POSTURE
free            $0.00     yes   Kimi-K2.6-free plans, local executes. DEFAULT. Best with Thor/Spark.
full-local      ~$1.50    yes   V4-Pro plans, local executes. V4-Pro judgment over Kimi-free.
full            ~$17      no    V4-Pro plans, V4-Flash executes. No GPU. ~10% of Code Max.
frontier-local  ~$45      yes   Opus plans, local executes. Sovereign + true frontier planning.
frontier        ~$60      no    Opus plans, V4-Flash executes. Closest to Claude Code. Hard sessions.
local           $0.00     yes   Pure local, no API. Air-gapped floor.
```

## Honest framing

- **`free` and `full-local` are the headline value** — near-frontier planning
  (Kimi-free or V4-Pro) plus free/sovereign local execution, $0–1.50/month. This is
  the whole point of owning the GPU.
- **`full`** is the no-GPU on-ramp — anyone can run it, ~$17/mo, ~10% of Claude
  Code Max.
- **`frontier` / `frontier-local`** are real but their value *narrows* versus a
  Claude Code subscription. They exist for unlimited-usage, no-rate-limit, and
  private-execution reasons — not pure cost. Use `frontier` for a genuinely hard
  session, then drop back to `full-local`.
- **`local`** is the air-gapped floor: no API at all, limited to local model
  quality.

## The role chains

Each mode declares an ordered `provider.model` chain per role; a rung whose API
key is absent is skipped silently. The roles:

| Role | What it does |
|---|---|
| `code_plan` | write the PLAN.md (the expensive, high-leverage step) |
| `code_execute` | the Hermes loop itself — implement the plan, every turn |
| `code_steer` | frequent cheap nudges / small corrections |
| `code_repair` | targeted fix on a verify failure |
| `research_fanout` | many small parallel calls (query-expand, filter, extract) |
| `research_synth` | one large synthesis call over the evidence set |
| `code_frontier` | the rare Opus escalation rung (empty unless a frontier mode) |

A role a mode doesn't list falls back to the base chain in
[`config/roles.yaml`](../config/roles.yaml). The full mode definitions live in
[`config/modes.yaml`](../config/modes.yaml) — edit that file (or your
`~/.hermes-max/modes.yaml` copy), never the code.

## Safety & fall-through

- A `requires_gpu` mode (e.g. `free`) **warns** rather than silently paying when no
  local vLLM is reachable — you must explicitly choose to pay (`hm mode full`).
- Missing a key drops only that rung: `free` without `OPENROUTER_API_KEY` falls
  through to local planning automatically — no error, no cost.
- The spend ceiling (`local < free < full < frontier`) is enforced by the router,
  so a mode is both a chain swap **and** a cost cap.

## The agent-loop backend swap

A mode also decides which model the **Hermes loop itself** runs on (distinct from
the conductor's per-role routing). `hm mode <name>` runs `scripts/set_mode.sh`,
which atomically rewrites the `model:` block of `~/.hermes/config.yaml`:

- local-executor modes (`free` / `full-local` / `frontier-local` / `local`) →
  local vLLM (`$VLLM_BASE_URL`, no key);
- remote-executor modes (`full` / `frontier`) → DeepSeek V4-Flash via the funded
  DeepInfra endpoint.

It backs up to `config.yaml.bak` and captures the original `model:` block once, so
the swap is fully reversible (`hm mode free` rewrites the loop back to local).
Skip the live swap with `HM_NO_HERMES_SWAP=1`.

Deeper detail: [architecture.md](architecture.md) §11.
