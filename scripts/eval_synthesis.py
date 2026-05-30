#!/usr/bin/env python3
"""Stage-0 synthesis eval — rank the conductor's synth/steer candidates on the
operator's OWN work, with real calls, real cost, real latency.

WHAT THIS IS (and is not): a synthesize-role candidate sends a compressed brief
and must return a STRUCTURED DIRECTIVE (the Stage-2 schema). We score the
directive's STRUCTURAL quality with a transparent, deterministic rubric — valid
JSON, every required key present, ordered_steps that are concrete, assumptions
stated, per-step confidence, and specificity (does it name real files/APIs from
the brief, not generic filler). We MEASURE cost (from usage + a price table) and
latency. We do NOT execute the directive here — true first-try-verify quality is
the Stage-3 gate on real execution; this is the cheap up-front ranking the spec
asks for, reported honestly.

PRESENCE-GATED: a candidate is evaluated only if its API key is set. Needs >=2
present candidates (the Stage-0 DoD). Free candidates (Cerebras/Groq) cost $0;
the one paid anchor (DeepInfra) costs cents. Nothing sensitive is sent — the
briefs are public architecture questions about this very harness.

Run via scripts/eval-synthesis.sh (loads .env). Output: a ranked quality+cost
table + a synth-default recommendation.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

# ── candidates (Stage-0 local copy; Stage-1 formalizes this in the registry) ──
# Two KINDS, by design — the operator's synth/steer picks are LOCKED, not contestants:
#   kind="default"  the operator's CHOSEN DeepInfra synth/steer models. Run ONCE on
#                   the briefs to CONFIRM they emit valid structured directives at the
#                   expected cost — a confirmation/smoke, NOT a bake-off to replace them.
#   kind="pool"     the FREE cross-family parallel_draft pool. This is the genuinely
#                   open question (which free families give the best draft diversity),
#                   so THESE are the ones we rank.
# price = (in_usd_per_1M, out_usd_per_1M); free tiers are (0, 0).
CANDIDATES = [
    {"id": "deepinfra/V4-Pro", "kind": "default", "env": "DEEPINFRA_API_KEY",
     "base_url": "https://api.deepinfra.com/v1/openai",
     "model": "deepseek-ai/DeepSeek-V4-Pro", "role": "synth",
     "price": (1.30, 2.60), "region": "US", "note": "LOCKED synth default"},
    {"id": "deepinfra/V4-Flash", "kind": "default", "env": "DEEPINFRA_API_KEY",
     "base_url": "https://api.deepinfra.com/v1/openai",
     "model": "deepseek-ai/DeepSeek-V4-Flash", "role": "steer",
     "price": (0.10, 0.20), "region": "US", "note": "LOCKED steer default + draft anchor"},
    {"id": "cerebras/GLM-4.7", "kind": "pool", "env": "CEREBRAS_API_KEY",
     "base_url": "https://api.cerebras.ai/v1",
     "model": "zai-glm-4.7", "role": "draft",
     "price": (0.0, 0.0), "region": "US", "note": "free (preview)"},
    {"id": "cerebras/gpt-oss-120b", "kind": "pool", "env": "CEREBRAS_API_KEY",
     "base_url": "https://api.cerebras.ai/v1",
     "model": "gpt-oss-120b", "role": "draft",
     "price": (0.0, 0.0), "region": "US", "note": "free (preview)"},
    {"id": "groq/gpt-oss-120b", "kind": "pool", "env": "GROQ_API_KEY",
     "base_url": "https://api.groq.com/openai/v1",
     "model": "openai/gpt-oss-120b", "role": "draft",
     "price": (0.0, 0.0), "region": "US", "note": "free"},
    {"id": "groq/qwen3-32b", "kind": "pool", "env": "GROQ_API_KEY",
     "base_url": "https://api.groq.com/openai/v1",
     "model": "qwen/qwen3-32b", "role": "draft",
     "price": (0.0, 0.0), "region": "US", "note": "free"},
    # NOTE: DeepSeek-direct is intentionally NOT a Stage-0 candidate for THIS operator:
    # the key is present but the account is unfunded ($0 balance), so a paid call 402s.
    # The Stage-1 registry still lists it as a presence-gated synth rung (below the US
    # hosts by design) — there the role executor's silent-fallback treats the 402 like
    # any failure and falls through. Here we just keep the eval table clean.
]

# ── real synthesis briefs (deep, ambiguous, no cheap oracle — the synth regime) ─
# These are genuine architecture questions about THIS harness: exactly the kind
# of decomposition the synthesize role exists for. Public; nothing sensitive.
BRIEFS = [
    {
        "title": "presence-gated role executor",
        "blocker": "An optional cloud 'conductor' must use as-many-or-as-few provider "
                   "API keys as the operator has set. A role (steer/synth/escalate) has "
                   "an ordered provider chain. At call time we must skip rungs whose key "
                   "is absent, call the first present rung, and on failure/429/5xx/timeout "
                   "SILENTLY fall through to the next present rung and log it — NEVER raising "
                   "into the local orchestrator's core loop. If no rung succeeds, return a "
                   "graceful 'proceed local' signal.",
        "decision": "Design the control flow for this presence-gated, silent-fallback role "
                    "executor: the resolver (present rungs), the per-rung try/advance loop, "
                    "the USD-cap interaction (paid rungs blocked -> behave as absent), and the "
                    "never-raise 'proceed local' return. Identify the failure modes and the "
                    "exact conditions under which control returns to local execution.",
    },
    {
        "title": "verifier-selected parallel-draft fan-out",
        "blocker": "On VERIFIABLE coding subtasks (objective test oracle), we want best-of-N "
                   "across a POOL of free/cheap models from different families (Cerebras GLM, "
                   "Cerebras gpt-oss, Groq gpt-oss, Groq qwen3, Groq llama-4, + a paid DeepSeek "
                   "anchor). Each has its own RPM/RPD budget. We fan out concurrently, run every "
                   "candidate diff through a deterministic verifier (lint->type->tests), and the "
                   "VERIFIER (not a model) selects the winner.",
        "decision": "Design the concurrent fan-out: respecting each provider's live RPM/RPD "
                    "budget, skipping exhausted sources, degrading to fewer candidates or N=1-local "
                    "rather than failing, selecting by verifier verdict (most tests passed, then "
                    "smallest diff), and the none-pass fallback to the synthesize role. State the "
                    "gate that decides a subtask is 'verifiable' vs 'ambiguous'.",
    },
    {
        "title": "deterministic brief-assembler",
        "blocker": "A weak local model must NOT hand-write the brief it sends to a stronger cloud "
                   "model. Instead a deterministic assembler pulls goal/done-so-far from PLAN.md + "
                   "checkpoint log, original directives verbatim, architecture-state + "
                   "failed-approaches from a knowledge graph + watchdog stuck-summaries, and "
                   "token-budgeted code excerpts from a graph-RAG. The local model writes ONLY the "
                   "current_blocker and decision_needed fields.",
        "decision": "Design the brief schema and assembly: the compact (<=8K, steer) vs full "
                    "(15-30K, synth) profiles, a request_more progressive-disclosure mechanism, the "
                    "structured directive the cloud returns (ordered_steps, files_to_touch, "
                    "apis_to_use, tests_to_write, pitfalls, per-step confidence, assumptions), and "
                    "how token budgeting picks which RAG excerpts to include.",
    },
    {
        "title": "advisory-with-verify-gate authority",
        "blocker": "The cloud model is smarter but BLIND (no repo access); the local model is weaker "
                   "but SIGHTED. Cloud directives must be ADVISORY and gated before any commit: check "
                   "each stated assumption against ACTUAL repo state, run a static gate (compile/type/"
                   "lint, APIs exist), write the prescribed tests FIRST and run them, and on "
                   "low-confidence + high-blast-radius get a second synth opinion; if the two opinions "
                   "disagree, escalate or surface to a human.",
        "decision": "Design directive_verify: the assumption-check step (how to verify 'function X "
                    "exists' against the repo and reject + re-brief on a false assumption, appending it "
                    "to failed_approaches), the static + test gates, the confidence-escalation trigger, "
                    "and the ordering so nothing executes until the gate passes.",
    },
    {
        "title": "stingy classifier-gated invocation ladder",
        "blocker": "Reaching to cloud must be RARE and justified. A difficulty classifier tags each "
                   "subtask. The ladder by subtask type: verifiable+hard -> parallel_draft (best-of-N "
                   "free) -> if none pass, synthesize; ambiguous+hard -> steer (cheap nudge) -> "
                   "synthesize (deep brief); frontier-novel / synth-failed -> escalate (Opus) ONLY if "
                   "synth fails verify twice or two opinions disagree on a high-blast-radius decision.",
        "decision": "Design the invocation policy: the exact trigger conditions wiring the classifier "
                    "to each ladder rung, the budget/cap interaction, what gets recorded to the "
                    "knowledge graph so the classifier LEARNS which subtasks needed which tier, and the "
                    "honest frequency targets (synth <=~15/project, Opus <=~3) with the signal to watch "
                    "if Opus calls exceed 3 (brief quality is the bottleneck).",
    },
]

SYS = (
    "You are a senior software architect acting as a STATELESS synthesizer. You receive a compressed "
    "brief about a blocker and must return a single STRUCTURED DIRECTIVE as STRICT JSON (no prose "
    "outside the JSON, no markdown fences). The local executor is sighted (has the repo) but weaker; "
    "you are blind but stronger. Your directive is ADVISORY and will be verified before execution, so "
    "every assumption you make MUST be listed explicitly so it can be checked.\n\n"
    "Return EXACTLY this JSON shape:\n"
    "{\n"
    '  "ordered_steps": [{"step": "<concrete action>", "confidence": "high|medium|low"}],\n'
    '  "files_to_touch": ["<path or component>"],\n'
    '  "apis_to_use": ["<function/class/endpoint the executor should call>"],\n'
    '  "tests_to_write": ["<assertion the executor should write FIRST>"],\n'
    '  "pitfalls": ["<a concrete failure mode to avoid>"],\n'
    '  "assumptions": ["<a fact about the repo you assumed; each must be checkable>"]\n'
    "}\n"
    "Be specific and reference the brief's concrete nouns; do not emit generic placeholder text."
)

REQUIRED_KEYS = ("ordered_steps", "files_to_touch", "apis_to_use", "tests_to_write",
                 "pitfalls", "assumptions")


def _call(cand: dict[str, Any], brief: dict[str, str], timeout: float = 90.0) -> dict[str, Any]:
    key = os.environ.get(cand["env"], "")
    user = (f"## Blocker\n{brief['blocker']}\n\n## Decision needed\n{brief['decision']}\n\n"
            "Return the structured directive JSON now.")
    payload = {
        "model": cand["model"],
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        "max_tokens": 6000,  # generous: several candidates are reasoning models (burn thinking budget)
        "temperature": 0.3,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    t0 = time.monotonic()
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{cand['base_url']}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    dt = time.monotonic() - t0
    usage = data.get("usage", {}) or {}
    try:
        content = data["choices"][0]["message"].get("content")
    except Exception:  # noqa: BLE001
        content = None
    return {"content": content, "usage": usage, "latency_s": dt}


def _extract_json(text: str | None) -> dict | None:
    """Best-effort: parse the JSON directive, tolerating fences / leading prose."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    # find the outermost object
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1 or b <= a:
        return None
    try:
        return json.loads(s[a:b + 1])
    except Exception:  # noqa: BLE001
        return None


def _score(directive: dict | None, brief: dict[str, str]) -> tuple[int, list[str]]:
    """Transparent structural rubric, 0..7. Reported per-criterion."""
    notes: list[str] = []
    if directive is None:
        return 0, ["invalid/absent JSON"]
    score = 1  # parsed
    present = [k for k in REQUIRED_KEYS if isinstance(directive.get(k), list) and directive.get(k)]
    if len(present) == len(REQUIRED_KEYS):
        score += 1
        notes.append("all keys")
    else:
        notes.append(f"keys {len(present)}/{len(REQUIRED_KEYS)}")
    steps = directive.get("ordered_steps") or []
    concrete_steps = [s for s in steps if isinstance(s, dict) and len(str(s.get("step", ""))) > 15]
    if len(concrete_steps) >= 3:
        score += 1
        notes.append(f"{len(concrete_steps)} steps")
    if any(isinstance(s, dict) and s.get("confidence") in ("high", "medium", "low")
           for s in steps):
        score += 1
        notes.append("per-step conf")
    if directive.get("assumptions"):
        score += 1
        notes.append("assumptions")
    # specificity: does it name concrete nouns from the brief (not generic filler)?
    blob = json.dumps(directive).lower()
    nouns = ["plan.md", "rpm", "rpd", "verify", "verifier", "knowledge graph", "kg", "rag",
             "checkpoint", "directive", "assumption", "escalat", "classif", "budget",
             "fallback", "fall through", "present", "absent", "confidence", "blast",
             "ordered", "pool", "fan", "brief", "schema", "token"]
    hits = sum(1 for n in nouns if n in blob)
    if hits >= 6:
        score += 1
        notes.append(f"specific({hits})")
    else:
        notes.append(f"generic({hits})")
    if len(json.dumps(directive)) > 600:  # substantive, not a stub
        score += 1
        notes.append("substantive")
    return score, notes


def _run_candidate(c: dict[str, Any], n: int) -> dict[str, Any]:
    a: dict[str, Any] = {"cand": c, "score": 0, "cost": 0.0, "lat": 0.0, "ok": 0, "n": 0}
    for brief in BRIEFS:
        cid = c["id"]
        try:
            res = _call(c, brief)
        except Exception as e:  # noqa: BLE001
            print(f"    {cid:<22} {brief['title']:<34} ERROR {type(e).__name__}: {str(e)[:40]}")
            a["n"] += 1
            continue
        d = _extract_json(res["content"])
        sc, notes = _score(d, brief)
        u = res["usage"]
        cost = (u.get("prompt_tokens", 0) / 1e6 * c["price"][0]
                + u.get("completion_tokens", 0) / 1e6 * c["price"][1])
        a["score"] += sc
        a["cost"] += cost
        a["lat"] += res["latency_s"]
        a["ok"] += 1 if d else 0
        a["n"] += 1
        print(f"    {cid:<22} {brief['title']:<34} {sc}/7  ${cost:.5f}  {res['latency_s']:.1f}s  [{','.join(notes)}]")
    return a


def main() -> None:
    present = [c for c in CANDIDATES if os.environ.get(c["env"])]
    defaults = [c for c in present if c["kind"] == "default"]
    pool = [c for c in present if c["kind"] == "pool"]
    if len(present) < 2:
        print(f"  Only {len(present)} candidate key(s) present; Stage-0 needs >=2. "
              "Set more *_API_KEY in .env. SKIPPING (informational).")
        return
    n = len(BRIEFS)
    maxq = n * 7
    print(f"═══ Stage-0 synthesis eval — {len(BRIEFS)} real briefs ═══")
    print("  The operator's synth=V4-Pro / steer=V4-Flash picks are LOCKED — confirmed, not contested.")
    print("  Ranking energy goes to the FREE parallel_draft pool (the genuinely open choice).\n")

    # ── (1) CONFIRM the locked DeepInfra defaults (smoke, not a bake-off) ──────
    conf: dict[str, dict[str, Any]] = {}
    if defaults:
        print("  ── CONFIRM locked defaults (DeepInfra; do they emit valid directives, at what cost?) ──")
        for c in defaults:
            conf[c["id"]] = _run_candidate(c, n)
        print()
        print(f"  {'locked default':<22} {'role':<6} {'valid':>6} {'qual':>7} {'$tot':>9} {'$/brief':>9} {'avg_s':>6}")
        print("  " + "-" * 70)
        for c in defaults:
            a = conf[c["id"]]
            ok = "OK" if a["ok"] == n and a["score"] >= n * 5 else "CHECK"
            print(f"  {c['id']:<22} {c['role']:<6} {a['ok']:>3}/{n:<2} {a['score']:>3}/{maxq:<3} "
                  f"${a['cost']:>8.5f} ${a['cost']/n:>8.5f} {a['lat']/n:>5.1f}  [{ok}]")
        print("  " + "-" * 70)
        vp = conf.get("deepinfra/V4-Pro")
        if vp:
            verdict = ("✔ confirmed: emits valid structured directives — V4-Pro stays the synth default"
                       if vp["ok"] == n and vp["score"] >= n * 5
                       else "⚠ V4-Pro produced weak/invalid directives on some briefs — inspect rows above")
            print(f"  {verdict}\n")

    # ── (2) RANK the free draft pool (the real open question) ──────────────────
    if pool:
        print("  ── RANK free draft pool (cross-family diversity for parallel_draft best-of-N) ──")
        agg = {c["id"]: _run_candidate(c, n) for c in pool}
        print()
        ranked = sorted(pool, key=lambda c: (-agg[c["id"]]["score"], agg[c["id"]]["lat"]))
        print(f"  {'pool candidate':<22} {'valid':>6} {'qual':>7} {'avg_s':>6}  (all free, $0)")
        print("  " + "-" * 56)
        for c in ranked:
            a = agg[c["id"]]
            print(f"  {c['id']:<22} {a['ok']:>3}/{n:<2} {a['score']:>3}/{maxq:<3} {a['lat']/n:>5.1f}")
        print("  " + "-" * 56)
        strong = [c["id"] for c in ranked if agg[c["id"]]["score"] >= n * 4]
        print(f"  Pool keeps cross-family diversity; strongest drafters: {', '.join(strong) or '(none cleared bar)'}.")
        print("  parallel_draft fans across ALL present pool members — the verifier, not this score, picks the winner.\n")
    else:
        print("  (no free pool keys present — set CEREBRAS_API_KEY / GROQ_API_KEY to rank the draft pool)\n")

    print("  NOTE: transparent structural rubric + measured cost/latency, reported honestly. True")
    print("  first-try-verify quality is the Stage-3 gate on real execution; this is the up-front pass.")


if __name__ == "__main__":
    main()
