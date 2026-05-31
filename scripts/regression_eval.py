#!/usr/bin/env python3
"""regression_eval.py — regression-eval-as-code (Phase 6.3). A standing suite of
DETERMINISTIC capability probes (fast tool-level checks, not full agent turns) with a
stored baseline. The gate BLOCKS if a check that PASSED in the baseline now fails — so
a change that breaks a previously-working capability is caught.

  --update   run the probes and (re)write the baseline (~/.hermes-max/regression-baseline.json)
  (default)  run the probes, compare to the baseline, exit 1 on any regression

Modeled on gbrain's bench-publish + eval-gate. Run with a venv that has `mcp`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

H = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
BASELINE = Path(os.path.expanduser("~/.hermes-max/regression-baseline.json"))


async def _call(port: int, tool: str, args: dict, timeout: float = 40):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    box = {}
    try:
        async with streamablehttp_client(f"http://{H}:{port}/mcp") as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await asyncio.wait_for(s.call_tool(tool, args), timeout)
                txt = getattr(res.content[0], "text", "") if res.content else ""
                d = res.structuredContent or (json.loads(txt) if txt else {})
                box["v"] = d.get("result", d) if isinstance(d, dict) else d
    except BaseException:  # noqa: BLE001
        if "v" in box:
            return box["v"]
        return {"_error": True}
    return box["v"]


async def probe_codegraph_impact():
    d = await _call(9114, "code_impact", {"symbol": "verify"})
    return isinstance(d, dict) and (d.get("impacted_count") or 0) >= 1, f"impacted={d.get('impacted_count')}"


async def probe_lsp_references():
    # _call unwraps {ok,tool,result} -> the inner Serena references payload (a string
    # or dict). A non-trivial payload means references were found.
    d = await _call(9112, "lsp_find_references",
                    {"name_path": "record_trace", "relative_path": "mcp-observability/observability_core.py"})
    ok = (isinstance(d, str) and len(d) > 10) or (isinstance(d, dict) and bool(d) and not d.get("_error"))
    return ok, f"refs_len={len(d) if isinstance(d,str) else 'dict'}"


async def probe_graph_signals():
    d = await _call(9102, "search_code", {"query": "verify gate lint typecheck", "k": 4})
    mode = d.get("mode", "") if isinstance(d, dict) else ""
    return "gsig" in mode, f"mode={mode}"


async def probe_best_of_n_early_exit():
    cands = [{"id": "bad", "files": {"s.py": "def add(a,b):\n return a-b\n"}},
             {"id": "good", "files": {"s.py": "def add(a,b):\n return a+b\n"}}]
    tests = {"test_s.py": "from s import add\ndef test():\n assert add(2,3)==5\n"}
    d = await _call(9108, "generate_and_select",
                    {"task_spec": "add", "candidates": cands, "tests": tests, "early_exit": True})
    return isinstance(d, dict) and d.get("selected") == "good", f"selected={d.get('selected') if isinstance(d,dict) else '?'}"


async def probe_metamorphic():
    tmp = tempfile.mkdtemp(prefix="reg-")
    Path(tmp, "srt.py").write_text("def srt(xs): return sorted(xs)\n")
    d = await _call(9101, "metamorphic_test", {"path": f"{tmp}/srt.py", "function": "srt", "relation": "idempotent"})
    import shutil; shutil.rmtree(tmp, ignore_errors=True)
    return isinstance(d, dict) and d.get("status") == "pass", f"status={d.get('status') if isinstance(d,dict) else '?'}"


async def probe_condenser():
    hist = [{"role": "system", "content": "x"}] + [{"role": "user", "content": "turn " * 50}] * 30
    d = await _call(9104, "condense_context", {"history": hist, "force": True}, timeout=200)
    return isinstance(d, dict) and d.get("fired") and (d.get("ratio") or 1) < 1.0, \
        f"ratio={d.get('ratio') if isinstance(d,dict) else '?'}"


PROBES = {
    "codegraph_impact": probe_codegraph_impact,
    "lsp_references": probe_lsp_references,
    "graph_signals": probe_graph_signals,
    "best_of_n_early_exit": probe_best_of_n_early_exit,
    "metamorphic_test": probe_metamorphic,
    "condenser": probe_condenser,
}


async def run_all() -> dict:
    out = {}
    for name, fn in PROBES.items():
        try:
            ok, detail = await fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        out[name] = {"pass": bool(ok), "detail": detail}
        print(f"  {'✓' if ok else '✗'} {name}: {detail}")
    return out


def main() -> int:
    update = "--update" in sys.argv
    skip = {a.split("=", 1)[1] for a in sys.argv if a.startswith("--skip=")}
    results = asyncio.run(run_all())
    if update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({k: v["pass"] for k, v in results.items()}, indent=2))
        print(f"\n✓ baseline written: {BASELINE} ({sum(v['pass'] for v in results.values())}/{len(results)} pass)")
        return 0
    try:
        baseline = json.loads(BASELINE.read_text())
    except Exception:  # noqa: BLE001
        print("\n⚠ no baseline — run with --update first; not gating.")
        return 0
    regressions = [k for k, was in baseline.items()
                   if was and k not in skip and not results.get(k, {}).get("pass")]
    if regressions:
        print(f"\n✗ REGRESSION — {len(regressions)} previously-passing check(s) now FAIL: {', '.join(regressions)}")
        return 1
    print(f"\n✓ no regression ({sum(1 for k,was in baseline.items() if was and results.get(k,{}).get('pass'))}"
          f"/{sum(1 for was in baseline.values() if was)} baselined checks still pass)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
