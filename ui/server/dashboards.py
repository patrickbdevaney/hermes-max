"""Phase 6 dashboard data — MCP service health + run state-file inspection.

Both are honest, stdlib-only probes: service health is a real TCP connect to the
known MCP port range; the state inspector reads the agent's on-disk state files
from a working directory (the verify-parse ground truth made visible). No new pip
deps, no fabricated metrics.
"""
from __future__ import annotations

import json
import os
import socket
import time
from typing import Any

# The MCP layer's port range (orchestration phases: ~14 servers, 9101–9115).
MCP_PORTS = range(9101, 9116)


def services_health(timeout: float = 0.15) -> dict[str, Any]:
    """Best-effort liveness for each MCP port: a real loopback TCP connect."""
    out: list[dict[str, Any]] = []
    for port in MCP_PORTS:
        t0 = time.monotonic()
        ok = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                ok = s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            ok = False
        out.append({
            "port": port,
            "open": ok,
            "latency_ms": round((time.monotonic() - t0) * 1000, 1) if ok else None,
        })
    return {"services": out, "up": sum(1 for s in out if s["open"]), "total": len(out)}


# Files the agent maintains on disk; the inspector surfaces them live.
_STATE_FILES = [
    "EXECUTION_STATE.json",
    ".hermes-conductor/state.json",
    ".hermes.md",
    "PLAN.md",
]


def read_state(cwd: str | None) -> dict[str, Any]:
    base = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    files: list[dict[str, Any]] = []
    for rel in _STATE_FILES:
        path = os.path.join(base, rel)
        entry: dict[str, Any] = {"name": rel, "path": path, "exists": False}
        try:
            with open(path) as f:
                text = f.read()
            entry["exists"] = True
            entry["size"] = len(text)
            if rel.endswith(".json"):
                try:
                    entry["json"] = json.loads(text)
                except ValueError:
                    entry["content"] = text[:20000]
            else:
                entry["content"] = text[:20000]
        except FileNotFoundError:
            pass
        except OSError as e:
            entry["error"] = str(e)
        files.append(entry)
    return {"cwd": base, "files": files}
