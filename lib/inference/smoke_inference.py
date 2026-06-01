"""Offline smoke for lib/inference — no network, no keys required.

Validates the fabric's resolution logic end-to-end with a stubbed wire adapter:
  • zero keys → only local_vllm present; non-local rungs skip
  • a present key (GROQ) makes that provider resolvable
  • modes reassign coding chains (free: local executes; full: V4-Flash executes)
  • the spend ceiling filters rungs by tier (local mode admits only local)
  • run_role never raises; total exhaustion → proceed_local
  • ledger formats $0.000000 and records free token volume at $0
  • buckets gate on rpd exhaustion

Run: python3 -m lib.inference.smoke_inference   (from repo root)
"""
from __future__ import annotations

import os
import sys
import tempfile

# isolate ledger/buckets/mode state to a temp dir so the smoke is hermetic
_TMP = tempfile.mkdtemp(prefix="inf-smoke-")
os.environ["INFERENCE_LEDGER_PATH"] = os.path.join(_TMP, "ledger.jsonl")
os.environ["INFERENCE_BUCKETS_PATH"] = os.path.join(_TMP, "buckets.json")
os.environ["HERMES_MODE_FILE"] = os.path.join(_TMP, "mode")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.inference import (  # noqa: E402
    config, roles, router, ledger, buckets,
)

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name}  {detail}")


def stub_ok(kind, base_url, api_key, model, messages, **kw):
    return {"ok": True, "text": f"[{model}] ok", "in_tok": 100, "out_tok": 50,
            "cached_tok": 0, "status": 200, "headers": {}, "error": None}


def stub_fail(kind, base_url, api_key, model, messages, **kw):
    return {"ok": False, "text": "", "in_tok": 0, "out_tok": 0, "cached_tok": 0,
            "status": 500, "headers": {}, "error": "stub forced failure"}


def main() -> int:
    print("═══ lib/inference offline smoke ═══")

    # ── 1. presence gating ──────────────────────────────────────────────────
    zero_env: dict[str, str] = {}
    present = config.present_providers(zero_env)
    check("zero keys → only local_vllm present", present == {"local_vllm"},
          f"got {present}")

    groq_env = {"GROQ_API_KEY": "x"}
    check("GROQ key → groq present", "groq" in config.present_providers(groq_env))
    check("GROQ key → deepseek_direct still absent",
          "deepseek_direct" not in config.present_providers(groq_env))

    # ── 2. tiers + ceiling ──────────────────────────────────────────────────
    check("local_vllm tier == local", config.tier("local_vllm") == "local")
    check("groq tier == free", config.tier("groq") == "free")
    check("deepseek_direct tier == paid", config.tier("deepseek_direct") == "paid")
    check("anthropic tier == frontier", config.tier("anthropic") == "frontier")

    # ── 3. modes reassign coding chains ─────────────────────────────────────
    free_exec = roles.chain_for("code_execute", "free")
    check("free mode: code_execute == local only",
          free_exec == [("local_vllm", "driver")], f"got {free_exec}")
    full_exec = roles.chain_for("code_execute", "full")
    check("full mode: code_execute == V4-Flash → local",
          full_exec == [("deepseek_direct", "driver"), ("local_vllm", "driver")],
          f"got {full_exec}")
    free_plan = roles.chain_for("code_plan", "free")
    check("free mode: code_plan starts with Kimi (openrouter.synth_free)",
          free_plan and free_plan[0] == ("openrouter", "synth_free"), f"got {free_plan}")
    fl_plan = roles.chain_for("code_plan", "full-local")
    check("full-local: code_plan starts with V4-Pro",
          fl_plan and fl_plan[0] == ("deepseek_direct", "planner"), f"got {fl_plan}")

    # Kimi id correction propagated through inference.yaml
    kimi = config.resolve_model("openrouter", "synth_free") or {}
    check("Kimi id == moonshotai/kimi-k2.6:free",
          kimi.get("id") == "moonshotai/kimi-k2.6:free", f"got {kimi.get('id')}")

    # ── 4. ceiling enforcement via run_role ─────────────────────────────────
    router.set_caller(stub_ok)
    try:
        # full mode + only GROQ present: code_execute V4-Flash absent (no key) →
        # local_vllm is present(keyless) and under ceiling → resolves to local.
        r = router.run_role("code_execute", [{"role": "user", "content": "hi"}],
                            mode="full", env=groq_env)
        check("full+GROQ: code_execute resolves to local_vllm",
              r["ok"] and r["provider"] == "local_vllm", f"got {r.get('provider')}")

        # free mode, GROQ present: code_steer first rung groq.fast_mid resolves.
        r = router.run_role("code_steer", [{"role": "user", "content": "hi"}],
                            mode="free", env=groq_env)
        check("free+GROQ: code_steer resolves to groq",
              r["ok"] and r["provider"] == "groq", f"got {r.get('provider')}")

        # local mode admits only local tier: a groq-first role must skip groq.
        r = router.run_role("code_steer", [{"role": "user", "content": "hi"}],
                            mode="local", env=groq_env)
        check("local mode: groq skipped (above ceiling), resolves local",
              r["ok"] and r["provider"] == "local_vllm",
              f"got {r.get('provider')} fell={r.get('fell')}")
    finally:
        router.set_caller(None)

    # ── 5. never raises; exhaustion → proceed_local ─────────────────────────
    router.set_caller(stub_fail)
    try:
        r = router.run_role("code_plan", [{"role": "user", "content": "hi"}],
                            mode="full-local", env={"DEEPSEEK_API_KEY": "x",
                                                    "OPENROUTER_API_KEY": "x"})
        check("all rungs fail → proceed_local, ok False, no raise",
              (not r["ok"]) and r["proceed_local"] is True)
    finally:
        router.set_caller(None)

    # ── 6. ledger format + free volume ──────────────────────────────────────
    check("fmt_usd(0) == $0.000000", ledger.fmt_usd(0) == "$0.000000")
    check("fmt_usd(0.0000125) == $0.000013 (6dp)",
          ledger.fmt_usd(0.0000125) == "$0.000013", ledger.fmt_usd(0.0000125))
    rep = ledger.report("all")
    check("ledger recorded free token volume at $0",
          rep["free_tok"] > 0 and rep["total_usd"] == 0.0,
          f"free_tok={rep['free_tok']} usd={rep['total_usd']}")

    # ── 7. cost math (paid) ─────────────────────────────────────────────────
    # V4-Pro: 1M in @ $0.435, 1M out @ $0.87 → $1.305
    usd = config.cost_usd("deepseek_direct", "planner", 1_000_000, 1_000_000, 0)
    check("V4-Pro 1M+1M tok == $1.305000", abs(usd - 1.305) < 1e-9, f"got {usd}")
    # V4-Flash driver uses cost_flash: 1M in @ $0.14
    usdf = config.cost_usd("deepseek_direct", "driver", 1_000_000, 0, 0)
    check("V4-Flash 1M in == $0.140000", abs(usdf - 0.14) < 1e-9, f"got {usdf}")

    # ── 8. buckets gate on rpd exhaustion ───────────────────────────────────
    # llama-3.1-8b-instant rpd=14400; simulate exhaustion via direct state poke.
    import json
    bstate = {"groq:llama-3.1-8b-instant": {
        "req_d": [9e9] * 14400, "req_m": [], "tok_m": []}}
    # use a far-future ts so prune keeps them; patch _now via monkeypatch
    orig_now = buckets._now
    buckets._now = lambda: 9e9  # type: ignore
    try:
        with open(os.environ["INFERENCE_BUCKETS_PATH"], "w") as f:
            json.dump(bstate, f)
        check("groq fast_filter at rpd cap → no headroom",
              not buckets.has_headroom("groq", "fast_filter", 100))
        check("groq fast_mid (separate bucket) → still has headroom",
              buckets.has_headroom("groq", "fast_mid", 100))
    finally:
        buckets._now = orig_now  # type: ignore

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
