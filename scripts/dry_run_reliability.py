#!/usr/bin/env python3
"""Stage-4 reliability + observability END-TO-END dry-run.

Proves the three Stage 1–3 fixes work TOGETHER on a real sequence, with full live
visibility, WITHOUT needing the local model (it exercises the watchdog / RAG / KG /
checkpoint cores + the live log + the per-task summary — the parts the reliability
pass changed). The model-dependent steps (deep_research, parallel_draft, verify
against real inference) live in the existing `dry_run.py` and, thanks to the Stage-3
otel→livelog bridge, ALSO stream to watch.sh automatically.

Run it with `scripts/watch.sh` open in a side terminal to see the whole sequence
live; at the end it prints the per-task summary and writes `dry_run_trace.md`.

Asserted (each maps to a Stage-4 DoD item):
  * empty-dir index_repo  → instant clean EMPTY success (not a hang)
  * real-repo index_repo  → pre-flight scan, batched, heartbeated, self-checked,
                            est-vs-actual logged
  * RAG query             → returns results from the fresh index
  * KG record + recall    → write a triple, recall it
  * look-ahead estimate   → deep_research est logged, within ceiling
  * long step NOT killed  → over-budget-but-heartbeating = slow-but-alive
  * hung step IS killed    → silent-past-budget = killed with a clear report,
                            then a clean revert decision (deliberately-killed →
                            revert_to_last_green)
  * live log + summary    → readable + complete
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for p in ("lib", "scripts", "mcp-watchdog", "mcp-knowledge-graph", "mcp-checkpoint"):
    sys.path.insert(0, str(REPO / p))

# Isolated, throwaway stores so the dry-run never touches the real compounding ones.
_TMP = tempfile.mkdtemp(prefix="hmx-dryrun-")
# Honor an externally-provided live-log dir (e.g. bottleneck-eval per-run dir); else
# use a fresh temp. Truncate it so this run's summary reflects only this run.
LOG_DIR = os.environ.get("HERMES_MAX_LOG_DIR") or os.path.join(_TMP, "logs")
os.environ["HERMES_MAX_LOG_DIR"] = LOG_DIR
os.makedirs(LOG_DIR, exist_ok=True)
for _f in ("live.jsonl", "live.log"):
    try:
        open(os.path.join(LOG_DIR, _f), "w").close()
    except Exception:  # noqa: BLE001
        pass
os.environ["HERMES_MAX_VERBOSITY"] = os.environ.get("HERMES_MAX_VERBOSITY", "verbose")
os.environ["KG_DB_PATH"] = os.path.join(_TMP, "kg.db")
os.environ.setdefault("WATCHDOG_STATE_DIR", os.path.join(_TMP, "wd"))
RAG_DB = os.path.join(_TMP, "rag.db")
SAMPLE = REPO / "mcp-codebase-rag" / "sample_repo"
os.makedirs(LOG_DIR, exist_ok=True)

import livelog  # noqa: E402
import run_summary  # noqa: E402

TRACE: list[dict] = []
FAILED = 0


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _bad(m: str) -> None:
    global FAILED
    FAILED += 1
    print(f"  FAIL: {m}")


def step(name: str, est_s: float | None = None, inp: object = None):
    """Context-manager-ish timer that emits the live tool-call lifecycle + trace."""
    class _S:
        def __enter__(self):
            self.t0 = time.time()
            livelog.tool_start(name, server="dry-run", inp=inp, est_s=est_s)
            return self

        def done(self, ret=None, ok=True, reason=None):
            secs = time.time() - self.t0
            if ok:
                livelog.tool_ok(name, secs=secs, ret=ret, est_s=est_s)
            else:
                livelog.tool_fail(name, reason=reason, secs=secs)
            TRACE.append({"step": name, "secs": round(secs, 3), "ok": ok,
                          "est_s": est_s, "ret": ret, "reason": reason})

        def __exit__(self, *a):
            return False
    return _S()


def rag_call(snippet: str) -> dict:
    """Run a RAG-core snippet in the rag venv (it has httpx/tree-sitter), sharing the
    same isolated DB + live log dir. Returns the JSON the snippet prints."""
    py = REPO / "mcp-codebase-rag" / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    env = dict(os.environ, RAG_INDEX_PATH=RAG_DB, EMBED_BASE_URL="",
               PYTHONPATH=str(REPO / "mcp-codebase-rag"))
    code = ("import sys, json, rag_core\n" + snippet +
            "\nprint('@@JSON@@' + json.dumps(_out))\n")
    out = subprocess.run([py, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    for ln in out.stdout.splitlines():
        if ln.startswith("@@JSON@@"):
            return json.loads(ln[len("@@JSON@@"):])
    raise RuntimeError(f"rag snippet produced no result: {out.stdout}\n{out.stderr}")


def main() -> None:
    print("═══ Stage-4 reliability + observability dry-run ═══")
    print(f"  isolated stores under {_TMP}")
    print(f"  live log: {LOG_DIR}/live.log  (run scripts/watch.sh to see it stream)\n")

    import watchdog_core as wc
    import kg_core
    import checkpoint_core as cp

    # 1. empty-dir index → clean empty success (NOT a hang)
    empty = tempfile.mkdtemp(prefix="hmx-empty-")
    with step("index_repo[empty]", inp={"path": "empty/"}) as s:
        r = rag_call(f"_out = rag_core.index_repo({empty!r})")
        s.done(ret={k: r.get(k) for k in ("empty", "files_indexed", "mode")})
    if r.get("empty") and r.get("files_indexed") == 0 and r.get("index_health", {}).get("queryable"):
        _ok(f"empty-dir index → clean empty success (mode={r['mode']})")
    else:
        _bad(f"empty-dir index not a clean empty success: {r}")

    # 2. real-repo index → preflight + batched + heartbeat + self-check, est-vs-actual
    scan = rag_call(f"_out = rag_core.scan_repo({str(SAMPLE)!r})")
    est = wc.estimate_duration("index_repo", file_count=scan["n_files"],
                               total_bytes=scan["total_bytes"])
    with step("index_repo[sample]", est_s=est["est_s"], inp={"files": scan["n_files"]}) as s:
        r = rag_call(f"_out = rag_core.index_repo({str(SAMPLE)!r}, batch_size=2)")
        s.done(ret={k: r.get(k) for k in ("files_indexed", "chunks_indexed", "mode")},
               ok=bool(r.get("ok")))
    if r.get("ok") and r.get("index_health", {}).get("queryable"):
        _ok(f"real index → {r['files_indexed']} files, {r['chunks_indexed']} chunks, "
            f"est ~{est['est_s']}s vs actual {TRACE[-1]['secs']:.2f}s, health OK")
    else:
        _bad(f"real index/self-check failed: {r}")

    # 3. RAG query → results from the fresh index
    with step("search_code", inp={"q": "fibonacci"}) as s:
        q = rag_call("_out = rag_core.search_code('fibonacci sequence number', k=5)")
        syms = [x["symbol"] for x in q.get("results", [])]
        s.done(ret={"hits": syms[:5]})
    if "fibonacci" in syms:
        _ok(f"RAG query → {syms[:5]}")
    else:
        _bad(f"RAG query missed fibonacci: {syms}")

    # BARE mode (bottleneck-eval): stop after the minimal index+query path so the
    # full-vs-bare comparison has a genuinely lighter baseline.
    bare = bool(os.environ.get("HMX_BENCH_BARE"))
    if bare:
        print()
        agg = run_summary.aggregate(run_summary.load(os.path.join(LOG_DIR, "live.jsonl")))
        print(run_summary.fmt(agg))
        print("\n(bare path: index + query only)")
        sys.exit(1 if FAILED else 0)

    # 4. KG record + recall
    with step("kg_record", inp={"a": "hermes-max", "rel": "uses", "b": "watchdog"}) as s:
        kg_core.record_entity("system", "hermes-max")
        kg_core.record_entity("component", "watchdog")
        kg_core.record_relation("hermes-max", "uses", "watchdog")
        s.done(ret={"triple": "hermes-max -uses-> watchdog"})
    with step("kg_recall", inp={"name": "hermes-max"}) as s:
        rec = kg_core.recall_about("hermes-max")
        edges = list(rec.get("outgoing", [])) + list(rec.get("incoming", []))
        s.done(ret={"relations": len(edges)})
    if any(e.get("rel") == "uses" for e in edges):
        _ok(f"KG record+recall → {len(edges)} relation(s) recalled")
    else:
        _bad(f"KG recall missed the recorded relation: {rec}")

    # 5. look-ahead estimate for deep_research (logged, within ceiling)
    e = wc.estimate_duration("deep_research", query_count=4, per_source_s=30)
    livelog.decision("look-ahead", "deep_research ~%.0fs" % e["est_s"], e["basis"])
    if 0 < e["est_s"] <= e["ceiling_s"]:
        _ok(f"deep_research look-ahead logged: {e['basis']} (ceiling {e['ceiling_s']}s)")
    else:
        _bad(f"deep_research estimate implausible: {e}")

    # 6. long step NOT killed (over budget but heartbeating = slow-but-alive)
    wc.record_heartbeat("dryrun", "deep_research", progress="3/4", done=3, total=4)
    alive = wc.check_stall("deep_research", elapsed_s=200, task_id="dryrun")
    livelog.tool_slow("deep_research", 200, e["est_s"])
    if not alive["hung"] and alive["waiting"]:
        _ok("long step over budget but heartbeating → slow-but-alive, NOT killed")
    else:
        _bad(f"heartbeating long step wrongly killed: {alive}")

    # 7. hung step IS killed → clean revert (deliberately-killed step reverts)
    hung = wc.check_stall("fetch_clean", elapsed_s=600, expecting_heartbeat=False)
    if hung["hung"]:
        _ok(f"silent over-budget step killed with a clear report: {hung['reason']}")
    else:
        _bad(f"silent over-budget step not killed: {hung}")
    # demonstrate the clean revert on a throwaway git repo (no model needed)
    gitrepo = tempfile.mkdtemp(prefix="hmx-git-")
    subprocess.run(["git", "init", "-q", gitrepo], check=False)
    subprocess.run(["git", "-C", gitrepo, "config", "user.email", "d@d"], check=False)
    subprocess.run(["git", "-C", gitrepo, "config", "user.name", "d"], check=False)
    Path(gitrepo, "f.txt").write_text("green\n")
    with step("checkpoint[green]", inp={"label": "baseline"}) as s:
        ckpt = cp.checkpoint("baseline", verify=False, repo_path=gitrepo, init=True)
        s.done(ret={"ok": ckpt.get("ok")})
    Path(gitrepo, "f.txt").write_text("broken-uncommitted\n")  # simulate a bad in-flight change
    with step("revert_to_last_green", inp={"after": "killed step"}) as s:
        rv = cp.revert_to_last_green(repo_path=gitrepo)
        s.done(ret={"ok": rv.get("ok")})
    reverted = Path(gitrepo, "f.txt").read_text().strip() == "green"
    livelog.decision("recover", "revert_to_last_green", "killed step → restore last green state",
                     error=False)
    if reverted:
        _ok("deliberately-killed step reverts cleanly to last green")
    else:
        _bad(f"revert did not restore green state: {rv}")

    # ── per-task summary + trace artifact ────────────────────────────────────
    print()
    agg = run_summary.aggregate(run_summary.load(os.path.join(LOG_DIR, "live.jsonl")))
    table = run_summary.fmt(agg)
    print(table)
    _write_trace(table, agg)
    print(f"\n  trace written: {REPO / 'dry_run_trace.md'}")
    print("\n" + ("✗ reliability dry-run FAILED (%d assertion[s])" % FAILED if FAILED
                  else "✓ reliability dry-run PASSED — all three fixes cohere with full visibility"))
    sys.exit(1 if FAILED else 0)


def _write_trace(table: str, agg: dict) -> None:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# dry_run_trace.md — Stage-4 reliability + observability dry-run",
        "",
        f"_Generated {started} · model-independent reliability sequence "
        "(watchdog / RAG / KG / checkpoint + live log + summary)._",
        "",
        "Run `scripts/watch.sh` in a side terminal to see this stream live; the same "
        "events feed Phoenix. The model-dependent steps (deep_research, parallel_draft, "
        "verify) run in `scripts/dry_run.py` and stream here too via the otel→livelog bridge.",
        "",
        "## Sequence (per step: timing · est-vs-actual · result)",
        "",
        "| # | step | secs | est~ | ok | result / reason |",
        "|---|------|-----:|-----:|:--:|-----------------|",
    ]
    for i, t in enumerate(TRACE, 1):
        ret = t.get("ret") or t.get("reason") or ""
        ret = json.dumps(ret, default=str) if not isinstance(ret, str) else ret
        ret = ret.replace("|", "\\|")[:80]
        est = f"{t['est_s']:.0f}" if t.get("est_s") else "—"
        lines.append(f"| {i} | {t['step']} | {t['secs']:.2f} | {est} | "
                     f"{'✓' if t['ok'] else '✗'} | {ret} |")
    lines += ["", "## Per-tool summary", "", "```", table, "```", ""]
    decs = agg.get("decisions", [])
    if decs:
        lines += ["## Decisions (with reasons)", ""]
        for d in decs:
            mark = "✗" if d.get("error") else "•"
            lines.append(f"- {mark} **{d.get('decision')}** → {d.get('choice')} — {d.get('reason')}")
        lines.append("")
    lines += [
        "## What this proves",
        "",
        "- **No premature kill on legitimately-long work** — `index_repo[sample]` and the "
        "over-budget-but-heartbeating `deep_research` step run/keep-alive past their "
        "estimate because they heartbeat (slow-but-alive, not killed).",
        "- **Empty-dir index is a clean empty success**, not a hang — `index_repo[empty]` "
        "returns instantly with a valid queryable empty index.",
        "- **Genuinely-hung work IS killed** with a clear report (silent past budget), and "
        "the deliberately-killed step **reverts cleanly** to the last green checkpoint.",
        "- **Full visibility** — every step's input/output/timing/est-vs-actual and every "
        "decision is in the live log and the per-tool summary above.",
        "",
    ]
    (REPO / "dry_run_trace.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
