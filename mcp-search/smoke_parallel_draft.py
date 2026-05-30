#!/usr/bin/env python3
"""Standalone smoke test for parallel_draft (Stage 4). Offline: the pool call and
the verify EXECUTION are stubbed (the stub reads the candidate file and greens the
correct one), so the real selection machinery — temp dirs, green filtering, most-
tests/smallest-diff tie-break, fallbacks — is exercised deterministically.

Asserts the Stage-4 DoD:
  • GATE: a subtask with NO test oracle routes to 'synthesize' (not draft)
  • fan-out: cross-family candidates are turned into selectable patches
  • SELECT: the verifier picks the GREEN candidate (not a model judging itself)
  • none-pass: all-red drafts -> route_to 'synthesize'
  • degrade: empty pool + no $VLLM_BASE_URL -> route_to 'local'
  • degrade: empty pool + $VLLM_BASE_URL up -> local generation fallback
"""

from __future__ import annotations

import sys
from pathlib import Path

import search_core as sc


def _ok(m: str) -> None:
    print(f"  ok: {m}")


def _fail(m: str) -> None:
    print(f"  FAIL: {m}")
    sys.exit(1)


# Stub the verify EXECUTION: read the written candidate and green the correct add().
def _fake_verify(path: str, language: str) -> dict:
    blob = ""
    for fp in Path(path).rglob("*.py"):
        try:
            blob += fp.read_text()
        except Exception:  # noqa: BLE001
            pass
    green = "returna+b" in blob.replace(" ", "").replace("\n", "")
    return {"reachable": True, "passed": green, "error": None,
            "result": {"summary": "2 passed" if green else "1 failed, 1 error"}}


sc._verify = _fake_verify

TESTS = {"test_add.py": "from solution import add\n\ndef test_add():\n    assert add(2,3)==5\n"}
CORRECT = "def add(a, b):\n    return a + b\n"
WRONG1 = "def add(a, b):\n    return a - b\n"
WRONG2 = "def add(a, b):\n    return a * b\n"


def _pool(*contents):
    fams = [("cerebras", "zai-glm-4.7"), ("cerebras", "gpt-oss-120b"),
            ("groq", "openai/gpt-oss-120b"), ("groq", "qwen/qwen3-32b")]
    return lambda prompt, n: {
        "ok": True,
        "candidates": [{"provider": p, "model": m, "ok": True, "content": c}
                       for (p, m), c in zip(fams, contents)],
        "n_passed": len(contents)}


def main() -> None:
    # 1. GATE: no test oracle -> route to synthesize, NOT draft
    g = sc.parallel_draft("do something architectural and ambiguous", tests=None)
    if g.get("ok") or g.get("route_to") != "synthesize" or g.get("verifiable") is not False:
        _fail(f"ambiguous (no tests) must route to synthesize: {g}")
    _ok("GATE: no oracle -> route_to 'synthesize' (verifiable=False)")

    # 2. SELECT: verifier picks the GREEN candidate among a cross-family pool
    sc._call_pool = _pool(WRONG1, CORRECT, WRONG2)  # correct is the 2nd family
    r = sc.parallel_draft("implement add(a,b)", tests=TESTS, target_path="solution.py")
    if not r.get("ok") or r.get("selected") is None:
        _fail(f"a green candidate should be selected: {r}")
    if "return a + b" not in r["selected_files"].get("solution.py", ""):
        _fail(f"the SELECTED candidate must be the correct one: {r.get('selected_files')}")
    if r.get("draft_source") != "pool":
        _fail(f"draft_source should be 'pool': {r}")
    _ok(f"SELECT: verifier chose green '{r['selected']}' from {r['candidates_from']} "
        f"(green {r.get('green_count')}/{r.get('n')})")

    # 3. none-pass: all drafts red -> route_to synthesize
    sc._call_pool = _pool(WRONG1, WRONG2)
    rn = sc.parallel_draft("implement add(a,b)", tests=TESTS)
    if rn.get("selected") is not None or rn.get("route_to") != "synthesize":
        _fail(f"all-red drafts should route to synthesize: {rn}")
    _ok("none-pass: all-red pool drafts -> route_to 'synthesize'")

    # 4. degrade: empty pool + no $VLLM_BASE_URL -> route_to local
    sc._call_pool = lambda prompt, n: {"ok": False, "candidates": [], "error": "pool unreachable"}
    saved_vllm = sc.VLLM_BASE_URL
    sc.VLLM_BASE_URL = ""
    rl = sc.parallel_draft("implement add(a,b)", tests=TESTS)
    if rl.get("ok") or rl.get("route_to") != "local":
        _fail(f"empty pool + no model -> route_to local: {rl}")
    _ok("degrade: empty pool + no $VLLM_BASE_URL -> route_to 'local'")

    # 5. degrade: empty pool + $VLLM_BASE_URL up -> local generation fallback
    sc.VLLM_BASE_URL = "http://stub.invalid/v1"
    sc._generate_one = lambda task_spec, language, temperature: CORRECT
    rf = sc.parallel_draft("implement add(a,b)", tests=TESTS)
    if not rf.get("ok") or rf.get("selected") is None or rf.get("draft_source") != "local_fallback":
        _fail(f"empty pool + model up -> local generation fallback that selects: {rf}")
    _ok(f"degrade: empty pool + model up -> local_fallback selected '{rf['selected']}'")
    sc.VLLM_BASE_URL = saved_vllm


if __name__ == "__main__":
    main()
    print("parallel_draft smoke test PASSED")
