"""regression_core.py — Phase 6: the compounding regression corpus.

Every counterexample / seeded bug / rejected wrong-spec the verify + formal-ladder MCPs
surface is auto-promoted into a growing, DEDUPED regression suite — so each run makes future
runs cheaper and stronger (the system's compounding moat). When the counterexample carries an
executable property/contract it is written as a test_*.py guard; otherwise the structured
record (target + trace) is kept so the bug is never silently re-introduced.

Feeds the eval's seeded-bug catch table and (via the shared outcome log) the bandit. Enforced
from the conductor on a found counterexample — the executor is structurally biased NOT to
volunteer this, and the consequence of skipping is degraded future runs. Cheap, deterministic,
never raises.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional


def _root() -> Path:
    return Path(os.path.expanduser(os.environ.get("REGRESSION_DIR", "~/.hermes-max/regression")))


def _corpus_path() -> Path:
    return _root() / "corpus.jsonl"


def _dedup_key(target: str, kind: str, trace: str, test_code: str) -> str:
    """Stable key so the same counterexample is promoted ONCE. Normalizes whitespace/digits in
    the trace so cosmetically-different reports of the same bug collapse."""
    norm = re.sub(r"\s+", " ", (test_code or trace or "")).strip().lower()
    norm = re.sub(r"0x[0-9a-f]+|\b\d+\b", "#", norm)  # addresses/line numbers/counts → '#'
    return hashlib.sha1(f"{target}|{kind}|{norm}".encode()).hexdigest()[:16]


def _seen_keys() -> set[str]:
    keys: set[str] = set()
    try:
        with open(_corpus_path()) as f:
            for ln in f:
                try:
                    keys.add(json.loads(ln).get("key", ""))
                except ValueError:
                    continue
    except OSError:
        pass
    return keys


def promote(trace: str, task_class: str = "", target: str = "", language: str = "python",
            kind: str = "counterexample", test_code: str = "",
            failing_input: Any = None, ts: Optional[float] = None) -> dict[str, Any]:
    """Promote one counterexample into the corpus, DEDUPED. If `test_code` is supplied (e.g.
    the mutation-surviving property that caught it) it is written as a regression test guard.
    Returns {ok, added, key, test_path?}. Idempotent on the dedup key."""
    key = _dedup_key(target, kind, trace or "", test_code or "")
    if key in _seen_keys():
        return {"ok": True, "added": False, "key": key, "reason": "already in corpus (deduped)"}
    rec = {"ts": ts if ts is not None else time.time(), "key": key, "task_class": task_class,
           "target": target, "language": language, "kind": kind,
           "trace": (trace or "")[:1200], "failing_input": failing_input,
           "has_test": bool(test_code)}
    test_path = None
    try:
        _root().mkdir(parents=True, exist_ok=True)
        if test_code:
            suite = _root() / "suite" / (task_class or "general")
            suite.mkdir(parents=True, exist_ok=True)
            test_path = str(suite / f"test_regression_{key}.py")
            Path(test_path).write_text(test_code)
            rec["test_path"] = test_path
        with open(_corpus_path(), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        return {"ok": False, "added": False, "key": key, "error": str(e)}
    return {"ok": True, "added": True, "key": key, "test_path": test_path}


def promote_from_result(result: dict[str, Any], task_class: str = "",
                        target: str = "") -> dict[str, Any]:
    """Convenience: promote from a verify_formal four-value result if it is a counterexample
    (or a spec_rejected carrying surviving examples). No-op for verified/unknown."""
    if not isinstance(result, dict):
        return {"ok": True, "added": False, "reason": "not a result dict"}
    kind = result.get("result")
    if kind == "counterexample":
        return promote(str(result.get("trace", "")), task_class,
                       target or str(result.get("path", "")), result.get("language", "python"),
                       kind=f"counterexample:{result.get('stage') or result.get('method') or ''}",
                       failing_input=result.get("input"))
    if kind == "spec_rejected" and result.get("surviving_examples"):
        return promote("survived mutation: " + "; ".join(map(str, result["surviving_examples"][:5])),
                       task_class, target, kind="rejected-spec")
    return {"ok": True, "added": False, "reason": f"result '{kind}' is not promotable"}


def corpus(task_class: Optional[str] = None) -> dict[str, Any]:
    """List the regression corpus (optionally filtered by task class)."""
    rows: list[dict[str, Any]] = []
    try:
        with open(_corpus_path()) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if not task_class or r.get("task_class") == task_class:
                    rows.append(r)
    except OSError:
        pass
    return {"count": len(rows), "with_tests": sum(1 for r in rows if r.get("has_test")),
            "entries": rows[-50:]}


def seeded_bug_table() -> dict[str, Any]:
    """Roll up the corpus by task class + kind — feeds the eval's seeded-bug catch table."""
    by_class: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    total = 0
    try:
        with open(_corpus_path()) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                total += 1
                by_class[r.get("task_class", "?")] = by_class.get(r.get("task_class", "?"), 0) + 1
                k = r.get("kind", "?").split(":")[0]
                by_kind[k] = by_kind.get(k, 0) + 1
    except OSError:
        pass
    return {"total": total, "by_task_class": by_class, "by_kind": by_kind}


def regression_stats() -> dict[str, Any]:
    return {"dir": str(_root()), "corpus": str(_corpus_path()), **seeded_bug_table()}
