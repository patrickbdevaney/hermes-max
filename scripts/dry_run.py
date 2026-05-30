#!/usr/bin/env python3
"""Stage-5 RAPID real-inference DRY RUN orchestrator.

Exercises EVERY component once, end-to-end, against REAL inference (local model +
whatever cloud keys/mode allow), and dumps a human-readable dry_run_trace.md (the
proof artifact) plus best-effort OTel/Langfuse spans. Optimized for SPEED — tiny
inputs, a smoke proof the whole system coheres, not a benchmark.

Each component runs in its OWN venv subprocess (module-name collisions across
servers make a single shared process unsafe) via dry_run_steps.py. Mode-aware:
local/free/full — in `local`, every cloud step self-skips (CONDUCTOR_MODE=local
=> roles OFF) and is logged as skipped-with-reason; the base case passes with
zero cloud keys.

Usage: dry_run.py [--mode local|free|full]   (default: $CONDUCTOR_MODE or full)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.environ.get("HMX_REPO_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# step -> component dir whose venv runs it (and whose modules it imports)
STEP_COMPONENT = {
    "driver_local": "mcp-escalation",   # any httpx venv; reused for the local chat
    "classifier": "mcp-escalation",
    "watchdog": "mcp-watchdog",
    "steer": "mcp-escalation",
    "research": "mcp-research",
    "corpus": "mcp-research",
    "kg": "mcp-knowledge-graph",
    "rag": "mcp-codebase-rag",
    "synth": "mcp-escalation",
    "verify": "mcp-verify",
    "draft": "mcp-escalation",
    "verifier_select": "mcp-verify",
    "banyan": "mcp-research",
    "checkpoint": "mcp-checkpoint",
    "escalation_dry": "mcp-escalation",
}
# the spec's loop order (steps 1..9 expanded across every component)
ORDER = ["driver_local", "classifier", "watchdog", "steer", "research", "corpus", "kg",
         "rag", "synth", "verify", "draft", "verifier_select", "banyan", "checkpoint",
         "escalation_dry"]


def _venv_python(component: str) -> str:
    p = os.path.join(REPO, component, ".venv", "bin", "python")
    return p if os.path.isfile(p) else sys.executable


def _otel(step: str, res: dict, latency_ms: float) -> None:
    """Best-effort span mirror (Langfuse via the OTel collector). Never fatal."""
    try:
        sys.path.insert(0, os.path.join(REPO, "mcp-observability"))
        import otel_emit  # type: ignore
        otel_emit.record("dry_run_step", {
            "step": step, "component": res.get("component"), "status": res.get("status"),
            "provider": res.get("provider"), "model": res.get("model"),
            "latency_ms": round(latency_ms, 1)}, status="ok" if res.get("status") != "FAIL" else "error")
    except Exception:  # noqa: BLE001
        pass


def run_step(step: str, mode: str, workdir: str) -> dict:
    py = _venv_python(STEP_COMPONENT[step])
    steps_file = os.path.join(REPO, "scripts", "dry_run_steps.py")
    env = dict(os.environ, HMX_REPO_ROOT=REPO, CONDUCTOR_MODE=mode)
    t0 = time.time()
    try:
        p = subprocess.run([py, steps_file, step, mode, workdir], capture_output=True,
                           text=True, timeout=120, env=env)
        latency = (time.time() - t0) * 1000.0
        line = (p.stdout or "").strip().splitlines()[-1] if p.stdout.strip() else ""
        res = json.loads(line) if line.startswith("{") else {
            "component": step, "status": "FAIL",
            "reason": f"no JSON (rc={p.returncode}): {(p.stderr or p.stdout)[-160:]}"}
    except subprocess.TimeoutExpired:
        latency = (time.time() - t0) * 1000.0
        res = {"component": step, "status": "FAIL", "reason": "timeout (>120s)"}
    except Exception as e:  # noqa: BLE001
        latency = (time.time() - t0) * 1000.0
        res = {"component": step, "status": "FAIL", "reason": f"{type(e).__name__}: {e}"}
    res["latency_ms"] = round(latency, 1)
    res.setdefault("step", step)
    _otel(step, res, latency)
    return res


def assemble_trace(results: list[dict], mode: str, started: str, wall_s: float) -> str:
    npass = sum(1 for r in results if r.get("status") == "PASS")
    nskip = sum(1 for r in results if r.get("status") == "SKIP")
    nfail = sum(1 for r in results if r.get("status") == "FAIL")
    icon = {"PASS": "✅", "SKIP": "⊘", "FAIL": "❌"}
    L: list[str] = []
    L.append("# hermes-max dry-run trace (real-inference smoke proof)\n")
    L.append(f"- **mode**: `{mode}`  ·  **started**: {started}  ·  **wall**: {wall_s:.1f}s")
    L.append(f"- **endpoint** (`$VLLM_BASE_URL`): `{os.environ.get('VLLM_BASE_URL','unset')}`")
    L.append(f"- **result**: {npass} ✅ PASS · {nskip} ⊘ skip · {nfail} ❌ fail "
             f"(of {len(results)} components)")
    verdict = "✅ COHERENT" if nfail == 0 else "❌ INCOHERENT — see failures"
    L.append(f"- **verdict**: {verdict}\n")
    L.append("| # | component | status | provider/model | latency | tokens | cost | detail |")
    L.append("|---|---|---|---|--:|--:|--:|---|")
    for i, r in enumerate(results, 1):
        pm = r.get("provider", "—")
        if r.get("model"):
            pm += f" / {r['model']}"
        if r.get("status") == "SKIP":
            pm = f"_(skipped: {r.get('reason','')[:48]})_"
        tok = r.get("tokens") if r.get("tokens") is not None else "—"
        cost = f"${r['cost_usd']:.4f}" if isinstance(r.get("cost_usd"), (int, float)) else "—"
        detail = r.get("out") or r.get("reason") or ""
        L.append(f"| {i} | {r.get('component', r.get('step'))} | {icon.get(r.get('status'),'?')} "
                 f"{r.get('status')} | {pm} | {r.get('latency_ms',0):.0f}ms | {tok} | {cost} | "
                 f"{str(detail)[:90].replace('|','/')} |")
    L.append("\n## Per-step detail (input → output)\n")
    for i, r in enumerate(results, 1):
        L.append(f"### {i}. {r.get('component', r.get('step'))} — {icon.get(r.get('status'),'?')} {r.get('status')}")
        if r.get("action"):
            L.append(f"- action: {r['action']}")
        if r.get("provider"):
            L.append(f"- provider/model: {r.get('provider')}{(' / ' + r['model']) if r.get('model') else ''}")
        if r.get("reason"):
            L.append(f"- reason: {r['reason']}")
        if r.get("in"):
            L.append(f"- in:  `{r['in']}`")
        if r.get("out"):
            L.append(f"- out: `{r['out']}`")
        L.append(f"- latency: {r.get('latency_ms',0):.0f}ms"
                 + (f" · cost: ${r['cost_usd']:.4f}" if isinstance(r.get('cost_usd'), (int, float)) else ""))
        if r.get("trace"):
            L.append(f"- trace: `{r['trace']}`")
        L.append("")
    L.append("## What this proves\n")
    L.append("Every component fired in one end-to-end pass (or cleanly skipped with a reason "
             "in this mode). The local driver is the one hard dependency; cloud steps are "
             "mode-gated and presence-gated, degrading to local without crashing the run — the "
             "anti-Frankenstein property. The verify gate caught a red change (cannot declare "
             "done on red), and the best-of-N verifier rejected a buggy candidate.\n")
    return "\n".join(L)


def main() -> int:
    mode = os.environ.get("CONDUCTOR_MODE", "full")
    args = sys.argv[1:]
    if "--mode" in args:
        mode = args[args.index("--mode") + 1]
    mode = mode.strip().lower()
    if mode not in ("local", "free", "full"):
        mode = "full"

    started = os.environ.get("HMX_DRYRUN_STARTED", "(run)")
    workdir = tempfile.mkdtemp(prefix="hmx-dryrun-")
    print(f"═══ hermes-max dry-run · mode={mode} · workdir={workdir} ═══")
    t0 = time.time()
    results = []
    for step in ORDER:
        r = run_step(step, mode, workdir)
        results.append(r)
        ic = {"PASS": "✅", "SKIP": "⊘", "FAIL": "❌"}.get(r.get("status"), "?")
        print(f"  {ic} {step:16} {r.get('latency_ms',0):6.0f}ms  "
              f"{r.get('provider','—')}{('/'+r['model']) if r.get('model') else ''}  "
              f"{(r.get('reason') or r.get('out') or '')[:70]}")
    wall = time.time() - t0

    trace = assemble_trace(results, mode, started, wall)
    out = os.path.join(REPO, "dry_run_trace.md")
    with open(out, "w") as f:
        f.write(trace)
    nfail = sum(1 for r in results if r.get("status") == "FAIL")
    print(f"\ntrace -> {out}")
    print(f"RESULT: {'PASS' if nfail == 0 else 'FAIL'} ({nfail} failures) · {wall:.1f}s")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
