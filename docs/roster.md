# Roster — keeping model IDs current

The free/cheap LLM landscape is an open bazaar that rotates monthly: model ids get
renamed, `:free` variants come and go, providers deprecate slots. hermes-max is
built so this is **always a one-line config edit, never a code change.**

## How it stays honest

`lib/inference/roster.py` validates every configured model id at two moments:

- **`hm up`** — warn-only, so a stale id never blocks a start;
- **`hm health`** — the full `ROSTER` section, one line per slot.

For each `provider.slot` it:

1. checks `KNOWN_DEPRECATED` (a dict populated as models retire);
2. probes the provider's `/models` where available (cached 1h, never slows a task):
   OpenAI-compatible → `GET {base}/models`; `local_vllm` → reuse the discovery
   result; `anthropic` / `cerebras` have no `/models` endpoint → reported
   `unconfirmed` (relying on `KNOWN_DEPRECATED` + a first-call 404).

`hm health` prints, for example:

```
  ✓ openrouter.synth_free   moonshotai/kimi-k2.6:free   confirmed
  ✗ groq.synth_oss          openai/gpt-oss-120b         missing
      → NOT in provider /models — update id in inference.yaml
```

A deprecated or missing slot is a **warning, not an error** — the system starts
anyway and the chain simply uses the next present rung.

## Updating a slot

Every model slot in [`config/inference.example.yaml`](../config/inference.example.yaml)
(or your `~/.hermes-max/inference.yaml`) carries a `# verified: YYYY-MM-DD` comment.
When `hm health` flags one:

1. find the replacement id on the provider's console / `/models`;
2. change the id in the yaml (one line) and update the `# verified:` date;
3. `hm health` re-confirms.

**No code change. Ever.** This is the maintainability promise: provider knowledge
lives in one place (the yaml), behind one seam (`lib/inference`), with an honest
validator. See [architecture.md](architecture.md) §§16–17 and
[providers.md](providers.md).
