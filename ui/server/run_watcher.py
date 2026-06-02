"""RunWatcher — discovers hermes runs from the registry (Fix 4: universal SSE).

Any hermes instance (launched here, in `hm dev`, or bare in a terminal via the shell
wrapper) drops a descriptor in ~/.hermes-max/runs/. This module is the discovery seam:
`snapshot()` reads the registry and returns the current run list. Discovery is
poll-based — the `/api/runs` endpoint calls this and the browser polls it, so a
terminal run appears in the UI within a poll interval (~1s). No inotify dependency,
no background thread, no shared mutable state: a missing/locked descriptor is simply
skipped, never crashes the server.
"""
from __future__ import annotations

from typing import Any

from . import runs


def snapshot(limit: int = 50) -> list[dict[str, Any]]:
    """The current set of known runs (registry ∪ in-memory launches), newest first."""
    return runs.list_runs(limit=limit)


class RunWatcher:
    """Thin poll-based view over the run registry. Held by the HTTP handler so the
    discovery seam has a name/owner; `snapshot()` is the only operation."""

    def snapshot(self, limit: int = 50) -> list[dict[str, Any]]:
        return snapshot(limit)

    def active_count(self) -> int:
        return sum(1 for r in snapshot() if r.get("active"))
