"""db_update.py — fetch + cache advisory data from the OSV feeds (mcp-security).

Run manually or via a weekly timer:  python3 db_update.py
Stdlib-only (urllib + zipfile) — no extra dependency. Filters MAL-* malware entries and
CRITICAL/HIGH severities, writes a compact index keyed by 'ecosystem:package' to the advisory
DB. Best-effort: a feed that fails is skipped; absence of the DB degrades check_package to WARN.
"""
from __future__ import annotations

import io
import json
import time
import urllib.request
import zipfile

from advisory_db import _DB_PATH, _normalize

_OSV_FEEDS = {
    "pypi": "https://osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip",
    "npm":  "https://osv-vulnerabilities.storage.googleapis.com/npm/all.zip",
}


def update() -> dict:
    db: dict[str, list] = {}
    for ecosystem, url in _OSV_FEEDS.items():
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                content = resp.read()
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for name in z.namelist():
                    try:
                        entry = json.loads(z.read(name))
                    except Exception:  # noqa: BLE001
                        continue
                    severity = str(entry.get("database_specific", {}).get("severity", ""))
                    aliases = [entry.get("id", "")] + entry.get("aliases", [])
                    is_mal = any(str(a).startswith("MAL-") for a in aliases)
                    is_crit = severity.upper() in ("CRITICAL", "HIGH")
                    if not (is_mal or is_crit):
                        continue
                    for pkg in entry.get("affected", []):
                        pname = pkg.get("package", {}).get("name", "")
                        if not pname:
                            continue
                        db.setdefault(f"{ecosystem}:{_normalize(pname)}", []).append({
                            "id": entry.get("id", ""),
                            "severity": "MAL" if is_mal else severity,
                            "summary": (entry.get("summary", "") or "")[:120],
                        })
        except Exception as e:  # noqa: BLE001
            print(f"[mcp-security] failed to fetch {ecosystem}: {e}")

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DB_PATH.write_text(json.dumps(
        {"_meta": {"updated_at": time.time(), "ecosystems": list(_OSV_FEEDS)}, "advisories": db}),
        encoding="utf-8")
    n = sum(len(v) for v in db.values())
    print(f"[mcp-security] DB updated: {n} entries across {len(db)} packages")
    return {"ok": True, "entries": n, "packages": len(db)}


if __name__ == "__main__":
    update()
