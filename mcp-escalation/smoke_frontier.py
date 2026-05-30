"""smoke_frontier.py — the frontier Opus tier: three gates, compress-then-reason,
USD cap, plan-to-artifact. No real Anthropic key needed: the model calls are
stubbed at the _post_chat seam (presence of a dummy key is all the gates check).
"""
import json
import os
import sys
import tempfile

WORK = tempfile.mkdtemp(prefix="frontier-smoke-")
# Isolate ALL state files BEFORE importing the modules that read them at import.
os.environ["CONDUCTOR_LEDGER_PATH"] = os.path.join(WORK, "ledger.json")
os.environ["CONDUCTOR_BUDGET_PATH"] = os.path.join(WORK, "budget.json")
os.environ["FRONTIER_STATE_PATH"] = os.path.join(WORK, "frontier.json")
os.environ["FRONTIER_PLAN_DIR"] = WORK
os.environ["DEEPINFRA_API_KEY"] = "di-test"      # so the V4-Pro compress rung resolves
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"  # presence-only gate

import conductor_core as cc
import frontier_core as fc

fails = 0
def ok(m): print(f"  ok: {m}")
def bad(m):
    global fails; fails += 1; print(f"  FAIL: {m}")

# Stub the single network seam: V4-Pro (deepinfra) returns a compressed brief;
# Opus (anthropic) returns a JSON frontier plan. Usage ~ the spec's ~12K in / 4K out.
def fake_post_chat(base_url, api_key, model, messages, max_tokens):
    if "anthropic" in base_url:
        plan = {"approach": "novel lock-free ring buffer", "steps": ["s1", "s2"],
                "assumptions": ["module foo exists"], "apis_to_use": ["foo.bar"],
                "tests_to_write": ["test_ring_wraps"]}
        return ({"choices": [{"message": {"content": json.dumps(plan)}}],
                 "usage": {"prompt_tokens": 12000, "completion_tokens": 4000}}, {})
    return ({"choices": [{"message": {"content": "DISTILLED BRIEF: problem X, tried A/B, decide C."}}],
             "usage": {"prompt_tokens": 9000, "completion_tokens": 1500}}, {})
cc._post_chat = fake_post_chat
# Don't touch live RAG/KG in a smoke test — record the ingest intent only.
_ingests = []
fc._mcp = lambda port, tool, args: (_ingests.append((tool, args)) or {"ok": True})

NOVEL = {"novelty": "high", "blue_ocean": True, "file_count": 6, "cross_module": True}
HARD = {"novelty": "high", "file_count": 9, "prior_failures": 2}  # hard but NOT blue-ocean

print("[A] classify_frontier")
if fc.classify_frontier(NOVEL)["frontier_novel"] and not fc.classify_frontier(HARD)["frontier_novel"]:
    ok("frontier-novel (novelty=high + blue_ocean) YES; merely-hard NO")
else:
    bad("classify_frontier mis-classified")

print("[B] GATE 1 — mode")
os.environ["CONDUCTOR_MODE"] = "full"
r = fc.frontier_escalate("solve X", signals=NOVEL, synth_failures=2)
if not r["opus_invoked"] and r["gate_failed"] == "mode":
    ok("full mode -> Opus NOT invoked (gate=mode), routes to v4-pro-synth")
else:
    bad(f"mode gate: {r}")

print("[C] GATE 1 — key absent -> fall back to --full + warn")
os.environ["CONDUCTOR_MODE"] = "frontier"
saved = os.environ.pop("ANTHROPIC_API_KEY")
r = fc.frontier_escalate("solve X", signals=NOVEL, synth_failures=2)
if not r["opus_invoked"] and r["gate_failed"] == "key" and r.get("fell_back_to") == "full":
    ok("frontier + no key -> Opus OFF, falls back to --full with a clear warning")
else:
    bad(f"key gate: {r}")
os.environ["ANTHROPIC_API_KEY"] = saved

print("[D] GATE 2 — merely-HARD does NOT reach Opus")
r = fc.frontier_escalate("refactor module", signals=HARD, synth_failures=2)
if not r["opus_invoked"] and r["gate_failed"] == "difficulty":
    ok("merely-HARD -> gate=difficulty, stays at V4-Pro (Opus NOT invoked)")
else:
    bad(f"difficulty gate: {r}")

print("[E] GATE 3 — frontier-novel but NOT twice-failed does NOT reach Opus")
r = fc.frontier_escalate("solve X", signals=NOVEL, synth_failures=1, opinions_disagree=False)
if not r["opus_invoked"] and r["gate_failed"] == "failure":
    ok("frontier-novel + only 1 synth failure -> gate=failure (Opus is last-resort)")
else:
    bad(f"failure gate: {r}")

print("[F] ALL THREE GATES TRIP -> compress-then-reason -> Opus -> artifact")
r = fc.frontier_escalate("solve X (blue-ocean)", signals=NOVEL, synth_failures=2, repo=WORK)
if r["opus_invoked"] and r["model"] == "claude-opus-4-8":
    ok(f"Opus invoked: model={r['model']}, cost=${r['cost_usd']} (compress brief ~{r['compress'].get('brief_tokens_est')} tok)")
    # cost ~ 12K*5/M + 4K*25/M = $0.16
    if 0.10 <= r["cost_usd"] <= 0.30:
        ok(f"per-call cost ${r['cost_usd']} in the ~$0.18 band")
    else:
        bad(f"unexpected cost ${r['cost_usd']}")
    if r["compress"].get("model") and "DeepSeek-V4-Pro" in str(r["compress"]["model"]):
        ok("compress-then-reason: V4-Pro wrote the brief, Opus reasoned on it")
    else:
        bad(f"compress step did not use V4-Pro: {r['compress']}")
    plan_path = r["artifact"].get("plan_path")
    if plan_path and os.path.isfile(plan_path) and "claude-opus-4-8" in open(plan_path).read():
        ok(f"FRONTIER_PLAN.md written with provenance ({os.path.basename(plan_path)})")
    else:
        bad("FRONTIER_PLAN.md not written / missing provenance")
    if r["artifact"].get("rag_stored") and r["artifact"].get("kg_recorded"):
        ok("plan ingested into RAG + KG (provenance source=opus-4.8)")
    else:
        bad(f"RAG/KG ingest not recorded: {r['artifact']}")
    if "execute" in r["directive_verify"]:
        ok("Opus directive passed THROUGH directive_verify (advisory, not trusted-blind)")
    else:
        bad("directive_verify not run")
else:
    bad(f"all-gates-trip did not invoke Opus: {r}")

print("[G] USD CAP — cap hit -> NO Opus, fall back to V4-Pro")
orig_cap = fc.FRONTIER_USD_CAP_MONTHLY
fc.FRONTIER_USD_CAP_MONTHLY = 0.0001  # force the monthly cap
r = fc.frontier_escalate("solve Y (blue-ocean)", signals=NOVEL, synth_failures=2, repo=WORK)
fc.FRONTIER_USD_CAP_MONTHLY = orig_cap
if not r["opus_invoked"] and r.get("capped") and r["route"] == "v4-pro-synth-fallback":
    ok("frontier cap reached -> Opus blocked, fell back to V4-Pro synth (logged)")
else:
    bad(f"cap test: {r}")

print("[H] sparing accounting (frontier_status)")
st = fc.frontier_status()
if st["calls_month"] >= 1 and st["target_calls_monthly"] == 15:
    ok(f"frontier_status: {st['calls_vs_target']} calls vs target, spend_month=${st['spend_month_usd']}")
else:
    bad(f"frontier_status: {st}")

print()
if fails == 0:
    print("frontier smoke test PASSED")
    sys.exit(0)
print(f"frontier smoke test FAILED ({fails})")
sys.exit(1)
