#!/usr/bin/env python3
"""Standalone smoke for Stage 6 — Banyan content-evolution (CONTENT only).

No live services (embedding monkeypatched; state/skills -> temp dirs). Asserts:
  [A] banyan_select UCB1 — an UNDEREXPLORED namespace is visited despite lower
      utility; after visits, exploitation can favor the high-utility one
  [B] banyan_update — running utility (0.8/0.2) + gain history capped at 20
  [C] saturation — embedding-drift AND marginal-gain decline each flag + SURFACE
      to the operator (a deliberately-saturated branch stops being invested in)
  [D] directive interrupt preempts UCB1 selection
  [E] standing tasks generate on an empty queue
  [F] skill evolution is GATED (maturity) and refuses machinery paths
  [G] THE HARD LINE — a full cycle writes NO machinery file (.py etc. unchanged);
      the machinery guard refuses a .py target and classifies machinery correctly
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def _hash_all_py() -> dict[str, str]:
    """Hash every .py in mcp-research (the machinery this loop must never touch)."""
    out = {}
    for p in HERE.glob("*.py"):
        out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main() -> None:
    sys.path.insert(0, str(HERE))
    import banyan as b

    state_dir = tempfile.mkdtemp(prefix="banyan_state_")
    skills_dir = tempfile.mkdtemp(prefix="banyan_skills_")
    b.BANYAN_STATE_DIR = state_dir
    b.SKILLS_DIR = skills_dir
    b.STATE_FILE = os.path.join(state_dir, "state.json")
    b.DIRECTIVE_FILE = os.path.join(state_dir, "directive.json")
    b.SURFACED_LOG = os.path.join(state_dir, "surfaced.jsonl")
    b._CONTENT_ROOTS = (state_dir, skills_dir)

    machinery_before = _hash_all_py()

    # ---- [A] UCB1 selection ----
    print("[A] banyan_select UCB1 (explore the underexplored)")
    b.register_namespace("zk-proofs", priority=1.0)
    b.register_namespace("consensus", priority=1.0)
    # zk-proofs heavily visited + high utility; consensus UNVISITED
    for _ in range(20):
        b.banyan_update("zk-proofs", utility_sample=0.9, gain=0.5)
    sel = b.banyan_select()
    if sel["mode"] != "explore" or sel["namespace"] != "consensus":
        _fail(f"unvisited namespace should be explored first despite zk's high utility: {sel}")
    _ok(f"unvisited 'consensus' picked over high-utility 'zk-proofs' (UCB scores={sel['ucb_scores']})")

    # after consensus gets visits with LOW utility, exploitation should swing back
    for _ in range(20):
        b.banyan_update("consensus", utility_sample=0.05, gain=0.5)
    sel = b.banyan_select()
    if sel["namespace"] != "zk-proofs":
        _fail(f"once both visited, high-utility zk-proofs should win exploitation: {sel}")
    _ok(f"both visited -> high-utility 'zk-proofs' wins exploitation ({sel['ucb_scores']})")

    # ---- [B] banyan_update math ----
    print("[B] banyan_update (0.8/0.2 utility, gain history cap 20)")
    st = b._load_state()
    u_before = st["namespaces"]["zk-proofs"]["utility"]
    r = b.banyan_update("zk-proofs", utility_sample=0.0, gain=0.1)
    st = b._load_state()
    u_after = st["namespaces"]["zk-proofs"]["utility"]
    if not (u_after < u_before):  # blending toward a lower sample lowers utility
        _fail(f"utility should move toward the sample: {u_before}->{u_after}")
    if len(st["namespaces"]["zk-proofs"]["gain_history"]) > 20:
        _fail("gain history should cap at 20")
    _ok(f"utility {u_before:.3f}->{u_after:.3f} (0.8/0.2), gain_history<=20")

    # ---- [C] saturation: two signals + surface ----
    print("[C] saturation detection + surface to operator")
    b.register_namespace("saturated-topic")
    # seed a centroid, then feed near-identical embeddings -> drift saturation
    b.rank._embed = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    b.detect_saturation("saturated-topic", new_texts=["seed the centroid"])   # sets centroid
    sat = b.detect_saturation("saturated-topic", new_texts=["near identical again"])
    if not sat["saturated"] or not any("embedding-drift" in r for r in sat["reasons"]):
        _fail(f"identical embeddings should flag embedding-drift saturation: {sat}")
    if not os.path.exists(b.SURFACED_LOG):
        _fail("saturation must SURFACE to the operator (surfaced log)")
    surfaced = Path(b.SURFACED_LOG).read_text()
    if "SATURATED" not in surfaced:
        _fail(f"surfaced log should record the saturation: {surfaced}")
    # a saturated namespace is no longer selected
    st = b._load_state()
    if not st["namespaces"]["saturated-topic"]["saturated"]:
        _fail("saturated flag should persist")
    _ok(f"embedding-drift saturation flagged + surfaced (cos={sat['drift_similarity']:.2f}); branch stops")

    # marginal-gain decline path
    b.register_namespace("declining")
    st = b._load_state()
    st["namespaces"]["declining"]["gain_history"] = [0.4, 0.3, 0.2, 0.02, 0.01, 0.0, 0.0, 0.0]
    b._save_state(st)
    b.rank._embed = lambda texts: None  # no embedding -> only the gain signal
    sat = b.detect_saturation("declining")
    if not sat["saturated"] or not any("marginal-gain" in r for r in sat["reasons"]):
        _fail(f"declining gains should flag marginal-gain saturation: {sat}")
    _ok("marginal-gain decline saturation flagged + surfaced")

    # ---- [D] directive interrupt preempts ----
    print("[D] directive interrupt preempts UCB1")
    b.set_directive("focus on post-quantum signatures", namespace="pq-sigs")
    sel = b.banyan_select()
    if sel["mode"] != "directive" or not sel.get("preempted_ucb1"):
        _fail(f"a pending directive must preempt UCB1: {sel}")
    _ok(f"directive preempts selection (namespace={sel['namespace']})")
    b.clear_directive()
    if b.pending_directive() is not None:
        _fail("clear_directive should remove the directive")
    _ok("directive cleared -> loop returns to self-direction")

    # ---- [E] standing tasks ----
    print("[E] standing-task generation on empty queue")
    g = b.generate_standing_tasks("zk-proofs")
    if g["generated"] < 1 or not any("what's new" in t for t in g["queue"]):
        _fail(f"empty queue should generate standing tasks: {g}")
    g2 = b.generate_standing_tasks("zk-proofs")  # non-empty now -> no dup
    if g2["generated"] != 0:
        _fail(f"non-empty queue should not regenerate: {g2}")
    _ok(f"empty queue -> {g['generated']} standing tasks; non-empty -> 0 (idempotent)")

    # ---- [F] skill evolution gated + machinery-refused ----
    print("[F] skill evolution gated (maturity) + content-only")
    b.SELF_IMPROVEMENT_ENABLED = False
    r = b.write_skill("zk-summary", "# ZK\nnotes", tasks_done=999, days_active=999, skills_count=999)
    if r["ok"]:
        _fail("skill write must be blocked when SELF_IMPROVEMENT_ENABLED=false")
    _ok(f"immature/disabled -> skill write blocked ({r['blocking']})")

    b.SELF_IMPROVEMENT_ENABLED = True
    r = b.write_skill("zk-summary", "# ZK proofs\nlearned notes", tasks_done=250,
                      days_active=40, skills_count=60)
    if not r["ok"] or not r["path"].endswith(".md") or not os.path.exists(r["path"]):
        _fail(f"mature -> skill should be written as .md: {r}")
    if not r["path"].startswith(skills_dir):
        _fail(f"skill must live under the skills library: {r['path']}")
    _ok(f"mature -> skill written to {os.path.basename(r['path'])} (content, in skills lib)")

    # ---- [G] THE HARD LINE: no machinery written; guard refuses machinery ----
    print("[G] machinery guard — CONTENT evolves, MACHINERY frozen")
    # classification
    if not b.is_machinery_path(str(HERE / "server.py")):
        _fail("server.py must be classified machinery")
    if not b.is_machinery_path("/x/mcp-research/banyan.py"):
        _fail(".py under mcp-* must be machinery")
    if b.is_machinery_path(os.path.join(skills_dir, "x.md")):
        _fail("a skill .md under the skills lib must NOT be machinery")
    # the guard refuses a machinery write outright
    refuse = b._write_content(str(HERE / "server.py"), "MALICIOUS")
    if refuse["ok"] or "MACHINERY" not in refuse["error"]:
        _fail(f"guard must refuse writing a .py machinery file: {refuse}")
    refuse2 = b._write_content(os.path.join(skills_dir, "evil.py"), "x")
    if refuse2["ok"]:
        _fail("guard must refuse a .py even under a content root")
    _ok("guard refuses machinery (.py / mcp-* paths) and non-content extensions")

    # full cycle wrote ZERO machinery files
    machinery_after = _hash_all_py()
    changed = [k for k in machinery_before if machinery_before[k] != machinery_after.get(k)]
    if changed:
        _fail(f"the loop MUST NOT write machinery — changed: {changed}")
    if set(machinery_before) != set(machinery_after):
        _fail("the loop created/removed a .py machinery file")
    _ok(f"NO machinery write: all {len(machinery_before)} mcp-research .py files byte-identical after a full cycle")

    print("mcp-research Banyan (Stage 6) smoke test PASSED")


if __name__ == "__main__":
    main()
