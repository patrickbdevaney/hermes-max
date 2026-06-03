"""Verifier-guided test-time search — bounded best-of-N selection (Stage 1.2).

SWE-PRM-class selection (+10.7 pts) made losslessly-by-construction: candidates
are chosen by EXECUTION (run each through mcp-verify), never by a model judging
itself. The selected patch is one that actually goes green.

your inference host discipline (a single bandwidth-bound GPU stream — best-of-N competes with
itself for the one model):
  * default N is small (SEARCH_DEFAULT_N=3) and hard-capped (SEARCH_MAX_N=6);
  * the model-generation path requires $VLLM_BASE_URL and is meant for HARD
    subtasks only (the difficulty signal gates it via the skill);
  * the deterministic SELECTOR (candidates supplied) is cheap and always
    available — it only runs the verifier, no extra model calls.

Selection rule: keep only candidates that verify GREEN; among those prefer the
one passing the MOST tests, tie-broken by the SMALLEST diff (least code). If
none is green, say so honestly (caller escalates) — never return a red patch.

If $VLLM_BASE_URL is unreachable the generation path degrades to a clear error
and the agent falls back to writing the patch itself — never a crash.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

import otel_emit

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
SEARCH_MODEL = os.environ.get("SEARCH_MODEL", os.environ.get("EMBED_MODEL", "/model"))
DEFAULT_N = int(os.environ.get("SEARCH_DEFAULT_N", "3"))
MAX_N = int(os.environ.get("SEARCH_MAX_N", "6"))
GEN_TIMEOUT = float(os.environ.get("SEARCH_GEN_TIMEOUT", "120"))

VERIFY_PORT = int(os.environ.get("MCP_VERIFY_PORT", "9101"))
VERIFY_HOST = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
VERIFY_CALL_TIMEOUT = float(os.environ.get("SEARCH_VERIFY_TIMEOUT", "600"))

# Stage 4: the conductor's parallel_draft POOL lives on the escalation server.
ESCALATION_PORT = int(os.environ.get("MCP_ESCALATION_PORT", "9105"))
POOL_CALL_TIMEOUT = float(os.environ.get("SEARCH_POOL_TIMEOUT", "120"))

# M-Stage 6: optional reranker scoring for quality_threshold early-exit when no
# test oracle is available (auto-detect the local rerank serve on :8003).
RERANK_BASE_URL = os.environ.get("RERANK_BASE_URL", "http://127.0.0.1:8003").rstrip("/")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "/model")


def _rerank_score(query: str, doc: str) -> float | None:
    """Cross-encoder relevance of `doc` to `query` (~[0,1]); None if unreachable.
    Used only for the quality_threshold path (execution-based selection is default)."""
    base = RERANK_BASE_URL
    if not base or not doc:
        return None
    for path in ("/rerank", "/v1/rerank"):
        try:
            with httpx.Client(timeout=8) as c:
                r = c.post(f"{base}{path}", json={"model": RERANK_MODEL, "query": query[:2000],
                                                  "documents": [doc[:2000]]})
                r.raise_for_status()
                payload = r.json()
            results = payload.get("results", payload) if isinstance(payload, dict) else payload
            if isinstance(results, list) and results:
                return float(results[0].get("relevance_score", results[0].get("score", 0.0)))
        except Exception:  # noqa: BLE001
            continue
    return None


# ── verify boundary (graceful-degrade if mcp-verify is unreachable) ──────────
async def _call_verify(path: str, language: str) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://{VERIFY_HOST}:{VERIFY_PORT}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("verify", {"path": path, "language": language})
            text = getattr(res.content[0], "text", "") if res.content else ""
            data = res.structuredContent or (json.loads(text) if text else {})
            if isinstance(data, dict) and "result" in data and "passed" not in data:
                data = data["result"]
            return data if isinstance(data, dict) else {}


def _verify(path: str, language: str) -> dict[str, Any]:
    def _runner() -> dict[str, Any]:
        return asyncio.run(asyncio.wait_for(_call_verify(path, language), timeout=VERIFY_CALL_TIMEOUT))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            data = ex.submit(_runner).result(timeout=VERIFY_CALL_TIMEOUT + 30)
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "passed": False, "result": None, "error": f"{type(e).__name__}: {e}"}
    return {"reachable": True, "passed": bool(data.get("passed")), "result": data, "error": None}


async def _call_verify_formal(path: str, language: str) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://{VERIFY_HOST}:{VERIFY_PORT}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("verify_formal", {"path": path, "language": language})
            text = getattr(res.content[0], "text", "") if res.content else ""
            data = res.structuredContent or (json.loads(text) if text else {})
            if isinstance(data, dict) and "result" in data and isinstance(data.get("result"), dict):
                data = data["result"]
            return data if isinstance(data, dict) else {}


def _verify_formal(path: str, language: str) -> dict[str, Any]:
    """Call mcp-verify's verify_formal tool; degrade to an `unknown` result if unreachable."""
    def _runner() -> dict[str, Any]:
        return asyncio.run(asyncio.wait_for(_call_verify_formal(path, language), timeout=VERIFY_CALL_TIMEOUT))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_runner).result(timeout=VERIFY_CALL_TIMEOUT + 30)
    except Exception as e:  # noqa: BLE001
        return {"result": "unknown", "reason": f"verify_formal unreachable: {type(e).__name__}"}


def _formal_rank(formal_result: dict[str, Any] | None) -> int:
    """Rank the four-value formal verdict for best-of-N: verified beats unknown beats a
    spec_rejected/counterexample. A proven candidate outranks a merely-green one."""
    kind = (formal_result or {}).get("result")
    return {"verified": 2, "unknown": 1}.get(kind, 0)


def _main_source(files: dict[str, str], language: str) -> str | None:
    """The primary non-test source file of a candidate (largest matching-extension file
    that is not a test) — what the formal ladder should adjudicate."""
    ext = {"python": ".py", "rust": ".rs", "ts": ".ts", "go": ".go"}.get(language, ".py")
    cands = [(rel, c) for rel, c in (files or {}).items()
             if rel.endswith(ext) and "test" not in Path(rel).name.lower()]
    if not cands:
        return None
    return max(cands, key=lambda rc: len(rc[1]))[0]


_TESTS_PASSED_RE = re.compile(r"(\d+)\s+passed")


def _tests_passed(verify_result: dict[str, Any] | None) -> int:
    """Best-effort count of passing tests from the verify summary text."""
    if not verify_result:
        return 0
    blob = json.dumps(verify_result)
    m = _TESTS_PASSED_RE.search(blob)
    return int(m.group(1)) if m else 0


# ── deterministic selector (the lossless core — no model calls) ──────────────
def select_from_candidates(candidates: list[dict], tests: dict | None = None,
                           language: str = "python", base_files: dict | None = None,
                           early_exit: bool = False, formal: bool = False,
                           critical: bool = False, formal_top_k: int = 0) -> dict[str, Any]:
    """Run each candidate through mcp-verify in isolation; select the green one.

    candidates: [{"id": str, "files": {relpath: content}}, ...]
    tests:      {relpath: content} written into EVERY candidate dir (shared).
    base_files: {relpath: content} common scaffolding (e.g. pyproject) for all.
    Returns the selected candidate id + per-candidate verdicts. Never returns a
    red selection: if none is green, selected is None and reason says so.

    early_exit (M-Stage 6): return as soon as a candidate verifies GREEN, WITHOUT
    verifying the rest — execution-based early-exit (RASC: large sample savings at
    comparable accuracy; naive best-of-N gets less reliable as N grows). Trades
    'best among green' for 'first green', the right call for code where green==done.
    """
    if not candidates:
        return {"ok": False, "error": "no candidates supplied"}

    verdicts: list[dict[str, Any]] = []
    for cand in candidates:
        cid = str(cand.get("id", f"cand{len(verdicts)}"))
        files = cand.get("files", {}) or {}
        size = sum(len(v) for v in files.values())
        tmp = tempfile.mkdtemp(prefix=f"search-{cid}-")
        try:
            for rel, content in {**(base_files or {}), **files, **(tests or {})}.items():
                fp = Path(tmp) / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
            v = _verify(tmp, language)
            verdicts.append({
                "id": cid,
                "reachable": v["reachable"],
                "green": bool(v["passed"]),
                "tests_passed": _tests_passed(v["result"]),
                "size": size,
                "summary": (str(v["result"].get("summary")) if v.get("result") else v.get("error", ""))[:200],
            })
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # EARLY EXIT: first green wins — skip verifying the remaining candidates.
        if early_exit and verdicts[-1]["reachable"] and verdicts[-1]["green"]:
            otel_emit.record("best_of_n_result", {
                "candidates_generated": len(candidates), "candidates_verified": len(verdicts),
                "early_exit_fired": True, "selected": cid,
                "tests_passed": verdicts[-1]["tests_passed"]}, status="ok")
            return {"ok": True, "selected": cid, "early_exit": True,
                    "selected_files": files, "green_count": 1,
                    "candidates_verified": len(verdicts), "n": len(candidates),
                    "verdicts": verdicts,
                    "reason": f"early-exit: '{cid}' verified green after "
                              f"{len(verdicts)}/{len(candidates)} candidates "
                              f"({verdicts[-1]['tests_passed']} tests passed)"}

    if any(not vd["reachable"] for vd in verdicts):
        return {"ok": False, "verify_unreachable": True, "verdicts": verdicts,
                "reason": "mcp-verify unreachable — cannot select by execution; write the patch yourself"}

    green = [vd for vd in verdicts if vd["green"]]
    if not green:
        otel_emit.record("search_selected", {"selected": "none", "n": len(verdicts),
                                            "green": 0}, status="error")
        return {"ok": True, "selected": None, "verdicts": verdicts,
                "reason": "no candidate verified green — escalate or rethink the approach"}

    # A3 — formal-pass best-of-N tiebreaker. ONLY on CRITICAL modules and ONLY for the
    # top-k green survivors (passes tests → type/static → PBT+mutation → contract). Running
    # the solver rungs on all N would let solver wall-clock dominate, so it is capped.
    formal_used = False
    if formal and critical and green:
        k = formal_top_k or int(os.environ.get("SEARCH_FORMAL_TOPK", "3"))
        topk = sorted(green, key=lambda vd: (-vd["tests_passed"], vd["size"]))[:max(1, k)]
        for vd in topk:
            files = next((c.get("files") for c in candidates if str(c.get("id")) == vd["id"]), {}) or {}
            main = _main_source(files, language)
            rank = 0
            if main:
                tmp = tempfile.mkdtemp(prefix=f"formal-{vd['id']}-")
                try:
                    for rel, content in {**(base_files or {}), **files, **(tests or {})}.items():
                        fp = Path(tmp) / rel
                        fp.parent.mkdir(parents=True, exist_ok=True)
                        fp.write_text(content)
                    rank = _formal_rank(_verify_formal(str(Path(tmp) / main), language))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            vd["formal_rank"] = rank
        formal_used = True
        # select within the top-k by formal rank first, then the usual keys
        best = sorted(topk, key=lambda vd: (-vd.get("formal_rank", 0), -vd["tests_passed"], vd["size"]))[0]
    else:
        # prefer most tests passed, then smallest diff (least code)
        best = sorted(green, key=lambda vd: (-vd["tests_passed"], vd["size"]))[0]
    otel_emit.record("search_selected", {"selected": best["id"], "n": len(verdicts),
                                        "green": len(green), "tests_passed": best["tests_passed"],
                                        "size": best["size"],
                                        "formal_tiebreaker": formal_used,
                                        "formal_rank": best.get("formal_rank")}, status="ok")
    otel_emit.record("best_of_n_result", {
        "candidates_generated": len(candidates), "candidates_verified": len(verdicts),
        "early_exit_fired": False, "selected": best["id"], "green_count": len(green)}, status="ok")
    return {
        "ok": True,
        "selected": best["id"],
        "selected_files": next((c.get("files") for c in candidates
                                if str(c.get("id")) == best["id"]), {}),
        "green_count": len(green),
        "n": len(verdicts),
        "verdicts": verdicts,
        "formal_tiebreaker": formal_used,
        "reason": (f"selected '{best['id']}' (green, {best['tests_passed']} tests passed"
                   + (f", formal_rank={best.get('formal_rank')} [formal tiebreaker on critical top-k]"
                      if formal_used else ", smallest diff")
                   + f" among {len(green)} green of {len(verdicts)})"),
    }


# ── model generation (bounded; requires $VLLM_BASE_URL) ──────────────────────
def _extract_code(text: str) -> str:
    m = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip() + "\n"


def _generate_one(task_spec: str, language: str, temperature: float) -> str | None:
    if not VLLM_BASE_URL:
        return None
    payload = {
        "model": SEARCH_MODEL,
        "messages": [
            {"role": "system", "content": f"You are a precise {language} engineer. Output ONLY the "
             "complete file content in a single fenced code block, no prose."},
            {"role": "user", "content": task_spec},
        ],
        "temperature": temperature,
        "max_tokens": 1024,
    }
    try:
        with httpx.Client(timeout=GEN_TIMEOUT) as client:
            r = client.post(f"{VLLM_BASE_URL}/chat/completions", json=payload)
            r.raise_for_status()
            return _extract_code(r.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001
        return None


def generate_and_select(task_spec: str, n: int = 0, language: str = "python",
                        target_path: str = "solution.py", tests: dict | None = None,
                        base_files: dict | None = None,
                        candidates: list[dict] | None = None,
                        early_exit: bool = True, quality_threshold: float = 0.0) -> dict[str, Any]:
    """Bounded verifier-guided search. If `candidates` are supplied, skip
    generation and select among them (the cheap, always-available path). Else
    generate N candidates from $VLLM_BASE_URL (HARD subtasks only) and select.

    early_exit (M-Stage 6, default True for code): generate-and-verify ONE AT A TIME
    and return the moment a candidate verifies green — saving the cost of generating
    (and verifying) the remaining candidates. The largest saving is in generation
    (RASC reports up to ~85% fewer samples at comparable accuracy). With supplied
    candidates it short-circuits verification instead.
    quality_threshold (0-1, 0=off): when no test oracle is available, score each
    candidate against the task with the reranker and return the first above threshold.
    """
    n = DEFAULT_N if not n else n
    n = max(1, min(int(n), MAX_N))

    if candidates is None:
        if not VLLM_BASE_URL:
            return {"ok": False, "disabled": True,
                    "reason": "generation path needs $VLLM_BASE_URL; supply `candidates` to use the "
                              "selector directly, or write the patch yourself"}
        if not tests and quality_threshold <= 0:
            return {"ok": False, "error": "generation requires `tests` to select against (lossless "
                    "selection is execution-based), or quality_threshold>0 for reranker selection"}
        gen: list[dict] = []
        for i in range(n):
            # vary temperature across samples for diversity (no RNG needed)
            temp = round(0.2 + 0.6 * (i / max(1, n - 1)), 3) if n > 1 else 0.2
            code = _generate_one(task_spec, language, temp)
            if not code:
                continue
            cand = {"id": f"gen{i}", "files": {target_path: code}}
            gen.append(cand)
            # INTERLEAVED early-exit: verify (or score) THIS candidate now; stop
            # generating the rest the moment one is good enough.
            if early_exit and tests:
                sel1 = select_from_candidates([cand], tests, language, base_files, early_exit=True)
                if sel1.get("ok") and sel1.get("selected"):
                    sel1["candidates_generated"] = len(gen)
                    return sel1
            elif quality_threshold > 0:
                score = _rerank_score(task_spec, code)
                if score is not None and score >= quality_threshold:
                    otel_emit.record("best_of_n_result", {
                        "candidates_generated": len(gen), "candidates_verified": 0,
                        "early_exit_fired": True, "best_score": round(score, 4),
                        "threshold": quality_threshold, "selected": cand["id"]}, status="ok")
                    return {"ok": True, "selected": cand["id"], "selected_files": cand["files"],
                            "early_exit": True, "best_score": round(score, 4),
                            "candidates_generated": len(gen),
                            "reason": f"early-exit: candidate scored {score:.3f} >= {quality_threshold} "
                                      "(reranker; no test oracle)"}
        if not gen:
            return {"ok": False, "error": "no candidates generated (model unreachable?) — fall back"}
        candidates = gen

    return select_from_candidates(candidates, tests, language, base_files, early_exit=early_exit)


# ── Stage 4: verifier-selected parallel_draft across the conductor pool ───────
async def _call_pool_async(prompt: str, n: int) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://{VERIFY_HOST}:{ESCALATION_PORT}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("parallel_draft_pool", {"prompt": prompt, "n": n})
            text = getattr(res.content[0], "text", "") if res.content else ""
            data = res.structuredContent or (json.loads(text) if text else {})
            if isinstance(data, dict) and "result" in data and "candidates" not in data:
                data = data["result"]
            return data if isinstance(data, dict) else {}


def _call_pool(prompt: str, n: int) -> dict[str, Any]:
    """Get cross-family draft candidates from the conductor pool (escalation
    server). Degrades to an empty result if the server/role is unavailable."""
    def _runner() -> dict[str, Any]:
        return asyncio.run(asyncio.wait_for(_call_pool_async(prompt, n), timeout=POOL_CALL_TIMEOUT))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_runner).result(timeout=POOL_CALL_TIMEOUT + 30)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "candidates": [], "error": f"{type(e).__name__}: {e}"}


def parallel_draft(task_spec: str, language: str = "python",
                   target_path: str = "solution.py", tests: dict | None = None,
                   base_files: dict | None = None, n: int = 0,
                   draft_brief: str | None = None) -> dict[str, Any]:
    """Verifier-selected best-of-N across the FREE/cheap conductor pool — the
    optimal use of 'slop' models. ONLY for VERIFIABLE subtasks: `tests` (the
    objective oracle) is REQUIRED; without it this is an ambiguous task that must
    route to the synthesize role instead (returned as route_to='synthesize').

    Flow: fan out one draft per present pool family (cross-family DIVERSITY, not
    temperature sampling), extract each candidate's code, run EVERY candidate
    through mcp-verify, and select the one that goes green (most tests, smallest
    diff). If NONE pass -> route_to='synthesize'. If the pool is empty/unreachable
    -> degrade to local generation ($VLLM_BASE_URL) or route_to='local'. The local
    model integrates + checkpoints the winning diff (slop models never touch the
    repo). Never raises."""
    # ── GATE: verifiable subtasks only (objective oracle present) ─────────────
    if not tests:
        return {"ok": False, "verifiable": False, "route_to": "synthesize",
                "reason": "no test oracle supplied — this subtask is AMBIGUOUS; route to the "
                          "synthesize role (no oracle => the verifier can't select). parallel_draft "
                          "is only for verifiable subtasks."}
    n = DEFAULT_N if not n else n
    n = max(1, min(int(n), MAX_N))

    spec = draft_brief or task_spec
    prompt = (f"{spec}\n\nOutput ONLY the complete content of `{target_path}` in a single fenced "
              f"{language} code block, no prose.")

    pool = _call_pool(prompt, n)
    candidates: list[dict] = []
    sources: list[str] = []
    for c in pool.get("candidates", []) if isinstance(pool, dict) else []:
        if c.get("ok") and c.get("content"):
            cid = f"{c.get('provider')}:{str(c.get('model', '')).split('/')[-1]}"
            candidates.append({"id": cid, "files": {target_path: _extract_code(c["content"])}})
            sources.append(cid)

    if not candidates:
        # degrade: local best-of-N if the model endpoint is up, else route local
        if VLLM_BASE_URL:
            gen = generate_and_select(task_spec, n, language, target_path, tests, base_files)
            gen["draft_source"] = "local_fallback"
            gen["reason"] = (gen.get("reason", "") +
                             " | pool empty/unreachable -> local generation fallback").strip(" |")
            return gen
        otel_emit.record("draft_fanout", {"n_sources": 0, "degraded": "local"}, status="error")
        return {"ok": False, "route_to": "local", "candidates_from": [],
                "pool_error": pool.get("error") if isinstance(pool, dict) else None,
                "reason": "draft pool empty/unreachable and no $VLLM_BASE_URL — write the patch yourself"}

    otel_emit.record("draft_fanout", {"n_sources": len(candidates),
                                     "families": ",".join(sources)}, status="ok")
    # early_exit=True (M-Stage 6): the first pool draft that verifies green wins —
    # no need to verify the rest (execution-based, code where green==done).
    sel = select_from_candidates(candidates, tests, language, base_files, early_exit=True)
    sel["draft_source"] = "pool"
    sel["candidates_from"] = sources
    # none-pass fallback: the subtask was harder than 'verifiable-slop' assumed
    if sel.get("ok") and sel.get("selected") is None:
        sel["route_to"] = "synthesize"
        sel["reason"] = (sel.get("reason", "") +
                         " | none of the pool drafts passed -> route to the synthesize role").strip()
    return sel


def status() -> dict[str, Any]:
    return {
        "generation_available": bool(VLLM_BASE_URL),
        "model": SEARCH_MODEL if VLLM_BASE_URL else None,
        "default_n": DEFAULT_N,
        "max_n": MAX_N,
        "verify_endpoint": f"http://{VERIFY_HOST}:{VERIFY_PORT}/mcp",
        "pool_endpoint": f"http://{VERIFY_HOST}:{ESCALATION_PORT}/mcp",
        "note": "selector (candidates supplied) is always available; generation needs $VLLM_BASE_URL. "
                "parallel_draft fans the conductor pool over verifiable subtasks only. "
                "Use on HARD subtasks only — best-of-N competes for the one your inference host GPU.",
    }
