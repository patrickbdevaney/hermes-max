# Hardware tiers & decode-speed estimates

You supply the inference server; hermes-max only talks to it over `$VLLM_BASE_URL`.
The rows below are **examples, not prescriptions** — map your machine to a VRAM /
compute tier and pick any model in that class. A smaller local driver simply leans
harder on the cloud tiers (the presence-gated design makes this automatic).

> **Recommended minimum: the 24–32B class is the floor for an effective local
> executor.** Below that, quality degrades enough that cloud inference
> ([Profile B](profiles.md)) is usually the better choice. A 14B local model
> leaning on cloud uplift is a valid, honest configuration — not a compromise to be
> ashamed of.

## The tier table

| Hardware tier (examples) | Approx VRAM | Suggested local driver | Est. single-stream decode |
|---|---|---|---|
| DGX Spark / Jetson Thor / RTX 6000 Pro | 96–128GB+ unified/VRAM | Large MoE (Qwen3.6 ~122B-A10B, **Nemotron 3 Super 120B-A10B** for the 96–128GB tier) | ~12–25 tok/s *(est, MoE, bandwidth-bound)* |
| RTX 5090 / 4090 | 24–32GB | Mid driver (Qwen3.6 ~35B-A3B, Nemotron, Gemma-4 ~27–31B) | ~40–60 tok/s *(est, A3B)*; ~15–30 tok/s *(est, dense 27–31B)* |
| RTX 3090 / 4080 | 16–24GB | Qwen3.6 ~35B-A3B quantized, or ~14–32B dense | ~30–50 tok/s *(est, A3B q)*; ~10–25 tok/s *(est, dense)* |
| M4 Max/Ultra Studio (MLX/GGUF) | 36–128GB unified | Qwen3.6 35B-A3B / larger MoE via MLX or llama.cpp | ~20–50 tok/s *(est, MLX, varies by tier)* |
| RTX 4060 Ti / 3060 / gaming laptop | 8–16GB | Smaller GGUF (~14B class) + lean on free/full cloud | ~15–35 tok/s *(est, 14B q)*; recommend cloud uplift |
| Jetson Orin / small edge | 8–32GB | Small driver + heavier cloud uplift | ~5–20 tok/s *(est)*; recommend Profile B for serious work |
| No GPU / VPS | — | Cloud-only driver (Profile B, V4-Flash via the conductor) | n/a — API speed |

Every tok/s figure marked *(est)* is an **estimate** from bandwidth extrapolation
(below); treat the measured anchor as the reliable point and scale from your own
device's memory bandwidth.

## Why decode is bandwidth-bound

Single-stream decode reads the active weights once per token, so it is limited by
**memory bandwidth**, not FLOPs:

```
tok/s  ≈  memory_bandwidth_GBps  /  active_param_bytes_per_token
```

### Worked example (use this to estimate your own hardware)

A **35B-A3B** model activates ~3B params per token. At ~2 bytes/param (NVFP4-ish)
that is ~6 GB read per token.

- On a **273 GB/s** device: 273 / 6 ≈ **~46 tok/s ceiling**.
- Measured **~50 tok/s** on Jetson Thor with MTP speculative decode confirms the
  estimate (measured anchor).

Scale by your device's bandwidth: double the bandwidth, roughly double the ceiling.
A dense 27–31B model activates *all* its params per token, so its byte-read is much
larger and its decode is correspondingly slower than an A3B MoE of similar total
size — which is why the MoE families are a sensible default for edge hardware.

### Two more honest caveats

- **Long context inflates time-to-first-token substantially** on edge hardware —
  prefill is compute-heavy and grows with prompt length. The decode estimates above
  are steady-state, not TTFT.
- **Long-horizon work needs the full context window.** Serve the model with a large
  `max_model_len` (e.g. 262144) — on a 65K window the model compresses constantly
  and loses the plan. `hm health` warns if the served `max_model_len` is < 200000.

## The inference server

All three expose an OpenAI-compatible endpoint, so the orchestration above them is
identical — point `$VLLM_BASE_URL` at whichever you run:

- **vLLM** (CUDA)
- **llama.cpp** (any platform / GGUF)
- **MLX** (Apple Silicon)

Which model to actually pick, and how the cloud tiers fill the gap when your local
driver is small, is covered in [profiles.md](profiles.md) and [modes.md](modes.md).
