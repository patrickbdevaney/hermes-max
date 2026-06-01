# CLAUDE_frontier_tier.md — Four-Mode Launch (local/free/full/frontier) + Sparing Opus 4.8 Escalation

You are adding a fourth, opt-in capability tier to the hm launcher: a SPARING frontier-escalation tier
that invokes Claude Opus 4.8 only for the genuinely blue-ocean / frontier-novel synthesis the local
model + DeepSeek tiers can't close — engineered so the invocations are rare enough that the system still
drives ~all tokens locally and the per-month Opus cost stays a small additive amount, preserving the
affordability-performance Pareto vs Claude Code / Codex / Antigravity. Work in STAGES, in order; each
committed and validated. Read the whole spec first. Report after each stage.

## VERIFIED FACTS (May 2026 — the spec is built on these)
- Opus 4.8 model string: **`claude-opus-4-8`**. Pricing **$5/M input, $25/M output** (regular). 1M
  context, 128K max output. Prompt caching up to 90% off input; batch 50% off. Fast mode $10/$50 (do
  NOT use fast mode for this — cost, not latency, is the constraint).
- A frontier-escalation call using the compress-then-reason pattern (~15K distilled input + ~4K output)
  ≈ **~$0.18/call**, dropping toward ~$0.10 with prompt caching on the stable brief prefix.
- DeepSeek V4-Pro synth ≈ $0.047/call; V4-Flash steer ≈ $0.0002/call. Opus is ~4-20× a DeepSeek call —
  in absolute terms still cents, so the ONLY thing that protects the Pareto is INVOCATION RARITY.
- The crossover where Opus defeats the purpose is ~30-40 Opus calls/month (≈ $5-7/mo, approaching
  Claude Code's $20 once you add it's-not-as-seamless friction). Target: ≤15 Opus calls/month so the
  frontier tier adds only ~$2-3/mo on top of the ~$10/mo DeepSeek+electricity base.

## THE FOUR MODES (the hm launch-arg system)
`hm up` / `hm dev` take a mode flag. **Default is `--full`.** Each mode is a superset of the one below;
all are presence-gated on top (a tier with no key silently falls to the one below).

| Mode | Tiers active | Cost | Use |
|---|---|---|---|
| `--local` | local OpenAI-style endpoint ONLY ($VLLM_BASE_URL) | $0 + electricity | fully sovereign, offline, no cloud |
| `--free` | local + free drafting/steer (Groq, Cerebras), rate-limit-respecting | $0 + electricity | velocity uplift, zero API spend |
| `--full` (DEFAULT) | local + free + DeepSeek V4-Flash steer + V4-Pro synth (DeepInfra/direct) | ~$10/mo + elec | the cheap lean frontier synthesis — the recommended daily driver |
| `--frontier` | full + SPARING Opus 4.8 escalation (requires ANTHROPIC_API_KEY) | ~$12-15/mo + elec | closes the last gap to Opus/Claude-Code on blue-ocean frontier-novel work |

- `hm up` with no arg = `--full`. `hm up --local` / `--free` / `--frontier` select explicitly.
- `--frontier` REQUIRES `ANTHROPIC_API_KEY` present; if the flag is passed but the key is absent, warn
  clearly and fall back to `--full` behavior (Opus tier OFF). Never silently pretend frontier is active.
- The mode sets which conductor roles/chains are eligible; the per-subtask invocation gating (below)
  still decides whether any given hard subtask actually reaches the Opus tier. Mode = ceiling, gating =
  actual use.

## STAGE 1 — THE FRONTIER ESCALATION TIER (compress-then-reason, plan-to-artifact, three-gate)
Add Opus 4.8 as the top rung of the escalate role in mcp-escalation, behind `--frontier` mode + key.
- **Model + endpoint:** `claude-opus-4-8` via Anthropic API (`ANTHROPIC_API_KEY`), OpenAI-compatible or
  Anthropic SDK as the existing conductor client supports. Hard USD cap enforced in-server (reuse the
  escalation cap; default e.g. `FRONTIER_USD_CAP_MONTHLY=10`, `FRONTIER_USD_CAP_DAILY=2`); cap hit →
  fall back to V4-Pro synth and log.
- **The compress-then-reason pattern (this is what keeps Opus affordable even when used):** Opus does
  NOT ingest the full raw context. Instead:
  1. **V4-Pro (the cheap model) compresses** the full situation — repo state, failed approaches, research
     findings, the specific frontier problem, relevant code — into the most august, sophisticated,
     optimally-compressed brief (~10-15K tokens). The cheap model does the expensive-to-Opus token
     compression.
  2. **Opus 4.8 reasons** on that distilled brief and produces the frontier plan — the novel
     architecture, the blue-ocean approach, the ordered directive. Minimal Opus input tokens (the
     expensive part), maximal Opus reasoning value.
  This cuts an Opus call from ~$0.40 (if it ingested everything) to ~$0.18 (reasoning on a compressed
  brief), and falls toward ~$0.10 with prompt caching on the stable brief prefix.
- **Plan-to-artifact (the expensive insight is captured permanently):** Opus's output is written to a
  durable `FRONTIER_PLAN.md` in the project AND ingested into RAG/KG with provenance (source=opus-4.8,
  the problem, the date, the citations). So the frontier reasoning is reusable, compounds, and is never
  paid for twice for the same problem. The driver agent receives the plan as a directive; the artifact
  persists.
- **Three-gate invocation (ALL must trip — this is what keeps it sparing):**
  1. **Mode gate:** `--frontier` active + `ANTHROPIC_API_KEY` present.
  2. **Difficulty gate:** the classifier flags the subtask FRONTIER-NOVEL (not merely HARD) — a genuinely
     blue-ocean problem with no reference implementation / no clear approach. HARD-but-known stays at
     V4-Pro.
  3. **Failure gate:** V4-Pro synth has ALREADY failed the verify gate twice on this subtask, OR two
     independent V4-Pro opinions disagree on a high-blast-radius decision. Opus is the tie-breaker /
     last-resort, never the first attempt.
  Only when all three trip does Opus fire. Log every Opus invocation with the three-gate justification.
- **Verify-gated like everything else:** the Opus directive is advisory — directive_verify checks its
  assumptions against real repo state before the driver executes. Opus being expensive doesn't make it
  trusted-blind.

**Stage-1 DoD:** with `--frontier` + key, a FRONTIER-NOVEL subtask that twice-fails V4-Pro escalates to
Opus 4.8 via compress-then-reason (V4-Pro writes the ~12K brief, Opus reasons, ≤$0.18 logged); the plan
writes to FRONTIER_PLAN.md + RAG/KG with provenance; a merely-HARD subtask does NOT reach Opus (stays
V4-Pro); `--frontier` without the key warns + falls to `--full`; the USD cap blocks + falls back to
V4-Pro when hit; the Opus directive passes through directive_verify. Committed.

## STAGE 2 — THE hm MODE SYSTEM (local/free/full/frontier wired end-to-end)
- Add the mode flag to `hm up` and `hm dev` (default `--full`). The mode resolves which conductor
  roles/chains are eligible and sets the corresponding env (e.g. `CONDUCTOR_MODE=local|free|full|frontier`).
- The conductor's presence-gating + per-role chains already exist (Stage 1 of the conductor build); the
  mode is the CEILING on which tiers are eligible. `--local` disables all cloud roles; `--free` enables
  only free drafting/steer; `--full` adds DeepSeek paid synth/steer; `--frontier` adds the Opus rung.
- `conductor_status` / `hm status` shows the active mode + which tiers are live + (for frontier) the
  month-to-date Opus spend vs cap, so the operator always sees what's enabled and what it's costing.
- Falls through cleanly: `--frontier` with no Anthropic key → `--full`; `--full` with no DeepInfra key →
  `--free`; `--free` with no Groq/Cerebras keys → `--local`. The system always resolves to the highest
  tier whose keys are actually present, never breaks.

**Stage-2 DoD:** `hm up --local|--free|--full|--frontier` each activate exactly their tier ceiling;
default (no arg) = `--full`; each falls through to the next-lower tier when a required key is absent;
`hm status` shows active mode + live tiers + frontier spend-vs-cap; the per-subtask gating still governs
actual Opus use within `--frontier`. Committed.

## STAGE 3 — COST LEDGER + SPARING-NESS PROOF
The whole point is that Opus stays rare. Make that measurable and enforced.
- Extend the cost ledger: per-mode, per-tier, per-month spend; specifically track Opus call count +
  cost, and assert against the sparing target (≤15 Opus calls/month default; configurable
  `FRONTIER_TARGET_CALLS_MONTHLY`).
- `hm cost` (or `scripts/conductor-report.sh` extended): prints month-to-date — local tokens (free),
  DeepSeek synth/steer calls + cost, Opus calls + cost, total vs the ~$12-15/mo frontier-mode target,
  and a comparison line vs Claude Code's $20/mo. If Opus calls exceed the sparing target, the report
  WARNS that frontier use is drifting toward defeating the Pareto and suggests tightening the difficulty
  gate (the frontier-novel threshold) — because frequent Opus means the classifier is mis-flagging
  merely-hard as frontier-novel, or the work genuinely needs Claude Code.
- The honest framing in the report: this system wins the Pareto WHILE Opus stays rare; if your work is
  predominantly frontier-novel, the report should say so and note Claude Code may be the better tool for
  that work — don't hide it.

**Stage-3 DoD:** `hm cost` reports per-tier month-to-date spend with the Opus count + cost vs the sparing
target and a vs-Claude-Code line; exceeding the target warns and suggests tightening the difficulty gate;
the ledger enforces the USD cap. Committed.

## NON-NEGOTIABLE DISCIPLINE
Never modify Hermes's core loop; the Opus tier is the top rung of the existing escalate role behind a
mode flag + key, a stateless advisor behind a tool, verify-gated; presence/mode-gated; degrades to
`--full` then `--free` then `--local`; never put secrets in any brief on any route; back up config;
commit per stage; failures reported honestly.

## OUT OF SCOPE
- Opus fast mode (cost, not latency, is the constraint — regular mode only).
- Opus as a frequent/default synth tier (it is escalation-only, three-gated).
- Any tier that bills without an explicit mode flag (frontier is opt-in by `--frontier`).
- Routing the full raw context to Opus (always compress-then-reason via V4-Pro first).

## REPORT (per stage)
What landed; the compress-then-reason flow proof (V4-Pro brief size → Opus reasoning → artifact);
the three-gate invocation test (frontier-novel-twice-failed fires Opus, merely-hard does not); the mode
fall-through tests; the cost ledger + sparing-target report; git SHA.

## DEFINITION OF DONE
`hm up`/`hm dev` take `--local|--free|--full|--frontier` (default `--full`), each a presence-gated
superset that falls through to the highest tier whose keys are present; the `--frontier` tier invokes
Opus 4.8 (`claude-opus-4-8`, $5/$25, regular mode) ONLY behind three gates (frontier mode+key,
classifier=frontier-novel, V4-Pro already failed verify twice / opinions disagree), using the
compress-then-reason pattern (V4-Pro writes the ~12K brief, Opus reasons for ~$0.18→~$0.10 cached),
writing the result to a durable FRONTIER_PLAN.md + RAG/KG with provenance, verify-gated before execution,
under a hard monthly USD cap; `hm cost` proves Opus stays sparing (≤~15 calls/mo, ~$12-15/mo frontier
total vs Claude Code's $20) and warns if it drifts; and the whole thing degrades cleanly to --full/--free/
--local. The system drives ~all tokens locally, closes the last gap to Opus/Claude-Code only on genuine
blue-ocean frontier-novel work, and the Opus invocations are rare enough to preserve the
affordability-performance Pareto. Core loop untouched; config backed up; each stage committed; failures
reported honestly.