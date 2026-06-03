"""criticality.py — Part A Phase 2: the criticality classifier (shared by the
verification router AND, later, the research entry gate).

A module is CRITICAL iff it is PURE/deterministic AND high-blast-radius. Only such
modules earn the heavy rungs (Kani / SMT contracts) — running them on everything would
let solver wall-clock dominate. Deterministic keyword/AST rules decide when they fire
(sovereign-first); a cheap-LLM fallback is consulted ONLY when the rules are silent, and
that degrades to non-critical when no model is reachable (never a false `critical` that
burns the solver budget on nothing).

Dimensions of high blast-radius (any present → high blast):
  money/ledger · memory/unsafe · auth/credentials/permissions · data-integrity/persistence
  · termination (loops/recursion)
Purity signals (their ABSENCE keeps a module pure): network/io/random/time/env/input.
Concurrency signals are tracked separately — Kani has NO concurrency support, so the
router must NOT send concurrent code there (Phase 4 handles concurrency via Loom/Shuttle).

Returns {critical: bool, dimensions: [...], pure: bool, concurrent: bool, method}.
Never raises.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

try:
    import enhanced_verify as _ev  # cheap-LLM fallback (_llm) + JSON helpers
except Exception:  # noqa: BLE001
    _ev = None  # type: ignore
try:
    import pool as _pool
except Exception:  # noqa: BLE001
    _pool = None  # type: ignore

# ── signal lexicons (substring match, case-insensitive) ───────────────────────
_BLAST = {
    "money": ("balance", "ledger", "transfer", "payment", "invoice", "amount", "currency",
              "decimal", "satoshi", " wei", "txn", "debit", "credit", "settle"),
    "memory": ("unsafe", "ctypes", "mmap", "memcpy", "*mut ", "*const ", "raw pointer",
               "malloc", "free(", "transmute"),
    "auth": ("auth", "password", "passwd", "credential", "permission", "secret", "login",
             " jwt", "hmac", "signature", "oauth", "session_token", "api_key", "verify_"),
    "data_integrity": ("commit(", "persist", "migrate", "schema", "transaction", "rollback",
                       " sql", "execute(", "insert into", "update ", "delete from", "fsync"),
    "termination": (),  # detected structurally below (loops + recursion)
}
_CRYPTO = ("crypto", "hashlib", "encrypt", "decrypt", "cipher", "nonce", "aes", "rsa", "sha256")
_IMPURE = ("requests", "httpx", "urllib", "socket", "open(", "random", "time.time", "datetime.now",
           "os.environ", "input(", "subprocess", "fetch(", "reqwest", "std::fs", "std::net",
           "rand::", "system_time")
_CONCURRENT = ("threading", "asyncio", "async def", "await ", "atomic", "mutex", "arc<", "rwlock",
               "spawn", "channel", "tokio", "rayon", "lock()", "join(", "goroutine", " go ")


def _read(path_or_src: str) -> tuple[str, str]:
    """Return (src, name). Accepts a file path or raw source text."""
    p = Path(path_or_src)
    try:
        if p.exists() and p.is_file():
            return p.read_text(errors="replace"), p.stem
    except OSError:
        pass
    return path_or_src, "module"


def _has_loop_or_recursion(src: str, language: str) -> bool:
    if language == "python":
        try:
            tree = ast.parse(src)
        except Exception:  # noqa: BLE001
            return bool(re.search(r"\b(for|while)\b", src))
        if any(isinstance(n, (ast.For, ast.While, ast.AsyncFor)) for n in ast.walk(tree)):
            return True
        # direct self-recursion
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == fn.name
                   for c in ast.walk(fn)):
                return True
        return False
    # other languages: cheap lexical check (loop/recursion keywords)
    return bool(re.search(r"\b(for|while|loop)\b", src))


def criticality_classify(path_or_src: str, language: str = "auto") -> dict[str, Any]:
    """Classify a module's verification criticality. Deterministic rules win; the cheap-LLM
    fallback runs ONLY when no rule fires, and degrades to non-critical without a model."""
    src, name = _read(path_or_src)
    low = src.lower()
    if language == "auto":
        try:
            import verify_core
            language = verify_core.detect_language(path_or_src)
        except Exception:  # noqa: BLE001
            language = "python"

    dims: list[str] = []
    for dim, kws in _BLAST.items():
        if dim == "termination":
            continue
        if any(k in low for k in kws):
            dims.append(dim)
    if any(k in low for k in _CRYPTO):
        if "crypto" not in dims:
            dims.append("crypto")
    if _has_loop_or_recursion(src, language):
        dims.append("termination")

    concurrent = any(k in low for k in _CONCURRENT)
    impure = any(k in low for k in _IMPURE)
    pure = not impure
    high_blast = bool([d for d in dims if d != "termination"]) or "termination" in dims

    # A clear signal → deterministic verdict (rules win, sovereign-first).
    if dims:
        critical = high_blast and pure
        return {"critical": critical, "dimensions": dims, "pure": pure,
                "concurrent": concurrent, "method": "rules",
                "reason": ("pure + high-blast" if critical else
                           ("high-blast but impure → contract/runtime-monitor, not proof"
                            if high_blast and not pure else "low blast-radius"))}

    # No rule fired → ambiguous. Consult the cheap LLM if one is reachable; else not-critical.
    verdict = _llm_classify(name, src)
    if verdict is not None:
        return {**verdict, "concurrent": concurrent, "pure": pure, "method": "llm"}
    return {"critical": False, "dimensions": [], "pure": pure, "concurrent": concurrent,
            "method": "rules", "reason": "no blast-radius signal; no model for fallback"}


_CLASSIFY_SYS = (
    "Classify a code module's VERIFICATION criticality. CRITICAL means PURE/deterministic "
    "AND high blast-radius (money/ledger, memory/unsafe, auth/credentials, data integrity, "
    "or termination). Return STRICT JSON: {\"critical\": bool, \"dimensions\": [..]}. No prose."
)


def _llm_classify(name: str, src: str) -> dict[str, Any] | None:
    prompt = f"Module `{name}`:\n\n{src[:4000]}"
    out = None
    if _pool and _pool.available():
        r = _pool.map_cheap([prompt], system=_CLASSIFY_SYS, temperature=0, max_tokens=200)
        out = r[0] if r else None
    elif _ev is not None:
        out = _ev._llm(_CLASSIFY_SYS + "\n\n" + prompt, 200)
    if not out:
        return None
    try:
        import json
        m = re.search(r"\{.*\}", out, re.DOTALL)
        d = json.loads(m.group(0)) if m else {}
        if isinstance(d, dict) and "critical" in d:
            return {"critical": bool(d["critical"]),
                    "dimensions": [str(x) for x in (d.get("dimensions") or [])][:6],
                    "reason": "llm fallback"}
    except Exception:  # noqa: BLE001
        return None
    return None
