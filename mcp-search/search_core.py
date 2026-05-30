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
                           language: str = "python", base_files: dict | None = None) -> dict[str, Any]:
    """Run each candidate through mcp-verify in isolation; select the green one.

    candidates: [{"id": str, "files": {relpath: content}}, ...]
    tests:      {relpath: content} written into EVERY candidate dir (shared).
    base_files: {relpath: content} common scaffolding (e.g. pyproject) for all.
    Returns the selected candidate id + per-candidate verdicts. Never returns a
    red selection: if none is green, selected is None and reason says so.
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

    if any(not vd["reachable"] for vd in verdicts):
        return {"ok": False, "verify_unreachable": True, "verdicts": verdicts,
                "reason": "mcp-verify unreachable — cannot select by execution; write the patch yourself"}

    green = [vd for vd in verdicts if vd["green"]]
    if not green:
        otel_emit.record("search_selected", {"selected": "none", "n": len(verdicts),
                                            "green": 0}, status="error")
        return {"ok": True, "selected": None, "verdicts": verdicts,
                "reason": "no candidate verified green — escalate or rethink the approach"}

    # prefer most tests passed, then smallest diff (least code)
    best = sorted(green, key=lambda vd: (-vd["tests_passed"], vd["size"]))[0]
    otel_emit.record("search_selected", {"selected": best["id"], "n": len(verdicts),
                                        "green": len(green), "tests_passed": best["tests_passed"],
                                        "size": best["size"]}, status="ok")
    return {
        "ok": True,
        "selected": best["id"],
        "selected_files": next((c.get("files") for c in candidates
                                if str(c.get("id")) == best["id"]), {}),
        "green_count": len(green),
        "n": len(verdicts),
        "verdicts": verdicts,
        "reason": f"selected '{best['id']}' (green, {best['tests_passed']} tests passed, "
                  f"smallest diff among {len(green)} green of {len(verdicts)})",
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
                        candidates: list[dict] | None = None) -> dict[str, Any]:
    """Bounded verifier-guided search. If `candidates` are supplied, skip
    generation and select among them (the cheap, always-available path). Else
    generate N candidates from $VLLM_BASE_URL (HARD subtasks only) and select.
    """
    n = DEFAULT_N if not n else n
    n = max(1, min(int(n), MAX_N))

    if candidates is None:
        if not VLLM_BASE_URL:
            return {"ok": False, "disabled": True,
                    "reason": "generation path needs $VLLM_BASE_URL; supply `candidates` to use the "
                              "selector directly, or write the patch yourself"}
        if not tests:
            return {"ok": False, "error": "generation requires `tests` to select against (lossless "
                    "selection is execution-based)"}
        gen: list[dict] = []
        for i in range(n):
            # vary temperature across samples for diversity (no RNG needed)
            temp = round(0.2 + 0.6 * (i / max(1, n - 1)), 3) if n > 1 else 0.2
            code = _generate_one(task_spec, language, temp)
            if code:
                gen.append({"id": f"gen{i}", "files": {target_path: code}})
        if not gen:
            return {"ok": False, "error": "no candidates generated (model unreachable?) — fall back"}
        candidates = gen

    return select_from_candidates(candidates, tests, language, base_files)


def status() -> dict[str, Any]:
    return {
        "generation_available": bool(VLLM_BASE_URL),
        "model": SEARCH_MODEL if VLLM_BASE_URL else None,
        "default_n": DEFAULT_N,
        "max_n": MAX_N,
        "verify_endpoint": f"http://{VERIFY_HOST}:{VERIFY_PORT}/mcp",
        "note": "selector (candidates supplied) is always available; generation needs $VLLM_BASE_URL. "
                "Use on HARD subtasks only — best-of-N competes for the one your inference host GPU.",
    }
