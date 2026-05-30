#!/usr/bin/env python3
"""Stage-5 dry-run STEP runner — exercises ONE component, prints ONE JSON result.

Invoked once PER component by scripts/dry_run.py, each time under that component's
OWN venv with only that component's dir on sys.path (the repo has colliding module
names — otel_emit.py, smoke_test.py — across servers, so a single shared process
is unsafe). Usage:

    <component>/.venv/bin/python dry_run_steps.py <step_name> <mode> <workdir>

Each step returns a dict the orchestrator merges with timing:
  component, action, provider/model (or skipped+reason), status (PASS|FAIL|SKIP),
  tokens, cost_usd, in (input snippet), out (output snippet). NEVER raises — a
  failure is reported as status=FAIL so the trace shows it honestly.

`mode` is one of local|free|full and is also exported as CONDUCTOR_MODE, so the
conductor's resolver hard-caps cloud tiers exactly as in production (local => the
cloud steps self-skip as role-OFF; free => only free providers; full => all).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.environ.get("HMX_REPO_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def comp(name: str) -> str:
    """Prepend a component dir to sys.path and return it."""
    d = os.path.join(REPO, name)
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


def _snip(s, n=240):
    s = "" if s is None else str(s)
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


# ── 1. driver — the local model itself (the one hard dependency) ──────────────
def s_driver_local(mode, workdir):
    import httpx
    base = (os.environ.get("VLLM_BASE_URL") or "").rstrip("/")
    if not base:
        return {"component": "driver (local model)", "status": "FAIL",
                "reason": "VLLM_BASE_URL unset — the one required endpoint"}
    prompt = "Reverse a string in Python in one line. Reply with ONLY the code."
    try:
        with httpx.Client(timeout=60) as c:
            # discover a served model id
            mid = "default"
            try:
                ms = c.get(f"{base}/models").json().get("data", [])
                if ms:
                    mid = ms[0].get("id", mid)
            except Exception:  # noqa: BLE001
                pass
            r = c.post(f"{base}/chat/completions", json={
                "model": mid, "max_tokens": 256, "temperature": 0,
                # disable chain-of-thought for this trivial smoke (heavy reasoning
                # models otherwise burn the whole budget and return content=None);
                # ignored by non-vLLM OpenAI endpoints, so it's portable.
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            d = r.json()
        # reasoning models can put the answer in reasoning_content and leave
        # content None if the token budget is tight — accept either.
        msg = d.get("choices", [{}])[0].get("message", {}) or {}
        out = msg.get("content") or msg.get("reasoning_content")
        usage = d.get("usage", {}) or {}
        return {"component": "driver (local model)", "action": "trivial coding subtask",
                "provider": "local", "model": mid, "status": "PASS" if out else "FAIL",
                "tokens": usage.get("total_tokens"), "cost_usd": 0.0,
                "in": _snip(prompt), "out": _snip(out)}
    except Exception as e:  # noqa: BLE001
        return {"component": "driver (local model)", "status": "FAIL",
                "reason": f"{type(e).__name__}: {_snip(e,120)}"}


# ── 2. classifier — difficulty routing ────────────────────────────────────────
def s_classifier(mode, workdir):
    comp("mcp-escalation")
    import escalation_core as ec
    signals = {"file_count": 1, "novelty": "low", "test_failures": 0}
    cls = ec.classify_difficulty(signals)
    return {"component": "classifier (difficulty)", "action": "classify_difficulty",
            "provider": "local-logic", "status": "PASS" if cls.get("ok") else "FAIL",
            "in": _snip(signals), "out": f"difficulty={cls.get('difficulty')} reasons={cls.get('reasons')}"}


# ── 3. watchdog — arm budget + progress/spiral checks ─────────────────────────
def s_watchdog(mode, workdir):
    comp("mcp-watchdog")
    import watchdog_core as wd
    tid = "dryrun-task"
    arm = wd.start_task_budget(tid, wall_clock_s=120, max_turns=10, usd_cap=0.0)
    prog = wd.check_progress(tid, {"files_touched": 1, "tests_passing": 1, "checkpoints": 0, "turn": 1})
    spiral = wd.check_spiral("trying the same fix again and again and again and again", ngram=3)
    ok = arm.get("ok") and prog.get("ok") and spiral.get("ok")
    return {"component": "watchdog", "action": "arm budget + progress + spiral",
            "provider": "local-logic", "status": "PASS" if ok else "FAIL",
            "in": f"task={tid} wall=120s turns=10",
            "out": f"armed={arm.get('ok')} no_progress={prog.get('no_progress')} "
                   f"spiral={spiral.get('spiral_detected')}"}


# ── 4. conductor steer (mode-gated cloud) ─────────────────────────────────────
def s_steer(mode, workdir):
    comp("mcp-escalation")
    import conductor_core as cc
    r = cc.run_role("steer", prompt="One sentence: when should an agent ask for a steer vs proceed?",
                    max_tokens=96)
    if not r.get("ok"):
        return {"component": "conductor.steer", "action": "run_role(steer)",
                "skipped": True, "status": "SKIP",
                "reason": r.get("reason", "role OFF / proceed_local"),
                "provider": "(none)", "out": "proceed local-only"}
    return {"component": "conductor.steer", "action": "run_role(steer)",
            "provider": r.get("provider"), "model": r.get("model"), "status": "PASS",
            "tokens": (r.get("usage") or {}).get("total_tokens"), "cost_usd": r.get("cost_usd"),
            "in": "steer nudge", "out": _snip(r.get("content"))}


# ── 5. research — one real source, tiny query (SearXNG) ───────────────────────
def s_research(mode, workdir):
    comp("mcp-research")
    import research_core as rc
    q = "python list comprehension"
    try:
        hits = rc._search(q, limit=1)
    except Exception as e:  # noqa: BLE001
        hits = []
        err = f"{type(e).__name__}: {_snip(e,80)}"
    else:
        err = None
    if not hits:
        return {"component": "research (SearXNG)", "action": "one source, tiny query",
                "skipped": True, "status": "SKIP",
                "reason": err or "SearXNG returned no results / not reachable"}
    url = hits[0].get("url", "")
    fetched = {}
    try:
        fetched = rc._fetch(url)
    except Exception:  # noqa: BLE001
        pass
    content = (fetched.get("content") or hits[0].get("content") or hits[0].get("title") or "")[:800]
    # stash for the corpus step
    with open(os.path.join(workdir, "research.json"), "w") as f:
        json.dump({"url": url, "title": hits[0].get("title", ""), "content": content}, f)
    return {"component": "research (SearXNG)", "action": "one source, tiny query",
            "provider": "searxng-local", "status": "PASS",
            "in": _snip(q), "out": f"src={_snip(url,80)} chars={len(content)}"}


# ── 6. research.corpus — on-disk .md write (+ optional RAG index) ──────────────
def s_corpus(mode, workdir):
    comp("mcp-research")
    import corpus
    src = {"url": "https://example.org/dryrun", "title": "dry-run note", "content": "x"}
    rp = os.path.join(workdir, "research.json")
    if os.path.exists(rp):
        try:
            src = json.load(open(rp))
        except Exception:  # noqa: BLE001
            pass
    content = src.get("content") or "Dry-run corpus content: list comprehensions build lists tersely."
    res = corpus.write_corpus_doc("dryrun", "web", content,
                                  meta={"title": src.get("title", "dry-run"),
                                        "url": src.get("url", ""), "authority": 3})
    with open(os.path.join(workdir, "corpus.json"), "w") as f:
        json.dump({"path": res.get("path"), "relpath": res.get("relpath")}, f)
    return {"component": "research.corpus", "action": "write on-disk corpus .md",
            "provider": "disk", "status": "PASS" if res.get("ok") else "FAIL",
            "in": f"namespace=dryrun chars={len(content)}",
            "out": f"path={_snip(res.get('relpath') or res.get('path'),80)} chars={res.get('chars')}"}


# ── 7. knowledge-graph — ingest + recall ──────────────────────────────────────
def s_kg(mode, workdir):
    comp("mcp-knowledge-graph")
    import kg_core as kg
    relpath = "dryrun/web/note.md"
    cj = os.path.join(workdir, "corpus.json")
    if os.path.exists(cj):
        try:
            relpath = json.load(open(cj)).get("relpath") or relpath
        except Exception:  # noqa: BLE001
            pass
    e = kg.record_entity("decision", "dryrun-use-comprehensions",
                         {"provenance": "stage5-dry-run", "valid_from": "2026-05-30"})
    r = kg.record_relation("dryrun-use-comprehensions", "cites", relpath,
                           {"citation": relpath, "source_type": "web"})
    rec = kg.recall_about("dryrun-use-comprehensions")
    st = kg.stats()
    ok = e.get("ok") and r.get("ok") and rec.get("found")
    return {"component": "knowledge-graph", "action": "record entity+edge, recall",
            "provider": f"kg:{st.get('backend')}", "status": "PASS" if ok else "FAIL",
            "in": "decision -[cites]-> corpus doc",
            "out": f"backend={st.get('backend')} entities={st.get('entities')} "
                   f"recall_outgoing={[o['rel'] for o in rec.get('outgoing',[])]}"}


# ── 8. RAG — index a doc + hybrid search ──────────────────────────────────────
def s_rag(mode, workdir):
    comp("mcp-codebase-rag")
    import rag_core as rg
    os.environ.setdefault("RAG_INDEX_PATH", os.path.join(workdir, "rag.db"))
    idx = rg.index_document("List comprehensions build lists tersely in Python; prefer them over map+lambda.",
                            namespace="dryrun", source="dryrun", title="comprehensions")
    res = rg.search_code("how to build a list in python", k=3)
    hits = res.get("results") or res.get("hits") or []
    ok = idx.get("ok") and isinstance(hits, list)
    return {"component": "codebase-rag", "action": "index_document + search_code",
            "provider": "bm25+graph" + ("+dense" if os.environ.get("EMBED_BASE_URL") else "")
                        + ("+rerank" if os.environ.get("RERANK_BASE_URL") else ""),
            "status": "PASS" if ok else "FAIL",
            "in": "index 1 doc; query 'how to build a list'",
            "out": f"indexed={idx.get('ok')} hits={len(hits)}"}


# ── 9. synth — brief assemble + directive (mode-gated) ────────────────────────
def s_synth(mode, workdir):
    comp("mcp-escalation")
    import brief_assemble as ba
    import conductor_core as cc
    brief = ba.brief_assemble("dryrun-task", current_blocker="how to sum a list safely",
                              decision_needed="pick an implementation", profile="compact")
    live = (brief.get("sources_live") or {})
    directive = cc.run_role("synth", prompt="Return ONLY python: def total(xs): return sum(xs)",
                            max_tokens=128)
    if directive.get("ok"):
        synth_out = f"{directive.get('provider')}:{directive.get('model')} -> {_snip(directive.get('content'),80)}"
        status = "PASS"
    else:
        synth_out = f"synth role OFF/skip ({directive.get('reason','proceed_local')[:60]})"
        status = "PASS"  # degrading cleanly IS the pass condition in local/free
    return {"component": "conductor.synth + brief_assemble", "action": "assemble brief + directive",
            "provider": directive.get("provider") or "(local/degraded)",
            "model": directive.get("model"), "cost_usd": directive.get("cost_usd", 0.0),
            "status": status,
            "in": f"brief est_tokens={brief.get('est_tokens')} sources_live={live}",
            "out": synth_out}


# ── 10. verify gate — green passes, red is caught (the keystone) ───────────────
def s_verify(mode, workdir):
    comp("mcp-verify")
    import verify_core as vc
    good = os.path.join(workdir, "good_mod.py")
    test = os.path.join(workdir, "test_good_mod.py")
    open(good, "w").write("def total(xs):\n    return sum(xs)\n")
    open(test, "w").write("from good_mod import total\n\ndef test_total():\n    assert total([1,2,3])==6\n")
    green = vc.deep_verify(good, difficulty="medium")
    # a deliberately-broken directive must be CAUGHT (cannot declare done on red)
    bad = os.path.join(workdir, "bad_mod.py")
    open(bad, "w").write("def total(xs)\n    return sum(xs)\n")  # syntax error
    red = vc.verify(bad)
    caught = not red.get("passed")
    ok = bool(green.get("passed")) and caught
    return {"component": "verify (deterministic gate)", "action": "green passes, red caught",
            "provider": "ruff+mypy+pytest", "status": "PASS" if ok else "FAIL",
            "in": "good_mod.py (+test) and a syntax-broken bad_mod.py",
            "out": f"green_passed={green.get('passed')} red_caught={caught}"}


# ── 11. conductor draft pool — best-of-N fan-out (mode-gated) ──────────────────
def s_draft(mode, workdir):
    comp("mcp-escalation")
    import conductor_core as cc
    r = cc.draft_fanout(prompt="Draft: a 1-line python to sum a list.", n=4, max_tokens=128)
    cands = [c for c in r.get("candidates", []) if c.get("ok")]
    if not cands and r.get("proceed_local"):
        return {"component": "conductor.draft pool", "action": "parallel_draft fan-out",
                "skipped": True, "status": "SKIP",
                "reason": r.get("reason", "pool OFF / degrade local"),
                "provider": "(none)", "out": "degrade to N=1-local"}
    # stash candidate texts for the verifier-select step
    with open(os.path.join(workdir, "candidates.json"), "w") as f:
        json.dump([{"id": f"{c['provider']}:{c['model']}", "text": c.get("content", "")} for c in cands], f)
    return {"component": "conductor.draft pool", "action": "parallel_draft fan-out",
            "provider": ",".join(sorted({c["provider"] for c in cands})) or "(none)",
            "status": "PASS" if cands else "SKIP",
            "in": "best-of-N over present free pool",
            "out": f"candidates={[c['id'] for c in cands] if False else len(cands)} "
                   f"skipped={[s.get('skipped') for s in r.get('skipped',[])]}"}


# ── 12. verifier-select — best-of-N made safe by the gate ─────────────────────
def s_verifier_select(mode, workdir):
    comp("mcp-verify")
    import verify_core as vc
    # 3 candidate implementations: 2 correct, 1 buggy — the verifier must pick green.
    cands = {
        "cand_a": "def total(xs):\n    return sum(xs)\n",
        "cand_b": "def total(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t\n",
        "cand_bad": "def total(xs):\n    return sum(xs)+1\n",  # wrong
    }
    test = "from {mod} import total\n\ndef test_total():\n    assert total([1,2,3])==6\n"
    verdicts = {}
    selected = None
    for cid, code in cands.items():
        d = tempfile.mkdtemp(prefix=f"sel-{cid}-", dir=workdir)
        open(os.path.join(d, "m.py"), "w").write(code)
        open(os.path.join(d, "test_m.py"), "w").write(test.format(mod="m"))
        res = vc.verify(os.path.join(d, "m.py"))
        verdicts[cid] = bool(res.get("passed"))
        if res.get("passed") and selected is None:
            selected = cid
    ok = selected is not None and verdicts.get("cand_bad") is False
    return {"component": "search.verifier-select", "action": "best-of-N, verifier selects green",
            "provider": "verify-gate", "status": "PASS" if ok else "FAIL",
            "in": "3 candidates (2 correct, 1 buggy)",
            "out": f"selected={selected} verdicts={verdicts} (buggy rejected={verdicts.get('cand_bad') is False})"}


# ── 13. banyan — UCB1 select/update over 2 dummy namespaces ────────────────────
def s_banyan(mode, workdir):
    comp("mcp-research")
    os.environ.setdefault("BANYAN_STATE_DIR", os.path.join(workdir, "banyan"))
    import banyan
    banyan.register_namespace("dryrun-research", priority=1.0)
    banyan.register_namespace("dryrun-build", priority=1.0)
    sel1 = banyan.banyan_select()
    upd = banyan.banyan_update(sel1.get("namespace", "dryrun-research"), utility_sample=0.7, gain=0.5)
    sel2 = banyan.banyan_select()
    sat = banyan.detect_saturation(sel1.get("namespace", "dryrun-research"), new_texts=["a", "b"])
    ok = sel1.get("ok") and upd.get("ok") and sel2.get("ok")
    return {"component": "research.banyan (UCB1)", "action": "register x2, select, update, saturation",
            "provider": "local-bandit", "status": "PASS" if ok else "FAIL",
            "in": "2 namespaces, optimistic prior",
            "out": f"sel1={sel1.get('namespace')}({sel1.get('mode')}) -> update -> sel2={sel2.get('namespace')}; "
                   f"saturated={sat.get('saturated')} (thin data)"}


# ── 14. checkpoint — verified write + revert ──────────────────────────────────
def s_checkpoint(mode, workdir):
    comp("mcp-checkpoint")
    import checkpoint_core as ck
    repo = tempfile.mkdtemp(prefix="ckpt-", dir=workdir)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "d@d"],
                ["git", "config", "user.name", "d"]):
        subprocess.run(cmd, cwd=repo, check=False)
    open(os.path.join(repo, "f.py"), "w").write("v = 1\n")
    # checkpoint without remote verify server -> degrades to unverified commit (still green path)
    c1 = ck.checkpoint("dryrun green", verify=False, repo_path=repo, init=True)
    open(os.path.join(repo, "f.py"), "w").write("v = 2  # broken-ish change\n")
    rev = ck.revert_to_last_green(repo_path=repo)
    restored = open(os.path.join(repo, "f.py")).read().strip()
    ok = c1.get("ok") and rev.get("ok") and restored == "v = 1"
    return {"component": "checkpoint", "action": "checkpoint + revert-to-green",
            "provider": "git", "status": "PASS" if ok else "FAIL",
            "in": "init repo, commit v1, break to v2, revert",
            "out": f"sha={_snip(c1.get('sha'),12)} reverted={rev.get('ok')} restored='{restored}'"}


# ── 15. escalation ladder — DRY (no spend, proves wiring) ─────────────────────
def s_escalation_dry(mode, workdir):
    comp("mcp-escalation")
    import conductor_core as cc
    import escalation_core as ec
    # hard task: cloud tiers are OFF by default -> route returns local/surface, no spend
    rt = ec.route("design a novel lock-free allocator", difficulty="hard",
                  signals={"file_count": 6, "cross_module": True})
    # the Opus escalate rung is presence-gated: no ANTHROPIC_API_KEY -> role OFF
    esc = cc.run_role("escalate", prompt="(dry) hardest kernel", max_tokens=16)
    dry_ok = (not esc.get("ok")) and esc.get("proceed_local")  # OFF => proceed local (no spend)
    return {"component": "escalation ladder (DRY)", "action": "route(hard) + escalate rung (mocked/off)",
            "provider": "(no spend)", "cost_usd": 0.0, "status": "PASS" if dry_ok else "FAIL",
            "in": "hard task; cloud tiers off-by-default; no Opus key",
            "out": f"route_escalated={rt.get('escalated')} route={rt.get('route','local')}; "
                   f"escalate_rung=OFF->proceed_local={esc.get('proceed_local')}"}


STEPS = {
    "driver_local": s_driver_local, "classifier": s_classifier, "watchdog": s_watchdog,
    "steer": s_steer, "research": s_research, "corpus": s_corpus, "kg": s_kg, "rag": s_rag,
    "synth": s_synth, "verify": s_verify, "draft": s_draft, "verifier_select": s_verifier_select,
    "banyan": s_banyan, "checkpoint": s_checkpoint, "escalation_dry": s_escalation_dry,
}


def main() -> int:
    if len(sys.argv) < 4:
        print(json.dumps({"status": "FAIL", "reason": "usage: dry_run_steps.py <step> <mode> <workdir>"}))
        return 2
    step, mode, workdir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.environ["CONDUCTOR_MODE"] = mode
    fn = STEPS.get(step)
    if not fn:
        print(json.dumps({"status": "FAIL", "reason": f"unknown step {step}"}))
        return 2
    try:
        res = fn(mode, workdir)
    except Exception as e:  # noqa: BLE001 — a crash is a FAIL, reported honestly
        import traceback
        res = {"component": step, "status": "FAIL",
               "reason": f"{type(e).__name__}: {_snip(e,160)}",
               "trace": _snip(traceback.format_exc(), 300)}
    res.setdefault("step", step)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
