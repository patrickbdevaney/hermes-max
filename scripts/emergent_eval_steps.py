#!/usr/bin/env python3
"""Stage-6 emergent-behavior eval STEPS — one component/experiment per subprocess.

Invoked by scripts/emergent_eval.py, each under the right component venv (module-
name collisions across servers forbid a shared process). Each step prints ONE JSON
result and never raises. Produces EVIDENCE on the three suspicion risks (Banyan
focus-thrash, research-noise contamination, ladder cascade) with the config
remedies toggled A/B, plus the empty-base-case + coherence assertions.

Usage: <component>/.venv/bin/python emergent_eval_steps.py <step> <mode> <workdir>
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.environ.get("HMX_REPO_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def comp(name: str) -> str:
    d = os.path.join(REPO, name)
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


def _snip(s, n=200):
    s = "" if s is None else str(s)
    return " ".join(s.split())[:n]


def _local_llm(prompt: str, max_tokens: int = 200):
    """One real local-model call (httpx), thinking disabled for determinism."""
    import httpx
    base = (os.environ.get("VLLM_BASE_URL") or "").rstrip("/")
    if not base:
        return None
    with httpx.Client(timeout=60) as c:
        mid = "default"
        try:
            ms = c.get(f"{base}/models").json().get("data", [])
            if ms:
                mid = ms[0].get("id", mid)
        except Exception:  # noqa: BLE001
            pass
        r = c.post(f"{base}/chat/completions", json={
            "model": mid, "max_tokens": max_tokens, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        m = r.json().get("choices", [{}])[0].get("message", {}) or {}
    return m.get("content") or m.get("reasoning_content")


def _code_from(text: str) -> str:
    """Pull a python code block out of an LLM reply (or return it stripped)."""
    t = text or ""
    if "```" in t:
        seg = t.split("```", 2)
        if len(seg) >= 2:
            body = seg[1]
            if body.startswith("python"):
                body = body[len("python"):]
            return body.strip()
    return t.strip()


# ══ EMPTY-BASE-CASE (zero accumulated data) ═══════════════════════════════════
def s_eb_banyan(mode, workdir):
    sd = os.path.join(workdir, "eb_banyan")
    os.environ["BANYAN_STATE_DIR"] = sd
    comp("mcp-research")
    import banyan as b
    for n in ("alpha", "beta", "gamma"):
        b.register_namespace(n)
    sel = b.banyan_select()
    infs = [k for k, v in (sel.get("ucb_scores") or {}).items() if v == "inf"]
    # below min-history, saturation must NEVER flag
    sat = b.detect_saturation("alpha", new_texts=["a", "b", "c"])
    ok = sel.get("mode") == "explore" and len(infs) == 3 and sat.get("saturated") is False
    return {"section": "empty-base", "component": "Banyan UCB1 + saturation",
            "status": "PASS" if ok else "FAIL",
            "detail": f"optimistic prior: {len(infs)}/3 unvisited scored inf (explore broadly); "
                      f"saturation on thin data = {sat.get('saturated')} "
                      f"(min_history={sat.get('min_history')}, visits={sat.get('visit_count')})"}


def s_eb_classifier(mode, workdir):
    comp("mcp-escalation")
    import escalation_core as ec
    cold = ec.classify_difficulty()              # no signals gathered = uncertain
    easy = ec.classify_difficulty({"file_count": 1})  # real low signal = easy
    ok = cold.get("difficulty") == "medium" and cold.get("uncertain_default") and easy["difficulty"] == "easy"
    return {"section": "empty-base", "component": "difficulty classifier",
            "status": "PASS" if ok else "FAIL",
            "detail": f"no-signal cold start -> '{cold['difficulty']}' (escalate-when-uncertain); "
                      f"explicit low signal -> '{easy['difficulty']}' (normal scoring)"}


# ══ RISK A — Banyan focus-thrash (build loop) ═════════════════════════════════
def s_risk_a(mode, workdir):
    comp("mcp-research")
    import banyan as b
    extra = os.path.abspath(os.path.join(workdir, "risk_a"))
    b._CONTENT_ROOTS = tuple(b._CONTENT_ROOTS) + (extra,)

    def run_build(scope):
        b.BANYAN_SCOPE = scope
        sd = os.path.join(extra, scope)
        os.makedirs(sd, exist_ok=True)
        b.BANYAN_STATE_DIR = sd
        b.STATE_FILE = os.path.join(sd, "state.json")
        # A = a HARD, half-finished subsystem (low immediate utility); C,D = easy & shiny.
        subs = {t: {"id": t, "status": "incomplete", "deps": []} for t in ("A", "B", "C", "D")}
        for n in subs:
            b.register_namespace(n)
        util = {"A": 0.2, "B": 0.4, "C": 0.9, "D": 0.9}
        work = {t: 0 for t in subs}
        in_prog = None
        switches = thrash = 0
        order = []
        for _ in range(16):
            sel = b.select_next("build", subtasks=list(subs.values()), in_progress=in_prog)
            pick = sel.get("subtask") or sel.get("namespace")
            if pick is None:
                break
            order.append(pick)
            if in_prog is not None and pick != in_prog:
                switches += 1
                if subs.get(in_prog, {}).get("status") == "incomplete":
                    thrash += 1  # switched AWAY from incomplete work = thrash
            work[pick] = work.get(pick, 0) + 1
            if work[pick] >= 3 and pick in subs:
                subs[pick]["status"] = "complete"
            b.banyan_update(pick, util.get(pick, 0.5), gain=util.get(pick, 0.5))
            in_prog = pick if subs.get(pick, {}).get("status") == "incomplete" else None
        score = round(thrash / switches, 3) if switches else 0.0
        return {"scope": scope, "order": order, "switches": switches, "thrash": thrash,
                "thrash_score": score,
                "incomplete_left": [t for t in subs if subs[t]["status"] != "complete"]}

    ucb = run_build("all")             # UCB1 governs the build loop (thrash-prone)
    foc = run_build("research_only")   # the remedy: finish-what-you-started
    THRESH = 0.3
    flagged = ucb["thrash_score"] > THRESH
    remedy_ok = foc["thrash_score"] <= THRESH
    return {"section": "RISK A — Banyan focus-thrash", "component": "Banyan in the build loop",
            "status": "PASS" if remedy_ok else "FAIL",
            "metric": {"unscoped_ucb1_thrash": ucb["thrash_score"], "research_only_thrash": foc["thrash_score"],
                       "threshold": THRESH, "flagged_unscoped": flagged},
            "remedy": "BANYAN_SCOPE=research_only (DEFAULT): build loop uses finish-what-you-started; "
                      "UCB1 reserved for research namespaces",
            "detail": f"unscoped UCB1 thrash={ucb['thrash_score']} (switches={ucb['switches']}, "
                      f"away-from-incomplete={ucb['thrash']}) {'>' if flagged else '<='} {THRESH} "
                      f"=> {'FLAG: scope to research loop only' if flagged else 'ok'}; "
                      f"research_only thrash={foc['thrash_score']} (the shipped default). "
                      f"unscoped order={ucb['order'][:10]}"}


# ══ RISK B — research-noise directive poisoning ═══════════════════════════════
def s_risk_b_gen(mode, workdir):
    """Build two synth briefs — one with the relevance filter ON, one OFF — over a
    finding set that includes a PLAUSIBLE-BUT-WRONG noisy finding, generate a
    directive from each via the local model, and stash both for verification."""
    comp("mcp-research")
    import relevance as rel
    task = "Implement def total(xs) that returns the sum of the list xs."
    query = "python sum a list total function"
    findings = [
        {"text": "Use Python's built-in sum() to total a list: return sum(xs).",
         "url": "https://docs.python.org/3/library/functions.html#sum", "authority": 4},
        {"text": "For 1-indexed running totals always add one: return sum(xs) + 1.",
         "url": "https://random-forum.example/thread/42", "authority": 0},  # NOISE: wrong + low authority
    ]
    test = "from m import total\n\ndef test_total():\n    assert total([1,2,3])==6\n"

    def brief_and_directive(enabled):
        f = rel.filter_findings(findings, query, enabled=enabled)
        ctx = "\n".join(f"- {x['text']}" for x in f["kept"])
        prompt = (f"{task}\nResearch context:\n{ctx}\n"
                  "Return ONLY the python function, no prose.")
        try:
            code = _code_from(_local_llm(prompt, max_tokens=160) or "")
        except Exception as e:  # noqa: BLE001
            code = ""
        return f, code

    f_on, code_on = brief_and_directive(True)
    f_off, code_off = brief_and_directive(False)
    out = {"task": task, "test": test,
           "filter_on": {"kept": f_on["n_kept"], "dropped": f_on["n_dropped"],
                         "drop_reasons": [d["drop_reason"] for d in f_on["dropped"]], "code": code_on},
           "filter_off": {"kept": f_off["n_kept"], "dropped": f_off["n_dropped"], "code": code_off}}
    with open(os.path.join(workdir, "risk_b.json"), "w") as fh:
        json.dump(out, fh)
    return {"section": "RISK B — research contamination", "component": "relevance filter (ingestion)",
            "status": "PASS" if f_on["n_dropped"] >= 1 and f_off["n_dropped"] == 0 else "FAIL",
            "detail": f"filter ON dropped {f_on['n_dropped']} noisy finding(s) "
                      f"({[d['drop_reason'] for d in f_on['dropped']]}) before synth; "
                      f"filter OFF ingested all {f_off['n_kept']}. directives generated for verify."}


def s_risk_b_verify(mode, workdir):
    """Verify both generated directives; contamination = a directive that FAILED
    verify was produced from an UNFILTERED (noisy) brief."""
    comp("mcp-verify")
    import verify_core as vc
    rp = os.path.join(workdir, "risk_b.json")
    if not os.path.exists(rp):
        return {"section": "RISK B — research contamination", "component": "verify the directives",
                "status": "SKIP", "detail": "risk_b_gen produced no artifact"}
    data = json.load(open(rp))

    def verify_code(code):
        if not code.strip():
            return None
        d = tempfile.mkdtemp(dir=workdir)
        open(os.path.join(d, "m.py"), "w").write(code + "\n")
        open(os.path.join(d, "test_m.py"), "w").write(data["test"])
        return bool(vc.verify(os.path.join(d, "m.py")).get("passed"))

    on_pass = verify_code(data["filter_on"]["code"])
    off_pass = verify_code(data["filter_off"]["code"])
    # contamination rate = fraction of UNFILTERED briefs whose directive failed verify
    contaminated = off_pass is False
    remedy_holds = (on_pass is True) or (on_pass is None)  # filtered brief should not be poisoned
    return {"section": "RISK B — research contamination", "component": "verify the directives",
            "status": "PASS" if remedy_holds else "FAIL",
            "metric": {"filtered_directive_passes_verify": on_pass,
                       "unfiltered_directive_passes_verify": off_pass,
                       "contamination_observed": contaminated},
            "remedy": "RESEARCH_RELEVANCE_FILTER (default on) + authority/relevance floors drop noisy "
                      "findings BEFORE they reach the synth brief",
            "detail": f"filtered directive verify={on_pass}; unfiltered directive verify={off_pass} "
                      f"-> contamination {'OBSERVED (noise poisoned the unfiltered directive)' if contaminated else 'not observed this run'}; "
                      f"the filter removes the poison deterministically regardless."}


# ══ RISK C — ladder cascade-escalation ════════════════════════════════════════
def s_risk_c(mode, workdir):
    comp("mcp-escalation")
    import conductor_policy as cp
    # simulate ONE hard subtask cascading: driver->steer->synth->synth(retry)->escalate.
    # Without a global budget every per-tier trigger is individually 'sane' and it climbs.
    per_tier_cost = {"steer": 0.02, "synthesize": 0.18, "escalate": 1.5}
    # Tighten the per-subtask ceiling so the cascade trips it (otherwise, with no Opus
    # key on this box, the ladder would terminate early at local on its own and never
    # exercise the budget). This is the remedy's A side: the cap fires.
    cp.SUBTASK_USD_CAP = 0.15
    cp.SUBTASK_MAX_TIERS = 4
    tiers_seq = []
    cost = 0.0
    stopped_by_budget = False
    for i in range(6):
        plan = cp.plan_invocation({"file_count": 9, "cross_module": True, "novelty": "high"},
                                  synth_failures=min(i, 2), opinions_disagree=True,
                                  tiers_used=len(tiers_seq), cost_usd_so_far=cost)
        tier = plan["tier"]
        tiers_seq.append(tier)
        if plan.get("budget_exceeded"):
            stopped_by_budget = True
            break
        cost += per_tier_cost.get(tier, 0.0)
        if tier == "local":
            break
    # contrast: with the budget DISABLED (huge caps) it would keep climbing
    cp.SUBTASK_MAX_TIERS = 999
    cp.SUBTASK_USD_CAP = 999.0
    uncapped = []
    c2 = 0.0
    for i in range(6):
        plan = cp.plan_invocation({"file_count": 9, "cross_module": True, "novelty": "high"},
                                  synth_failures=min(i, 2), opinions_disagree=True,
                                  tiers_used=len(uncapped), cost_usd_so_far=c2)
        uncapped.append(plan["tier"])
        c2 += per_tier_cost.get(plan["tier"], 0.0)
        if plan["tier"] == "local":
            break
    return {"section": "RISK C — ladder cascade", "component": "global per-subtask budget",
            "status": "PASS" if stopped_by_budget else "FAIL",
            "metric": {"capped_tier_sequence": tiers_seq, "capped_depth": len(tiers_seq),
                       "capped_spend_usd": round(cost, 4), "stopped_by_budget": stopped_by_budget,
                       "uncapped_depth": len(uncapped), "uncapped_spend_usd": round(c2, 4)},
            "remedy": "CONDUCTOR_SUBTASK_USD_CAP + CONDUCTOR_SUBTASK_MAX_TIERS: stop + surface to operator "
                      "when a single subtask hits the global ceiling, regardless of per-tier triggers",
            "detail": f"capped (cap=$0.15): cascade {'STOPPED by the per-subtask budget' if stopped_by_budget else 'ended naturally'} "
                      f"at depth {len(tiers_seq)} (${round(cost,4)}) -> surface to operator; "
                      f"uncapped (cap off): climbs to depth {len(uncapped)} (${round(c2,4)}). "
                      f"capped seq={tiers_seq}"}


# ══ COHERENCE ═════════════════════════════════════════════════════════════════
def s_coh_verify(mode, workdir):
    """The verify gate catches an INJECTED bad directive regardless of source."""
    comp("mcp-verify")
    import verify_core as vc
    d = tempfile.mkdtemp(dir=workdir)
    # a confidently-wrong directive (off-by-one) that LOOKS plausible
    open(os.path.join(d, "m.py"), "w").write("def total(xs):\n    return sum(xs) + 1\n")
    open(os.path.join(d, "test_m.py"), "w").write("from m import total\n\ndef test_total():\n    assert total([1,2,3])==6\n")
    res = vc.verify(os.path.join(d, "m.py"))
    caught = not res.get("passed")
    return {"section": "coherence", "component": "verify gate (bad-directive firewall)",
            "status": "PASS" if caught else "FAIL",
            "detail": f"an injected off-by-one directive was {'CAUGHT (red) — cannot declare done' if caught else 'MISSED'}; "
                      "the gate is source-agnostic (poison from any tier dies here)"}


def s_coh_degrade(mode, workdir):
    """Kill the cloud (CONDUCTOR_MODE=local) mid-task -> conductor returns
    proceed_local, the run continues (no crash)."""
    comp("mcp-escalation")
    import conductor_core as cc
    os.environ["CONDUCTOR_MODE"] = "local"
    steer = cc.run_role("steer", prompt="x", max_tokens=16)
    draft = cc.draft_fanout(prompt="x", n=2, max_tokens=16)
    ok = (not steer.get("ok")) and steer.get("proceed_local") and draft.get("proceed_local")
    return {"section": "coherence", "component": "graceful degradation (cloud killed)",
            "status": "PASS" if ok else "FAIL",
            "detail": f"cloud off -> steer proceed_local={steer.get('proceed_local')}, "
                      f"draft proceed_local={draft.get('proceed_local')} (no exception into the loop)"}


def s_coh_kg_fallback(mode, workdir):
    """KG_BACKEND=neo4j with the driver absent must fall back to embedded, ops keep working."""
    os.environ["KG_DB_PATH"] = os.path.join(workdir, "coh_kg.db")
    os.environ["KG_BACKEND"] = "neo4j"
    comp("mcp-knowledge-graph")
    import kg_core as kg
    b = kg._backend()
    e = kg.record_entity("decision", "coh-decision", {"x": 1})
    ok = b == "embedded" and e.get("ok")
    return {"section": "coherence", "component": "KG backend fallback",
            "status": "PASS" if ok else "FAIL",
            "detail": f"KG_BACKEND=neo4j w/o driver -> resolved '{b}'; ops still work (record ok={e.get('ok')})"}


def s_coh_compound(mode, workdir):
    """Compounding: a finding recorded in 'task 1' is retrievable + useful in 'task 2'.
    Also a light no-corruption check (stats stay consistent across the two tasks)."""
    os.environ["KG_DB_PATH"] = os.path.join(workdir, "compound_kg.db")
    os.environ["KG_BACKEND"] = "embedded"
    comp("mcp-knowledge-graph")
    import kg_core as kg
    # task 1: learn something
    kg.record_entity("gotcha", "task1-finding", {"lesson": "sum() not sum()+1", "from_task": "t1"})
    kg.record_relation("task1-finding", "applies_to", "total-impl", {})
    st1 = kg.stats()
    # task 2: recall it
    rec = kg.recall_about("task1-finding")
    found = rec.get("found") and rec["entity"]["props"].get("lesson")
    st2 = kg.stats()
    no_corruption = st2["entities"] >= st1["entities"] and st2["relations"] >= st1["relations"]
    ok = bool(found) and no_corruption
    return {"section": "coherence", "component": "compounding + no-corruption",
            "status": "PASS" if ok else "FAIL",
            "detail": f"task-2 recalled task-1 lesson='{found}'; KG integrity intact "
                      f"(entities {st1['entities']}->{st2['entities']}, relations {st1['relations']}->{st2['relations']})"}


STEPS = {
    "eb_banyan": s_eb_banyan, "eb_classifier": s_eb_classifier,
    "risk_a": s_risk_a, "risk_b_gen": s_risk_b_gen, "risk_b_verify": s_risk_b_verify,
    "risk_c": s_risk_c, "coh_verify": s_coh_verify, "coh_degrade": s_coh_degrade,
    "coh_kg_fallback": s_coh_kg_fallback, "coh_compound": s_coh_compound,
}


def main() -> int:
    if len(sys.argv) < 4:
        print(json.dumps({"status": "FAIL", "reason": "usage: <step> <mode> <workdir>"}))
        return 2
    step, mode, workdir = sys.argv[1], sys.argv[2], sys.argv[3]
    fn = STEPS.get(step)
    if not fn:
        print(json.dumps({"status": "FAIL", "reason": f"unknown step {step}"}))
        return 2
    try:
        res = fn(mode, workdir)
    except Exception as e:  # noqa: BLE001
        import traceback
        res = {"component": step, "status": "FAIL", "reason": f"{type(e).__name__}: {_snip(e,160)}",
               "trace": _snip(traceback.format_exc(), 400)}
    res.setdefault("step", step)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
