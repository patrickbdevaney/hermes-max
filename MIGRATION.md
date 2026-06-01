# MIGRATION.md — provider-call inventory & the single-seam migration

> **Architectural rule:** MCP servers request *roles*; the inference fabric
> (`lib/inference/`) chooses providers. **Providers are config, not code.**

This document inventories every place that talks to a model backend today, and
tracks the consolidation behind the one seam: `lib/inference/`.

## The good news (starting point)

The repo was already most of the way de-Frankensteined before this spec:

- **No provider SDKs anywhere.** Grep for `import openai`, `import anthropic`,
  `import groq` across all MCP servers returns nothing. Every backend is reached
  over plain `httpx` against an OpenAI-compatible (or Anthropic) HTTP endpoint.
- **The non-escalation MCP servers make ZERO direct LLM calls.** `mcp-research`,
  `mcp-search`, etc. either hit the local vLLM endpoint (`$VLLM_BASE_URL`) or
  route hard-subtask help through the **conductor MCP** (`mcp-escalation`, port
  9105). They never name a cloud provider.

So the migration is not "rip out scattered SDK calls" — it is "move the one
provider registry + the two HTTP seams into `lib/inference/`, and express the
backend landscape as YAML."

## Where provider knowledge lived (before)

| Location | What it held | New home |
|---|---|---|
| `mcp-escalation/conductor_registry.py` (`PROVIDERS` dict, lines ~31–131) | hardcoded base URLs, model ids, env-key names, rpm/tpm/rpd limits, prices for deepinfra / fireworks / together / deepseek / moonshot / cerebras / groq / gemini / anthropic | **`inference.yaml`** (`providers:` blocks) loaded by `lib/inference/config.py` |
| `conductor_registry.py` `DEFAULT_ROLE_CHAINS`, `DEFAULT_DRAFT_POOL` | per-role provider order (synth/steer/escalate), draft pool | **`roles.yaml`** + **`modes.yaml`** loaded by `lib/inference/roles.py` |
| `conductor_core.py` `_post_chat()` (lines ~282–293) | the httpx OpenAI-compatible call seam | **`lib/inference/adapters.py`** `_openai()` |
| `frontier_core.py` Anthropic call path | the Opus call | **`lib/inference/adapters.py`** `_anthropic()` |
| `conductor_core.py` ledger (`~/.hermes-max/conductor/ledger.json`) | per-day/month spend by provider+role | **`lib/inference/ledger.py`** (`ledger.jsonl`, `$0.000000`, free-vs-paid split) |
| `conductor_core.py` budget (`budget.json`, `_budget_check`, `_update_budget_from_headers`) | per-(provider,model) rpm/tpm/rpd with header correction | **`lib/inference/buckets.py`** (`has_headroom`, `update_from_headers`, `note_429`) |
| `conductor_core.py` `run_role()` | walk a chain, first present rung, fall on error | **`lib/inference/router.py`** `run_role()` (presence + ceiling + bucket gated) |

## The seam (after)

```
MCP server  ──asks for a ROLE──▶  lib/inference.run_role(role, messages)
                                      │
                    config.py ◀───────┤  inference.yaml  (what backends exist)
                    roles.py  ◀───────┤  roles.yaml + modes.yaml (which backend per job)
                    buckets.py        │  (429 pre-check)
                    adapters.py ──────┘  the ONLY wire calls (httpx)
                    ledger.py            ($0.000000, every call)
```

Nothing outside `lib/inference/adapters.py` constructs a provider HTTP request.
A new provider of an existing `kind` is a YAML edit; no Python changes.

## Migration status

- [x] **Stage 0** — `lib/inference/` created; this inventory written; the
      no-direct-SDK rule documented (here + ARCHITECTURE.md).
- [x] **Stage 1** — `inference.example.yaml` schema + `config.py` loader;
      missing-key blocks skip silently; zero-key → local-only (proven by
      `lib/inference/smoke_inference.py`).
- [x] **Stage 2/4** — `roles.yaml` + `router.run_role()` + `ledger.py` +
      `buckets.py`; offline smoke green (23/23).
- [ ] **Stage 5** — point `mcp-escalation` (conductor), `mcp-research`,
      `mcp-search` at `lib/inference` by ROLE; delete the now-dead direct-call
      paths above so the repo shrinks; `hm preflight` reports provider/role
      coverage. *The conductor remains the policy/gating brain (Opus triple-gate,
      per-subtask budget); only its routing + registry move into the fabric.*

### Dead code to delete once Stage 5 lands (do not delete until callers move)
- `conductor_registry.py`: the `PROVIDERS` literal (superseded by inference.yaml).
- `conductor_core.py`: `_post_chat`, the budget+ledger blocks (superseded by
  adapters/buckets/ledger).
- `frontier_core.py`: the inline Anthropic HTTP call (superseded by the
  `anthropic` adapter; the three-gate POLICY stays).

The conductor's decision logic (`conductor_policy.py`, the frontier triple-gate,
`directive_verify.py`, `brief_assemble.py`, `plan_split.py`) is NOT provider
code and stays where it is — it becomes a *caller* of `lib/inference`.
