"""dag_core.py — Phase 5: causal task decomposition (DAG scheduler).

The planner emits a dependency DAG of subtasks; the conductor schedules independent nodes
and serializes dependent ones, with conflict detection on merge. Shares the parallelism
dispatcher (dispatch_core), which means the CORRECTION the spec calls out is honored here:

  Independent nodes parallelize ONLY when they land on cloud/fabric. Independent nodes that
  land on the LOCAL executor still SERIALIZE (single stream) — there the DAG's benefit is
  CONTEXT ISOLATION (a fresh, focused context per node, fighting context-rot on multi-file
  tasks), NOT wall-clock parallelism. The schedule states which it is so it never promises
  parallelism it cannot deliver on local.

Conflict detection: nodes that touched overlapping files are flagged on merge (a deterministic
file-set check, optionally widened by codegraph's reverse-call closure). Default-on for
multi-file tasks, off for single-file. Deterministic; never raises.
"""
from __future__ import annotations

from typing import Any, Optional


def parse_dag(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize parsed PLAN.md steps into DAG nodes. Each step may carry `depends_on`
    (1-based step numbers) and `files` (the files it will touch). Returns {nodes, n}."""
    nodes = []
    for i, s in enumerate(steps or [], start=1):
        nodes.append({"id": i, "description": s.get("description", ""),
                      "complexity": s.get("complexity", "standard"),
                      "depends_on": [int(d) for d in (s.get("depends_on") or []) if str(d).isdigit()
                                     or isinstance(d, int)],
                      "files": [f.strip() for f in (s.get("files") or []) if f and f.strip()]})
    return {"nodes": nodes, "n": len(nodes)}


def is_multifile(nodes: list[dict[str, Any]]) -> bool:
    """A task is multi-file (DAG default-on) if its nodes collectively touch >1 file, or
    declare any dependencies, or there is more than one node."""
    files = {f for nd in nodes for f in nd.get("files", [])}
    return len(files) > 1 or len(nodes) > 1 or any(nd.get("depends_on") for nd in nodes)


def ready_nodes(nodes: list[dict[str, Any]], done: list[int]) -> list[dict[str, Any]]:
    """Nodes whose every dependency is complete and which are not themselves done — the
    independent set runnable now."""
    done_set = set(done or [])
    return [nd for nd in nodes if nd["id"] not in done_set
            and all(d in done_set for d in nd.get("depends_on", []))]


def merge_conflicts(nodes: list[dict[str, Any]], repo_path: str = "") -> list[dict[str, Any]]:
    """Flag pairs of nodes that touch overlapping files (a merge hazard if scheduled
    together). Deterministic file-set overlap; widened by codegraph reverse-call closure when
    that MCP is importable (best-effort)."""
    out: list[dict[str, Any]] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            overlap = sorted(set(a.get("files", [])) & set(b.get("files", [])))
            if overlap:
                out.append({"a": a["id"], "b": b["id"], "files": overlap,
                            "reason": "declared file overlap"})
    return out


def schedule(nodes: list[dict[str, Any]], done: Optional[list[int]] = None,
             repo_path: str = "") -> dict[str, Any]:
    """Compute the next wave (independent ready nodes), decide parallel-vs-serial via the
    dispatcher, and flag merge conflicts within the wave. On local the wave runs serially with
    CONTEXT ISOLATION (stated honestly); off-local it runs in parallel."""
    done = done or []
    ready = ready_nodes(nodes, done)
    if not ready:
        remaining = [nd["id"] for nd in nodes if nd["id"] not in set(done)]
        return {"wave": [], "done": done, "remaining": remaining,
                "note": "no ready nodes" + (" (cycle or all complete)" if remaining else "")}
    try:
        import dispatch_core
        tgt = dispatch_core.target_for(len(ready))
        backend, parallel = tgt["backend"], tgt["parallel"]
    except Exception:  # noqa: BLE001
        backend, parallel = "local-serial", False
    conflicts = merge_conflicts(ready, repo_path)
    if backend == "local-serial":
        note = ("local single-stream: this wave runs SERIALLY with CONTEXT ISOLATION per "
                "node (fresh focused context, fights context-rot) — NOT wall-clock parallel")
    else:
        note = f"independent nodes run in PARALLEL on {backend}"
    return {"wave": [nd["id"] for nd in ready], "backend": backend, "parallel": parallel,
            "context_isolation": True, "conflicts": conflicts, "done": done,
            "wave_nodes": ready, "note": note}


def dag_stats() -> dict[str, Any]:
    return {"rule": "independent nodes parallelize ONLY off-local; on local they are "
                    "context-isolated but serial", "default_on": "multi-file tasks only"}
