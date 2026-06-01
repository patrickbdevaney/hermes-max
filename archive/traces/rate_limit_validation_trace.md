# Stage-3 rate-limit validation trace (REAL free-tier, $0)

- **mode**: `free` (CONDUCTOR_MODE=free → only free providers may fire)
- **draft pool (resolved, free mode)**: ['cerebras:zai-glm-4.7', 'cerebras:gpt-oss-120b', 'groq:openai/gpt-oss-120b', 'groq:qwen/qwen3-32b', 'groq:meta-llama/llama-4-scout-17b-16e-instruct']
- **max_tokens/round**: 4096 · **rounds**: 3
- **verdict**: ✅ PASS

## Per-round fan-out (best-of-N slop-draft)

### Round 1
- real candidates: ['groq:meta-llama/llama-4-scout-17b-16e-instruct', 'cerebras:gpt-oss-120b', 'groq:openai/gpt-oss-120b', 'cerebras:zai-glm-4.7', 'groq:qwen/qwen3-32b']
- **pre-flight SKIPPED (tracker prevented the call)**: —

### Round 2
- real candidates: ['cerebras:gpt-oss-120b', 'cerebras:zai-glm-4.7']
- **pre-flight SKIPPED (tracker prevented the call)**: ['groq:openai/gpt-oss-120b (tpm_exhausted)', 'groq:qwen/qwen3-32b (tpm_exhausted)', 'groq:meta-llama/llama-4-scout-17b-16e-instruct (tpm_exhausted)']

### Round 3
- real candidates: ['cerebras:gpt-oss-120b', 'cerebras:zai-glm-4.7']
- **pre-flight SKIPPED (tracker prevented the call)**: ['groq:openai/gpt-oss-120b (tpm_exhausted)', 'groq:qwen/qwen3-32b (tpm_exhausted)', 'groq:meta-llama/llama-4-scout-17b-16e-instruct (tpm_exhausted)']

## Ordered-role steer (research-cascade distill path)

- `run_role('steer')` → groq:qwen/qwen3-32b ok=True fell=1

## Live budget ledger the tracker wrote (per provider:model, 60s/24h windows)

```json
{
  "cerebras:zai-glm-4.7": {
    "req": [
      1780132341.9025352,
      1780132346.794767,
      1780132349.6749651,
      1780132354.3619673
    ],
    "tok": [
      [
        1780132341.9025352,
        4131
      ],
      [
        1780132346.794767,
        4131
      ],
      [
        1780132349.6749651,
        4131
      ],
      [
        1780132354.3619673,
        274
      ]
    ]
  },
  "cerebras:gpt-oss-120b": {
    "req": [
      1780132341.902744,
      1780132346.7950299,
      1780132349.6755877
    ],
    "tok": [
      [
        1780132341.902744,
        4131
      ],
      [
        1780132346.7950299,
        4131
      ],
      [
        1780132349.6755877,
        4131
      ]
    ]
  },
  "groq:openai/gpt-oss-120b": {
    "req": [
      1780132341.9028935
    ],
    "tok": [
      [
        1780132341.9028935,
        4131
      ]
    ],
    "hdr_remaining": 3805,
    "hdr_reset": 1780132375.2912695
  },
  "groq:qwen/qwen3-32b": {
    "req": [
      1780132341.9103491,
      1780132355.8471463
    ],
    "tok": [
      [
        1780132341.9103491,
        4131
      ],
      [
        1780132355.8471463,
        274
      ]
    ],
    "hdr_remaining": 1936,
    "hdr_reset": 1780132398.0584548
  },
  "groq:meta-llama/llama-4-scout-17b-16e-instruct": {
    "req": [
      1780132341.9105244
    ],
    "tok": [
      [
        1780132341.9105244,
        4131
      ]
    ],
    "hdr_remaining": 29746,
    "hdr_reset": 1780132343.6605651
  }
}
```

## Conductor fall/skip trace

- cerebras → (next): ValueError: empty content (reasoning budget exhausted?)

## Research-cascade coverage

The deep-research cascade's cloud uplift (dense-source distillation, `mcp-research/corpus.py::_conductor_distill`) calls `conductor_steer` → `run_role('steer')` — the SAME ordered chain + pre-flight budget gate exercised above. So the per-provider RPM/RPD/TPM discipline proven here for the slop-draft fan-out is identically the cascade's rate discipline: an exhausted free model is pre-flight-skipped and the cascade degrades to the next free model or to local distill, rather than 429-crashing.

## PASS criteria

- ≥1 real candidate returned: ✅
- ≥1 Groq rung PRE-FLIGHT skipped on budget (tpm/rpm/rpd): ✅ (count=6)
- ZERO leaked 429/413 (tracker prevented over-limit calls): ✅ (leaked=0)
- ZERO crashes (every round returned a dict): ✅