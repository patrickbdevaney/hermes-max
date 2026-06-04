"""advisory_db.py — local supply-chain advisory store (mcp-security).

A JSON file (refreshed by db_update.py from the open OSV feeds) keyed by 'ecosystem:package'.
check_package fails CLOSED on confirmed-malware (MAL-*) advisories and otherwise WARNs; if the
DB is absent or stale it degrades to WARN (never hard-blocks a first run with no DB). Sovereign
+ offline: the check is a local lookup, no per-install network call. Never raises.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

_DB_PATH = Path(os.path.expanduser(os.environ.get("HM_ADVISORY_DB", "~/.hermes/advisory_db.json")))
_STALE_SECS = int(os.environ.get("HM_ADVISORY_STALE_SECS", str(7 * 24 * 3600)))  # 7 days
_BLOCK_SEVERITY = set(os.environ.get("HM_SECURITY_BLOCK_SEVERITY", "MAL").upper().split(","))


def _normalize(name: str) -> str:
    """pip/npm canonical-ish form: collapse - _ . runs, lowercase."""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def _load_db() -> dict:
    if not _DB_PATH.exists():
        return {}
    try:
        return json.loads(_DB_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def check_package(ecosystem: str, name: str) -> dict:
    """Check a package against the local advisory DB. Returns
    {safe, action ∈ allow|warn|block, advisories, reason}. action='block' ONLY when an
    advisory's severity prefix is in HM_SECURITY_BLOCK_SEVERITY (default MAL); a stale/absent
    DB → 'warn'; clean → 'allow'."""
    db = _load_db()
    meta = db.get("_meta", {})
    stale = (time.time() - meta.get("updated_at", 0)) > _STALE_SECS
    key = f"{(ecosystem or '').lower()}:{_normalize(name)}"
    entries = db.get("advisories", {}).get(key, [])

    if not entries:
        reason = "No advisories found"
        if stale:
            reason += " (advisory DB absent/stale — run mcp-security db_update to refresh)"
        return {"safe": True, "action": "warn" if stale else "allow", "advisories": [], "reason": reason}

    blocking = [e for e in entries
                if any(str(e.get("severity", "")).upper().startswith(s) or
                       str(e.get("id", "")).upper().startswith(s) for s in _BLOCK_SEVERITY)]
    if blocking:
        return {"safe": False, "action": "block", "advisories": blocking,
                "reason": f"BLOCKED: {len(blocking)} malware advisory/ies — "
                          + ", ".join(e.get("id", "?") for e in blocking[:3])}
    non_blocking = [e for e in entries if e not in blocking]
    return {"safe": True, "action": "warn", "advisories": non_blocking,
            "reason": f"WARN: {len(non_blocking)} non-malware advisory/ies — review before proceeding"}
