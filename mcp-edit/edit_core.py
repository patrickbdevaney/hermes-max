"""edit_core.py — validated edit application (mcp-edit).

Two interchangeable, mechanically-checked edit formats so a small model's botched partial
edits stop silently corrupting files:

  • validated_write — whole-file: rejects any elision marker ('...', '# rest of code',
    '[existing code]', …) so the model must return the COMPLETE file.
  • validated_edit  — SEARCH/REPLACE: the SEARCH anchor must be unique; on an exact miss a
    difflib fuzzy match surfaces the nearest candidate (ratio ≥ 0.85) instead of guessing;
    ambiguous anchors are rejected. Applies atomically and returns a unified diff.

Pure stdlib. cwd-confined via $AGENT_WORK_DIR when set. Additive + presence-gated: the
executor's native write_file / edit_file remain the bare-local fallback. Never raises.
"""
from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import Any

_ELISION = re.compile(
    r"(\.{3}(?!\w)|#\s*rest of code|#\s*\.\.\.|\[existing code\]|#\s*unchanged"
    r"|#\s*omitted|#\s*etc\b|//\s*\.\.\.|//\s*rest|/\*\s*\.\.\.\s*\*/)",
    re.IGNORECASE,
)
_SR_RE = re.compile(r"<{7} SEARCH\n(.*?)\n={7}\n(.*?)\n>{7} REPLACE", re.DOTALL)
_FUZZY_THRESHOLD = 0.85


def _safe_path(path: str) -> Path:
    """Resolve `path`; if $AGENT_WORK_DIR is set, confine the resolved path under it."""
    p = Path(path).expanduser().resolve()
    root = os.environ.get("AGENT_WORK_DIR")
    if root:
        rp = Path(root).expanduser().resolve()
        try:
            p.relative_to(rp)
        except ValueError:
            raise ValueError(f"path escapes AGENT_WORK_DIR confinement: {path}")
    return p


def validated_write(path: str, content: str) -> dict[str, Any]:
    """Format A — write the WHOLE file; reject if an elision marker is present."""
    m = _ELISION.search(content or "")
    if m:
        return {"ok": False, "error": f"Elision detected: {m.group().strip()!r}. Send the "
                "COMPLETE file — no '...' or placeholder gaps standing in for unchanged code."}
    try:
        p = _safe_path(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "path": str(p), "bytes": len(content)}


def validated_edit(path: str, search: str, replace: str) -> dict[str, Any]:
    """Format B — SEARCH/REPLACE with a unique-anchor check and a difflib fuzzy fallback."""
    if not (search or "").strip():
        return {"ok": False, "error": "empty SEARCH block"}
    try:
        p = _safe_path(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not p.exists():
        return {"ok": False, "error": f"File not found: {path}"}

    original = p.read_text(encoding="utf-8", errors="replace")
    count = original.count(search)

    if count == 1:
        updated = original.replace(search, replace, 1)
        try:
            p.write_text(updated, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
        diff = "".join(list(difflib.unified_diff(
            original.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}", n=3))[:120])
        return {"ok": True, "diff": diff}

    if count == 0:
        matcher = difflib.SequenceMatcher(None, search, original)
        ratio = matcher.ratio()
        if ratio >= _FUZZY_THRESHOLD:
            i, j, k = matcher.find_longest_match(0, len(search), 0, len(original))
            start = original.rfind("\n", 0, j) + 1
            end = original.find("\n", j + k)
            end = len(original) if end == -1 else end
            return {"ok": False, "error": "SEARCH block not found exactly.",
                    "fuzzy_ratio": round(ratio, 3), "nearest_candidate": original[start:end][:200],
                    "hint": "Adjust SEARCH to match the nearest candidate exactly (whitespace/indentation)."}
        return {"ok": False, "fuzzy_ratio": round(ratio, 3),
                "error": f"SEARCH block not found (fuzzy ratio {ratio:.2f} < {_FUZZY_THRESHOLD}); "
                "check whitespace, indentation, and exact wording."}

    return {"ok": False, "error": f"SEARCH block appears {count} times — make it more specific "
            "so it matches exactly once."}


def apply_search_replace_blocks(path: str, response_text: str) -> dict[str, Any]:
    """Parse all '<<<<<<< SEARCH / ======= / >>>>>>> REPLACE' blocks from a model response
    and apply them in order, stopping on the first failure (multi-edit case)."""
    blocks = _SR_RE.findall(response_text or "")
    if not blocks:
        return {"ok": False, "error": "no SEARCH/REPLACE blocks found", "applied": 0}
    applied = 0
    for search, replace in blocks:
        r = validated_edit(path, search, replace)
        if not r.get("ok"):
            return {"ok": False, "error": f"block {applied + 1} failed: {r.get('error')}",
                    "applied": applied}
        applied += 1
    return {"ok": True, "applied": applied}
