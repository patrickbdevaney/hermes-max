# CLAUDE_system_validation.md — Whole-System Coherence Eval, Seamless Install, & Emergent-Behavior Sanity Check

You are doing the FINAL integration pass on the complete `hermes-max` system: prove every component
works together coherently and usefully, make install/getting-started seamless and hardware-agnostic,
and instrument specifically for the emergent failure modes that isolated tests miss. Work in STAGES,
in order; each independently committed, smoke-tested, validated. Read the whole spec first. Report
after each stage. This spec ADDS no new heavy machinery — it validates, packages, and instruments what
exists, plus two sanctioned small additions (reranker, per-repo mutation testing).

## THE GOAL
Three things: (1) a one-command install + getting-started that works across hardware tiers with
lean/free/full modes; (2) a rapid real-inference dry-run that exercises every component in its loop and
dumps observable traces to a file, proving the system boots and coheres; (3) a combinatorial
emergent-behavior eval that specifically hunts the interaction failure modes — with explicit
instrumentation for the three highest-suspicion risks (Banyan focus-thrash, research-noise directive
poisoning, ladder cascade-escalation).

## NON-NEGOTIABLE DISCIPLINE (unchanged)
Orchestration layer has ZERO CUDA dependencies — pure Python + HTTP + datastores; the ONLY
platform-specific code is the inference server below `$LOCAL_LLM_URL`, which is external and swappable.
Never modify Hermes's core loop; everything is presence-gated and degrades cleanly; back up config;
commit per stage; failures are signal, reported honestly.

---

## STAGE 1 — ONE-COMMAND INSTALL + HARDWARE-AGNOSTIC GETTING-STARTED
Make clone→configure→boot→wired-to-Hermes a single command, with three modes and hardware-tiered
local-model guidance. **Documentation must NOT reference any specific operator hardware or a single
hard-coded local model** — present hardware as a TEMPLATE TABLE so any user maps their machine to a tier.

- **`bootstrap.sh` (one command, no chmod, idempotent):** detects platform (Linux/macOS/CUDA/Metal/CPU),
  brings up datastores (Postgres, Qdrant, Redis; KG store per Stage 4 below), installs deps into
  isolated venvs per MCP server, health-checks each, wires all MCP servers to Hermes via the manifest,
  and prints a readiness report. Absent optional keys → those features no-op, bootstrap still succeeds.
- **Three modes (single env/flag switch, `CONDUCTOR_MODE`):**
  - **`local`** — local model only (Hermes + driver via `$LOCAL_LLM_URL`). Zero cloud. Fully sovereign,
    $0, works offline. The guaranteed-correct base case.
  - **`free`** — local + free cloud tiers only (Cerebras steer, Groq research-cascade + slop-draft,
    Gemini-Flash last-resort). No paid keys. Rate-limit-mindful (Stage 3 validates this).
  - **`full`** — adds paid reasoning synth + steer (DeepInfra DeepSeek V4-Pro/Flash) and rare Opus
    escalation. The ideal/recommended mode.
  - Each mode is presence-gated on top of the others: `full` falls back through `free` to `local` as
    keys/endpoints disappear. Mode is a documented preference, not a hard requirement.
- **`.env.example`** — assumes `$LOCAL_LLM_URL` already points at the user's running inference server
  (the user sets up their own local model endpoint; we don't manage inference). All cloud keys optional
  and commented, grouped by mode. No required keys for `local` mode.
- **Hardware → driver-model TEMPLATE TABLE in the README** (examples, not prescriptions; the user
  picks). Frame by VRAM/compute tier, recommend edge-friendly GQA/low-KV models. Example shape:

  | Hardware tier (examples) | Approx VRAM | Suggested local driver tier (examples) |
  |---|---|---|
  | DGX Spark / Jetson Thor / RTX 6000 Pro | 96–128GB+ unified/VRAM | Large MoE driver (e.g. Qwen3.6 ~122B-A10B class, or Nemotron-Super) |
  | RTX 5090 / 4090 | 24–32GB | Mid driver (e.g. Qwen3.6 ~35B-A3B, Nemotron, Gemma-4 ~27–31B) |
  | RTX 3090 / 4080 | 16–24GB | Qwen3.6 ~35B-A3B quantized, or ~14–32B dense |
  | M4 Max/Ultra Studio (MLX/GGUF) | 36–128GB unified | Qwen3.6 35B-A3B / larger MoE via MLX or llama.cpp |
  | RTX 4060 Ti / 3060 / gaming laptop | 8–16GB | Smaller GGUF (~14B class) + lean on free/full cloud tiers |
  | Jetson Orin / small edge | 8–32GB | Small driver + heavier cloud uplift |
  | No GPU / VPS | — | Cloud-only driver (cheap model via conductor); `local` mode unavailable |

  Emphasize: smaller local driver → lean harder on the conductor's cloud tiers (the presence-gated
  design makes this automatic). Mention Qwen3.6 series as the default family (GQA-friendly KV, edge
  weight sizes), with Nemotron and Gemma-4 as alternative examples. Inference server per platform:
  vLLM (CUDA), llama.cpp (any/GGUF), MLX (Apple) — all expose OpenAI-compatible endpoints; the
  orchestration is identical above the endpoint.
- **Inference-layer abstraction assertion:** grep the orchestration + all MCP servers for hard CUDA/
  torch imports; assert NONE exist above the `$LOCAL_LLM_URL` boundary (so the client runs on Apple/AMD/
  CPU). Any found → refactor behind the HTTP boundary or flag.

**Stage-1 DoD:** `bootstrap.sh` brings the whole stack up in one command on the test platform; `local`/
`free`/`full` modes switch cleanly and fall back correctly; README has the hardware template table with
no operator-specific references; the no-CUDA-above-endpoint assertion passes. Committed.

## STAGE 2 — SANCTIONED SMALL ADDITIONS (only these two; the rest stay deferred)
Implement ONLY the two items the prime directive sanctions, each as an independent presence-gated
addition, eval-gated (add only if it earns its place):
- **Reranker** — the one sanctioned RAG addition, and ONLY if Stage 5 eval shows retrieval precision is
  the bottleneck. Qwen3-Reranker (local, served via `$LOCAL_LLM_URL`-style endpoint) as a final
  precision pass after hybrid retrieval. Feature-flagged; absent → retrieval works without it.
- **Per-repo mutation testing** — added to mcp-verify, per-repo, ONLY if a repo's eval shows the test
  suite passes bad directives (weak oracle). Feature-flagged; off by default.
- **Everything else stays OUT OF SCOPE** (do NOT build): Neo4j+Graphiti+Cognee beyond the embedded
  store, Letta, 8-stage RAG (HyDE/RAG-Fusion/ColBERT/Self-RAG/HippoRAG), Temporal/LangGraph outer
  scheduler, MAP-Elites/ADAS/OMNI-EPIC, 10-stage verification ladder (fuzz/Lean4/debate), HSM/Merkle/
  Vault, custom multi-agent debate. Each can later attach as one more independent MCP server. The KG
  uses ONE embedded store (SQLite-backed); Hermes cron + the DSPy module cover scheduling; Hermes
  native delegation covers multi-agent; non-root user + sandboxed workdir + allow-lists + native
  `tirith` cover security.

**Stage-2 DoD:** reranker + mutation-testing exist as feature-flagged additions, both default-off, both
degrade cleanly when off; a grep/audit confirms NONE of the out-of-scope systems were built. Committed.

## STAGE 3 — RATE-LIMIT PRODUCTION VALIDATION (research cascade + slop draft under real free-tier limits)
Prove the free-tier cascade and slop drafting actually work within real rate limits — this is the
production-viability gate for `free` mode.
- **Per-provider live budget tracker** (in the conductor role-executor): track RPM/RPD/TPM/TPD per
  provider+model from the registry, updated live from response headers (Groq returns
  `x-ratelimit-remaining-*`); BEFORE firing a call, check the call fits the remaining budget; if not,
  skip that provider/model and fall through. Never fire-and-absorb-429.
- **Verified constraints to respect** (May 2026, volatile): Groq GPT-OSS-120B 30 RPM / 1K RPD / **8K
  TPM**, Qwen3-32B 60 RPM / 1K RPD / **6K TPM** (so a single >6K-token brief 413s — cap Groq-bound
  briefs at ~3-4K tokens to leave output headroom); Cerebras GLM-4.7 / GPT-OSS-120B **64K ctx**, 5 RPM
  / 2,400 RPD / 1M TPD (context headroom but low RPM — serialize); Gemini 2.5 Flash **~20 RPD** (verified
  on free-account console — last-resort only). Semantic Scholar 5,000 req/5min shared unauth (fine); arXiv
  ~1 req/3s; GitHub search 30 req/min with PAT.
- **Role split validated:** Cerebras for STEER (needs context headroom, tolerates low RPM); Groq for
  high-volume RESEARCH-CASCADE steps + SLOP-DRAFT (needs throughput, small briefs, serialize within TPM);
  Gemini-Flash last-resort steer only.
- **Research cascade rate-discipline:** serialize per-Groq-model calls, stagger to respect TPM, back off
  on 429 with `retry-after`, fall to the next free model or to local. A research run must COMPLETE (not
  crash) when a provider exhausts — degrade to fewer sources / local distill.

**Stage-3 DoD:** a real research cascade + a real best-of-N slop-draft run COMPLETE within free-tier
limits without 429/413 crashes (budget tracker skips exhausted models, briefs stay under TPM, run
degrades to local when free exhausts); a trace shows the budget tracker preventing an over-limit call.
Committed.

## STAGE 4 — KG STORE SIMPLIFICATION (one embedded store)
Per the prime directive, the KG uses ONE embedded store, not the full Neo4j+Graphiti+Cognee stack.
- Use an embedded SQLite-backed graph/memory store for the KG (entities, edges, provenance, temporal
  validity, citation graph). Hermes native memory + this embedded KG cover what Letta/Graphiti-service
  would. This removes a heavy external dependency and makes the one-command install trivial on any
  hardware (no Neo4j container required for the base case).
- Keep the SAME logical schema (episodes/entities/edges/provenance) so a future swap to a graph DB is a
  backend change, not a schema change. Feature-flag a Neo4j backend as OPTIONAL for power users, default
  embedded.

**Stage-4 DoD:** KG runs on the embedded store with the full schema (provenance + temporal validity +
citation edges); bootstrap needs no external graph DB in the base case; optional Neo4j backend flag
works. Committed.

## STAGE 5 — RAPID REAL-INFERENCE DRY RUN (prove the whole loop boots & coheres, fast)
A single fast command that exercises EVERY component once against REAL inference (local model + whatever
cloud keys are present) and dumps a human-readable trace file. Optimize for SPEED — this is a smoke
proof the system coheres end-to-end, not a benchmark.
- **`scripts/dry_run.sh`** runs one tiny end-to-end task that touches every component in its loop:
  1. driver (local) receives a trivial but real coding subtask;
  2. classifier routes it; watchdog arms;
  3. one steer call (free tier if present, else skip-logged);
  4. one research call (one source per type, tiny query) → corpus write (disk .md) + KG ingest;
  5. one synth call (brief_assemble → directive → verify gate);
  6. one parallel_draft fan-out (best-of-N across present free pool) → verifier select;
  7. one Banyan select/update cycle over 2 dummy namespaces;
  8. checkpoint write + revert test;
  9. escalation path exercised in DRY form (mock the Opus call if no key / cap=0) to prove the ladder
     wiring without spending.
- **Trace output:** write a single human-readable `dry_run_trace.md` (and mirror spans to Langfuse)
  with, per step: component, provider/model used (or skipped + why), latency, token/cost, PASS/FAIL,
  and the actual input/output snippet. The file is the proof artifact — a human can read it top to
  bottom and see the whole system fired coherently.
- **Speed:** use the smallest viable inputs, run independent steps concurrently where safe, target a
  few minutes wall-clock. The point is "does it all wire together and produce sane outputs," fast.
- **Mode-aware:** runs in `local`/`free`/`full`; in `local` it proves the base case with zero cloud and
  every cloud step cleanly skip-logged.

**Stage-5 DoD:** `dry_run.sh` completes in `local`, `free`, and `full` modes; `dry_run_trace.md` shows
every component fired (or cleanly skipped with reason) with sane real outputs; the base `local` case
passes with zero cloud keys. Committed.

## STAGE 6 — COMBINATORIAL EMERGENT-BEHAVIOR EVAL (hunt the interaction failure modes)
Isolated component tests pass; this stage hunts the EMERGENT failures that only appear in combination.
Run a small set of REAL (not trivial) multi-subtask tasks end-to-end with full tracing, then assert on
the interaction patterns. Instrument SPECIFICALLY for the three highest-suspicion risks:

- **RISK A — Banyan focus-thrash (highest suspicion):** UCB1 is a stationary-bandit algorithm; coherent
  BUILDING needs sustained focus, but UCB1's exploration term pulls the agent toward undervisited
  namespaces. SUSPICION: Banyan is net-POSITIVE for the research/exploration loop (breadth is the goal)
  but net-NEGATIVE for the coding/build loop (it abandons half-finished hard subsystems for shinier
  easy ones). **Instrument:** log every Banyan selection + whether it abandoned an incomplete subtask;
  compute a "thrash score" (switches away from incomplete work / total switches). **Gate:** if build-loop
  thrash exceeds a threshold, the eval must FLAG that Banyan should be SCOPED TO THE RESEARCH LOOP ONLY
  (a config split: UCB1 governs research-namespace selection; the build loop uses
  finish-what-you-started / dependency-order, NOT UCB1). Make that split implementable via config.
- **RISK B — research-noise directive poisoning:** good research helps directives; noisy/irrelevant
  research poisons the synth brief → confident wrong directives (caught by verify, but at cost).
  SUSPICION: research PRECISION matters more than recall here. **Instrument:** for each synth call fed by
  research, log whether the research findings were actually relevant (did the directive cite them? did
  the verify gate pass?); compute a "research contamination rate" (synth calls where research findings
  appeared in a directive that FAILED verify). **Gate:** if contamination is high, the eval flags that
  research feeding synth needs a relevance filter / higher authority threshold before ingestion.
- **RISK C — ladder cascade-escalation:** each tier has sane triggers individually, but a single hard
  subtask could cascade driver→steer→synth→research→synth→escalate, burning money/time before the cap
  fires. **Instrument:** log per-subtask the full tier sequence + cumulative cost/time; compute max
  cascade depth and per-subtask spend. **Gate:** enforce a GLOBAL per-subtask budget (cost + tier-count
  ceiling), not just per-tier triggers; if a subtask hits it, stop and surface to the operator rather
  than escalating further.
- **General coherence assertions:** verify-gate catches injected bad directives regardless of source;
  every component degrades without crashing the run when its key/endpoint is killed mid-task;
  compounding works (a finding from task 1 is retrievable and useful in task 2); no component corrupts
  another's state (KG/RAG/checkpoint integrity intact after a full run).
- **Empty-base-case correctness (critical):** run the WHOLE eval with ZERO accumulated data (fresh KG,
  no task history, no calibration). Assert every adaptive component has correct empty-base behavior:
  UCB1 uses an optimistic prior (explores broadly, no utility data needed); saturation detection is
  DISABLED below a minimum history (≥10 tasks/namespace — never flag saturated on thin data); the
  difficulty classifier defaults to ESCALATE-WHEN-UNCERTAIN (conservative). The system must work WELL on
  the first project, not after a warm-up.

**Stage-6 DoD:** the combinatorial eval runs real multi-subtask tasks with full tracing; produces an
emergent-behavior report scoring the three risks (thrash, contamination, cascade) with the config
remedies wired and toggle-able (Banyan-research-only split, research relevance filter, global
per-subtask budget); the empty-base-case run passes with zero data; coherence + degradation +
compounding + no-corruption assertions all hold; output is a readable `emergent_eval_report.md`.
Committed.

---

## REASONING SUMMARY (carry into the eval — which components to watch)
- **High-confidence value-add:** conductor (proven), verify-gate (the keystone — makes risky components
  safe), corpus/RAG, best-of-N slop-draft (verifier makes mediocre models safe), research source breadth
  (gated by verify).
- **Suspicion — watch closely (Stage 6 instruments these):** (A) Banyan UCB1 — likely good for research
  breadth, likely harmful for build-loop focus; default to scoping it to the research loop if thrash
  shows. (B) research→synth feed — precision over recall; filter if contamination shows. (C) the
  escalation ladder — needs a global per-subtask budget to prevent cascade. The eval must produce
  EVIDENCE on each, not assume.

## OUTPUT / OBSERVABILITY
- All eval/dry-run traces → human-readable `.md` files (`dry_run_trace.md`, `emergent_eval_report.md`)
  AND Langfuse spans. Execution traces live in Langfuse + these report files; the knowledge corpus is
  the separate on-disk markdown store. Do not conflate.
- README: install (one command), the three modes, the hardware template table (no operator-specific
  refs), how to run the dry-run and the eval, and the honest emergent-behavior findings (including the
  Banyan-scope recommendation if the eval flags thrash).

## DEFINITION OF DONE
One-command bootstrap brings the whole stack up across hardware tiers with local/free/full modes (no
CUDA above the inference endpoint; hardware presented as a template table with Qwen3.6/Nemotron/Gemma-4
examples, no operator-specific references); only the two sanctioned additions (reranker, per-repo
mutation testing) were built, both eval-gated and default-off, everything else confirmed out-of-scope;
the free-tier research cascade + slop draft provably complete within real rate limits via a live budget
tracker; a fast real-inference dry-run proves every component fires coherently and dumps a readable
trace; and a combinatorial emergent-behavior eval produces EVIDENCE on the three suspicion risks
(Banyan focus-thrash → research-loop-only split, research-noise contamination → relevance filter, ladder
cascade → global per-subtask budget) with remedies wired and toggle-able, passes the zero-data
empty-base-case, and confirms coherence/graceful-degradation/compounding/no-corruption. The system is
proven to work together — or the specific incoherences are documented with their config remedies.
Nothing out-of-scope built; core loop untouched; config backed up; each stage committed; failures
reported honestly.