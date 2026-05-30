#!/usr/bin/env python3
"""Stage-3 rate-limit PRODUCTION validation — REAL free-tier calls, $0.

Proves the conductor's live budget tracker is production-viable for `free` mode:
the research cascade's cloud uplift and the best-of-N slop-draft both flow through
run_role()/draft_fanout(), whose PRE-FLIGHT RPM/RPD/TPM gate must skip an
over-budget rung BEFORE firing — never fire-and-absorb a 429/413.

What this does (CONDUCTOR_MODE=free, so only Cerebras/Groq/Gemini can fire, no
paid spend): it fans out a real best-of-N draft a few rounds in a tight window.
Groq's per-MODEL free TPM is tiny (gpt-oss 8K, qwen3 6K), so after the first
round the tracker has recorded enough real token usage that the next round's Groq
rungs are pre-flight-SKIPPED (reason: tpm/rpm budget), while Cerebras (30K TPM)
keeps producing candidates. The run COMPLETES every round (degrades to fewer
sources) instead of crashing on a 429/413.

PASS criteria (all must hold):
  • >=1 real candidate came back overall (the free pool actually works), AND
  • >=1 Groq rung was PRE-FLIGHT skipped with a budget reason (tpm/rpm/rpd), AND
  • ZERO uncaught 429/413 surfaced as a crash (every round returned a dict).

Writes a human-readable trace to rate_limit_validation_trace.md. Uses an ISOLATED
budget file so it neither pollutes nor is polluted by the operator's live state.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _load_env_file(path: str) -> None:
    """Minimal .env loader (KEY=VALUE), only for keys not already in the env."""
    try:
        for raw in Path(path).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.split("#", 1)[0].strip()
            if k and k not in os.environ:
                os.environ[k] = v
    except FileNotFoundError:
        pass


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    _load_env_file(str(repo / ".env"))

    # Force free mode + an isolated, fresh budget ledger for a deterministic run.
    os.environ["CONDUCTOR_MODE"] = "free"
    tmp = tempfile.mkdtemp(prefix="hmx-rl-")
    os.environ["CONDUCTOR_BUDGET_PATH"] = str(Path(tmp) / "budget.json")
    os.environ["CONDUCTOR_LEDGER_PATH"] = str(Path(tmp) / "ledger.json")
    # Big-ish output so a single Groq call's estimate approaches its per-model TPM,
    # making the 2nd-round pre-flight skip deterministic and cheap (free tier, $0).
    MT = int(os.environ.get("RL_MAX_TOKENS", "4096"))
    ROUNDS = int(os.environ.get("RL_ROUNDS", "3"))

    import conductor_core as cc
    import conductor_registry as reg
    import conductor_resolver as resolver

    env = dict(os.environ)
    cfg = reg.load_config()
    mode = resolver.current_mode(env)
    pool = resolver.resolve_pool(cfg["draft_pool"], cfg["providers"], env)
    pool_label = [f"{e['provider']}:{e['model']}" for e in pool]

    brief = ("Draft a 3-bullet design note: pick a concurrency-safe in-memory rate "
             "limiter for a small Python service. Be concrete and brief.")

    rounds: list[dict] = []
    any_candidate = False
    groq_preflight_skips = 0
    crashes = 0

    for i in range(ROUNDS):
        try:
            r = cc.draft_fanout(prompt=brief, n=5, max_tokens=MT)
        except Exception as e:  # noqa: BLE001 — a raise here is itself a FAIL
            crashes += 1
            rounds.append({"round": i + 1, "CRASH": f"{type(e).__name__}: {e}"})
            continue
        cands = [c for c in r.get("candidates", []) if c.get("ok")]
        errs = [c for c in r.get("candidates", []) if not c.get("ok")]
        skipped = r.get("skipped", [])
        any_candidate = any_candidate or bool(cands)
        for s in skipped:
            why = str(s.get("skipped", ""))
            if s.get("provider") == "groq" and ("tpm" in why or "rpm" in why or "rpd" in why):
                groq_preflight_skips += 1
        # a 429/413 that leaked as a per-candidate ERROR (not a pre-flight skip) is
        # exactly what the tracker must PREVENT — count it.
        leaked = [c for c in errs if "429" in str(c.get("error", "")) or "413" in str(c.get("error", ""))]
        rounds.append({
            "round": i + 1,
            "candidates": [f"{c['provider']}:{c['model']}" for c in cands],
            "preflight_skipped": [f"{s.get('provider')}:{s.get('model')} ({s.get('skipped')})"
                                  for s in skipped],
            "errored": [f"{c['provider']}:{c['model']} ({c.get('error')})" for c in errs],
            "leaked_429_413": [f"{c['provider']}:{c['model']}" for c in leaked],
        })
        crashes += 0  # leaked 429/413 handled below in PASS logic

    leaked_total = sum(len(r.get("leaked_429_413", [])) for r in rounds)

    # One ordered-role call too (the research-cascade distill path): steer must
    # complete by walking its present free chain (cerebras->groq->gemini).
    steer = cc.run_role("steer", prompt="One sentence: why cap input tokens before a free-tier call?",
                        max_tokens=128)
    steer_line = (f"{steer.get('provider')}:{steer.get('model')} ok={steer.get('ok')} "
                  f"fell={len(steer.get('fell', []))}" if steer.get("ok")
                  else f"proceed_local (role_active={steer.get('role_active')})")

    # read the real budget ledger the tracker wrote
    try:
        budget = json.loads(Path(os.environ["CONDUCTOR_BUDGET_PATH"]).read_text())
    except Exception:  # noqa: BLE001
        budget = {}

    falls = [t for t in cc._TRACE if t.get("event") == "rung_fell"]

    ok = bool(any_candidate) and groq_preflight_skips >= 1 and leaked_total == 0 and crashes == 0

    # ── write the human-readable trace artifact ──────────────────────────────
    out = repo / "rate_limit_validation_trace.md"
    L: list[str] = []
    L.append("# Stage-3 rate-limit validation trace (REAL free-tier, $0)\n")
    L.append(f"- **mode**: `{mode}` (CONDUCTOR_MODE=free → only free providers may fire)")
    L.append(f"- **draft pool (resolved, free mode)**: {pool_label}")
    L.append(f"- **max_tokens/round**: {MT} · **rounds**: {ROUNDS}")
    L.append(f"- **verdict**: {'✅ PASS' if ok else '❌ FAIL'}\n")
    L.append("## Per-round fan-out (best-of-N slop-draft)\n")
    for r in rounds:
        L.append(f"### Round {r['round']}")
        if "CRASH" in r:
            L.append(f"- ❌ CRASH: {r['CRASH']}")
            continue
        L.append(f"- real candidates: {r['candidates'] or '—'}")
        L.append(f"- **pre-flight SKIPPED (tracker prevented the call)**: {r['preflight_skipped'] or '—'}")
        if r["errored"]:
            L.append(f"- post-call errors (fell to next): {r['errored']}")
        if r["leaked_429_413"]:
            L.append(f"- ⚠ LEAKED 429/413 (tracker FAILED to prevent): {r['leaked_429_413']}")
        L.append("")
    L.append("## Ordered-role steer (research-cascade distill path)\n")
    L.append(f"- `run_role('steer')` → {steer_line}\n")
    L.append("## Live budget ledger the tracker wrote (per provider:model, 60s/24h windows)\n")
    L.append("```json")
    L.append(json.dumps(budget, indent=2)[:2000])
    L.append("```\n")
    L.append("## Conductor fall/skip trace\n")
    for t in falls[-20:]:
        L.append(f"- {t.get('frm')} → {t.get('to')}: {t.get('reason')}")
    L.append("")
    L.append("## Research-cascade coverage\n")
    L.append("The deep-research cascade's cloud uplift (dense-source distillation, "
             "`mcp-research/corpus.py::_conductor_distill`) calls `conductor_steer` → "
             "`run_role('steer')` — the SAME ordered chain + pre-flight budget gate "
             "exercised above. So the per-provider RPM/RPD/TPM discipline proven here "
             "for the slop-draft fan-out is identically the cascade's rate discipline: "
             "an exhausted free model is pre-flight-skipped and the cascade degrades to "
             "the next free model or to local distill, rather than 429-crashing.\n")
    L.append("## PASS criteria\n")
    L.append(f"- ≥1 real candidate returned: {'✅' if any_candidate else '❌'}")
    L.append(f"- ≥1 Groq rung PRE-FLIGHT skipped on budget (tpm/rpm/rpd): "
             f"{'✅' if groq_preflight_skips >= 1 else '❌'} (count={groq_preflight_skips})")
    L.append(f"- ZERO leaked 429/413 (tracker prevented over-limit calls): "
             f"{'✅' if leaked_total == 0 else '❌'} (leaked={leaked_total})")
    L.append(f"- ZERO crashes (every round returned a dict): {'✅' if crashes == 0 else '❌'}")
    out.write_text("\n".join(L))

    print(f"mode={mode} pool={len(pool_label)} candidates_seen={any_candidate} "
          f"groq_preflight_skips={groq_preflight_skips} leaked_429_413={leaked_total} crashes={crashes}")
    print(f"steer: {steer_line}")
    print(f"trace -> {out}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
