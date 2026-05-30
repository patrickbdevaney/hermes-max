#!/usr/bin/env python3
"""Standalone smoke test for the invocation policy (Stage 5). Offline: KG pointed
at a dead port (record/report degrade). Asserts the Stage-5 DoD:

  • routine subtasks stay LOCAL (no cloud)
  • verifiable+hard -> parallel_draft (when the pool is active)
  • ambiguous+hard -> steer (when active) else synthesize
  • frontier-novel/synth-failed -> escalate ONLY when the Opus gate is met AND a
    key is present; otherwise it degrades cleanly (here: no Opus key -> not chosen)
  • presence-gating skips inactive roles (no keys -> everything degrades to local)
  • frequency report returns honest numbers + target checks
"""

from __future__ import annotations

import os
import sys

# KG dead -> record/report degrade; isolate from any sourced .env keys.
os.environ["MCP_KG_PORT"] = "59103"
os.environ["BRIEF_MCP_TIMEOUT"] = "2"
for _k in ("DEEPINFRA_API_KEY", "FIREWORKS_API_KEY", "TOGETHER_API_KEY", "DEEPSEEK_API_KEY",
           "MOONSHOT_API_KEY", "CEREBRAS_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
           "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)

import conductor_policy as cp  # noqa: E402


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _fail(m: str) -> None:
    print(f"  FAIL: {m}")
    sys.exit(1)


EASY = {"file_count": 1}
HARD = {"file_count": 10, "prior_failures": 2, "novelty": "high", "cross_module": True}


def main() -> None:
    # 1. routine -> local regardless of keys
    p = cp.plan_invocation(EASY, verifiable=True)
    if p["tier"] != "local" or p["difficulty"] != "easy":
        _fail(f"routine must stay local: {p}")
    _ok("routine (easy) -> LOCAL, no cloud")

    # 2. with ALL keys present: verifiable+hard -> parallel_draft
    os.environ["DEEPINFRA_API_KEY"] = "x"
    os.environ["CEREBRAS_API_KEY"] = "x"
    os.environ["GROQ_API_KEY"] = "x"
    pv = cp.plan_invocation(HARD, verifiable=True)
    if pv["tier"] != "parallel_draft":
        _fail(f"verifiable+hard should be parallel_draft: {pv}")
    _ok(f"verifiable+hard -> {pv['tier']} (next_if_fail={pv['next_if_fail']})")

    # 3. ambiguous+hard -> steer (steer active via DeepInfra V4-Flash)
    pa = cp.plan_invocation(HARD, verifiable=False)
    if pa["tier"] != "steer" or pa["next_if_fail"] != "synthesize":
        _fail(f"ambiguous+hard should be steer->synthesize: {pa}")
    _ok(f"ambiguous+hard -> {pa['tier']} -> {pa['next_if_fail']}")

    # 4. Opus gate: not met -> escalate skipped; met but no key -> degrades (not escalate)
    pno = cp.plan_invocation(HARD, verifiable=False, synth_failures=1)
    if pno["opus_allowed"]:
        _fail(f"one synth failure must NOT open the Opus gate: {pno}")
    pmet = cp.plan_invocation(HARD, verifiable=False, synth_failures=2)
    if not pmet["opus_allowed"]:
        _fail(f"two synth failures should open the Opus gate: {pmet}")
    if pmet["tier"] == "escalate":
        _fail(f"no Opus key present -> must NOT route to escalate: {pmet}")
    _ok(f"Opus gate: 1 fail->closed; 2 fails->open but no key -> tier={pmet['tier']} (degraded)")

    # 4b. Opus gate met AND key present AND --frontier mode -> escalate. (Opus is the
    # FRONTIER tier now: a key alone in --full keeps escalate OFF; --frontier is required.)
    os.environ["ANTHROPIC_API_KEY"] = "x"
    pfull = cp.plan_invocation(HARD, verifiable=False, synth_failures=2)
    if pfull["tier"] == "escalate":
        _fail(f"key present but mode=full must NOT route to escalate (Opus is frontier-only): {pfull}")
    _ok("gate met + key but mode=full -> escalate OFF (degraded) — Opus stays frontier-gated")
    os.environ["CONDUCTOR_MODE"] = "frontier"
    pop = cp.plan_invocation(HARD, verifiable=False, synth_failures=2)
    if pop["tier"] != "escalate":
        _fail(f"gate met + key + --frontier -> escalate: {pop}")
    _ok("Opus gate met + key present + --frontier mode -> tier=escalate")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("CONDUCTOR_MODE", None)

    # 4c. opinions disagree on high blast also opens the gate
    pdis = cp.plan_invocation(HARD, verifiable=False, opinions_disagree=True, blast_radius="high")
    if not pdis["opus_allowed"]:
        _fail(f"high-blast disagreement should open the Opus gate: {pdis}")
    _ok("two opinions disagree on high blast -> Opus gate open")

    # 5. presence-gating: NO keys -> every hard rung degrades to local
    for k in ("DEEPINFRA_API_KEY", "CEREBRAS_API_KEY", "GROQ_API_KEY"):
        os.environ.pop(k, None)
    poff = cp.plan_invocation(HARD, verifiable=True)
    poff2 = cp.plan_invocation(HARD, verifiable=False)
    if poff["tier"] != "local" or poff2["tier"] != "local":
        _fail(f"no keys -> all hard rungs degrade to local: {poff['tier']}/{poff2['tier']}")
    if any(poff["roles_active"].values()):
        _fail(f"no keys -> no role active: {poff['roles_active']}")
    _ok(f"presence-gating: no keys -> verifiable+ambiguous both degrade to LOCAL "
        f"(roles_active={poff['roles_active']})")

    # 6. record + frequency report degrade cleanly with KG down
    rec = cp.record_conductor_outcome("implement add()", "parallel_draft", "verified",
                                      signals=HARD, cost_usd=0.0)
    if not rec["ok"]:
        _fail(f"record should not raise even with KG down: {rec}")
    rep = cp.frequency_report()
    if not rep["ok"] or "targets" not in rep:
        _fail(f"frequency report shape wrong: {rep}")
    _ok(f"record+report degrade cleanly (kg_available={rep['kg_available']}, "
        f"targets={rep['targets']}, warnings={rep['warnings']})")


if __name__ == "__main__":
    main()
    print("conductor policy smoke test PASSED")
