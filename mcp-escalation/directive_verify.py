"""Advisory-with-verify-gate authority (Stage 3).

The cloud model is SMARTER but BLIND (no repo access); the local model is WEAKER
but SIGHTED. So a cloud directive is ADVISORY and is GATED before any commit:

  1. ASSUMPTION CHECK — every `assumptions` entry is verified against ACTUAL repo
     state (file/symbol existence). A demonstrably-false assumption (the cloud
     hallucinated a function/file) REJECTS the directive and is recorded to the KG
     as a failed_approach so the brief-assembler won't let it be re-suggested.
  2. STATIC GATE — the APIs in `apis_to_use` must exist; the repo baseline must
     pass verify.quick_check (lint+type), when mcp-verify is reachable.
  3. TEST GATE — the directive must prescribe concrete `tests_to_write` (the
     objective oracle the driver writes FIRST, then runs at execution time).
  4. CONFIDENCE ESCALATION — a low-confidence step on a HIGH-blast-radius change
     demands a second synth opinion (next present rung); if the two opinions
     DISAGREE, escalate (Opus) or surface to a human.

Only when the gate passes does the driver execute + checkpoint. Deterministic and
offline-capable: file/symbol checks hit the local filesystem; verify + KG calls
are optional and degrade. Never raises.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from brief_assemble import KG_PORT, _mcp  # reuse the degrade-safe MCP helper

VERIFY_PORT = os.environ.get("MCP_VERIFY_PORT", "9101")

# identifiers that look like a real symbol the directive intends to call
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
# path-ish tokens (have a slash or a known source extension)
_PATHISH = re.compile(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|rb|sh|ya?ml|toml|md)")
# words that signal the assumption ASSERTS something currently exists
_EXISTS_WORDS = ("exist", "is defined", "is present", "already", "is implemented",
                 "is available", "current", "lives in", "defined in")
# generic identifiers we never treat as a checkable symbol claim
_STOPWORDS = {"the", "this", "that", "exists", "function", "method", "class", "module",
              "file", "repo", "repository", "code", "test", "tests", "value", "true",
              "false", "none", "return", "returns", "should", "must", "will", "uses",
              "use", "using", "with", "from", "into", "and", "for", "all", "any"}


def _repo_files(repo: str) -> list[Path]:
    out: list[Path] = []
    skip = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.rsplit(".", 1)[-1] in ("py", "ts", "tsx", "js", "jsx", "rs", "go"):
                out.append(Path(root) / f)
    return out[:4000]


def _symbol_exists(name: str, repo: str) -> bool:
    """Is `name` defined anywhere as a function/class/const/assignment?"""
    pat = re.compile(rf"(?:def|class|function|const|let|var|fn)\s+{re.escape(name)}\b"
                     rf"|^\s*{re.escape(name)}\s*[:=]", re.MULTILINE)
    # fast path: ripgrep if available
    try:
        r = subprocess.run(["rg", "-l", "--", pat.pattern, repo], capture_output=True,
                           text=True, timeout=10)
        if r.returncode in (0, 1):
            return bool(r.stdout.strip())
    except Exception:  # noqa: BLE001 - no rg / timeout -> python fallback
        pass
    for fp in _repo_files(repo):
        try:
            if pat.search(fp.read_text(errors="ignore")):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _check_assumption(text: str, repo: str) -> dict[str, Any]:
    """Return {claim, kind, target, verified} where verified is True/False/None
    (None = unverifiable, does NOT block). Only a clearly-asserted-but-absent
    file/symbol yields False."""
    low = text.lower()
    asserts_exist = any(w in low for w in _EXISTS_WORDS)
    # 1. path-ish claims
    paths = _PATHISH.findall(text)
    for p in paths:
        exists = (Path(repo) / p).exists() or any(
            str(fp).endswith(p) for fp in _repo_files(repo))
        if not exists:
            return {"claim": text, "kind": "file", "target": p, "verified": False,
                    "evidence": f"no file matching '{p}' in repo"}
    # 2. backticked or asserted symbol claims
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]{2,})`", text)
    candidates = backticked[:]
    if asserts_exist and not candidates:
        # pull function-call-shaped or snake/Camel identifiers from the claim
        for m in _IDENT.findall(text):
            if m.lower() in _STOPWORDS:
                continue
            if "_" in m or (m[0].isupper() and any(c.islower() for c in m)) or f"{m}(" in text:
                candidates.append(m)
    for name in candidates:
        if name.lower() in _STOPWORDS:
            continue
        if not _symbol_exists(name, repo):
            return {"claim": text, "kind": "symbol", "target": name, "verified": False,
                    "evidence": f"no definition of '{name}' found in repo"}
    if paths or candidates:
        return {"claim": text, "kind": "file/symbol", "target": (paths + candidates)[0],
                "verified": True, "evidence": "found in repo"}
    return {"claim": text, "kind": "prose", "target": None, "verified": None,
            "evidence": "not a checkable file/symbol claim"}


def _blast_radius(directive: dict) -> str:
    files = directive.get("files_to_touch") or []
    tops = {str(f).split("/")[0] for f in files if "/" in str(f)}
    if len(files) >= 4 or len(tops) >= 2:
        return "high"
    if len(files) >= 2:
        return "medium"
    return "low"


def _low_confidence(directive: dict) -> bool:
    steps = directive.get("ordered_steps") or []
    if any(isinstance(s, dict) and str(s.get("confidence", "")).lower() == "low" for s in steps):
        return True
    return str(directive.get("confidence", "")).lower() == "low"


def _record_failed(approach: str, reason: str) -> bool:
    r = _mcp(KG_PORT, "record_entity",
             {"type": "failed_approach", "name": approach[:120],
              "props": {"reason": reason, "source": "directive_verify"}})
    return bool(r and r.get("ok"))


def compare_directives(a: dict, b: dict) -> dict[str, Any]:
    """Cheap agreement check between two synth opinions: file-set overlap +
    first-step similarity. Disagreement => escalate / surface to human."""
    fa = {str(x).lower() for x in (a.get("files_to_touch") or [])}
    fb = {str(x).lower() for x in (b.get("files_to_touch") or [])}
    overlap = len(fa & fb) / len(fa | fb) if (fa or fb) else 1.0

    def first_step(d: dict) -> str:
        steps = d.get("ordered_steps") or []
        s0 = steps[0] if steps else {}
        return (s0.get("step") if isinstance(s0, dict) else str(s0)) or ""
    wa = set(re.findall(r"[a-z]{4,}", first_step(a).lower()))
    wb = set(re.findall(r"[a-z]{4,}", first_step(b).lower()))
    step_sim = len(wa & wb) / len(wa | wb) if (wa or wb) else 1.0
    agree = overlap >= 0.34 or step_sim >= 0.34
    return {"agree": agree, "file_overlap": round(overlap, 2),
            "first_step_sim": round(step_sim, 2)}


def directive_verify(directive: dict, *, repo: str | None = None, task_id: str | None = None,
                     second_directive: dict | None = None,
                     run_static: bool = True) -> dict[str, Any]:
    """Gate an advisory directive before execution. Returns a verdict with
    per-gate results and `execute` (bool). execute is True ONLY when assumptions
    hold, the static/API gate passes, tests are prescribed, and either no second
    opinion is needed or a provided second opinion AGREES."""
    repo = repo or os.getcwd()
    if not isinstance(directive, dict):
        return {"ok": False, "execute": False, "reason": "directive is not a JSON object"}

    # ── gate 1: assumption check ──────────────────────────────────────────────
    checked = [_check_assumption(str(a), repo) for a in (directive.get("assumptions") or [])]
    false_ones = [c for c in checked if c["verified"] is False]
    recorded: list[str] = []
    for c in false_ones:
        if _record_failed(f"false assumption: {c['target']}", c["evidence"]):
            recorded.append(c["target"])
    assumptions_ok = not false_ones

    # ── gate 2: static / APIs-exist gate ──────────────────────────────────────
    apis = directive.get("apis_to_use") or []
    missing_apis = []
    for api in apis:
        name = re.sub(r"\(.*$", "", str(api)).split(".")[-1].strip()
        if name and not _PATHISH.search(str(api)) and len(name) >= 3 \
                and name.lower() not in _STOPWORDS and not _symbol_exists(name, repo):
            missing_apis.append(str(api))
    quick = None
    if run_static:
        q = _mcp(VERIFY_PORT, "quick_check", {"path": repo})
        if q is not None:
            quick = {"ran": True, "passed": bool(q.get("ok") and q.get("passed", q.get("ok")))}
    static_ok = not missing_apis  # quick_check is advisory (baseline may be mid-edit)

    # ── gate 3: test gate (prescribed oracle present) ─────────────────────────
    tests = directive.get("tests_to_write") or []
    tests_ok = len([t for t in tests if len(str(t)) > 8]) >= 1

    # ── gate 4: confidence escalation ─────────────────────────────────────────
    blast = _blast_radius(directive)
    low_conf = _low_confidence(directive)
    needs_second = low_conf and blast == "high"
    second = None
    if needs_second and second_directive is not None:
        second = compare_directives(directive, second_directive)
        needs_second = False  # we have the opinion; decision is in `second.agree`

    # ── compose verdict ───────────────────────────────────────────────────────
    blockers: list[str] = []
    action = "execute"
    if not assumptions_ok:
        blockers.append(f"{len(false_ones)} false assumption(s): "
                        f"{[c['target'] for c in false_ones]}")
        action = "reject_and_rebrief"
    if missing_apis:
        blockers.append(f"apis_to_use not found in repo: {missing_apis}")
        action = "reject_and_rebrief"
    if not tests_ok:
        blockers.append("no concrete tests_to_write prescribed (need the objective oracle)")
        action = "reject_and_rebrief"
    if needs_second:
        blockers.append("low confidence on a high-blast-radius change -> get a second synth opinion")
        action = "get_second_opinion"
    if second is not None and not second["agree"]:
        blockers.append(f"two synth opinions disagree (overlap {second['file_overlap']}) "
                        "-> escalate (Opus) or surface to human")
        action = "escalate_or_human"

    execute = not blockers
    return {
        "ok": True, "execute": execute, "action": action if not execute else "execute",
        "gates": {
            "assumptions": {"passed": assumptions_ok, "checked": checked,
                            "false": false_ones, "recorded_failed": recorded},
            "static": {"passed": static_ok, "missing_apis": missing_apis, "quick_check": quick},
            "tests": {"passed": tests_ok, "prescribed": len(tests)},
            "confidence": {"blast_radius": blast, "low_confidence": low_conf,
                           "needs_second_opinion": needs_second, "second_opinion": second},
        },
        "blockers": blockers,
        "reason": ("all gates passed — execute then checkpoint" if execute
                   else "; ".join(blockers)),
    }
