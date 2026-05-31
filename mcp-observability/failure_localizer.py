"""failure_localizer.py — trajectory-level failure localization + classification
(Phase 6.2). Given a FAILED trajectory, isolate the step where it went wrong and
classify the failure mode, feeding the result back to the trajectory store as the
training signal for the GEPA optimizer. Deterministic-first (heuristics over the
tool-call sequence), with optional model reflection for the root-cause sentence.
Never raises.
"""
from __future__ import annotations

import json
import os
from typing import Any

ESCALATION_URL = os.environ.get("ESCALATION_MCP_URL",
                                f"http://127.0.0.1:{os.environ.get('MCP_ESCALATION_PORT','9105')}/mcp")

# failure mode -> the signals (substrings) that indicate it, checked in priority order
_MODES = [
    ("timeout",              ("timeout", "timed out", "deadline", "ceiling", "gateway_timeout")),
    ("hung",                 ("hung", "stall", "no heartbeat")),
    ("hallucinated_property",("hallucinat", "property")),
    ("wrong_tool",           ("gated", "parametric", "lighter tools not", "use_instead", "wrong tool")),
    ("budget_exhausted",     ("budget", "cooldown", "exhausted")),
    ("verify_red",           ("verify", "test fail", "assertion", "red")),
    ("no_progress",          ("no progress", "no_progress", "stuck", "loop")),
    ("spiral",               ("spiral", "repeat")),
]


def _tool_name(tc: Any) -> str:
    return (tc.get("tool") or tc.get("name") or "") if isinstance(tc, dict) else str(tc)


def _step_failed(tc: Any) -> bool:
    if not isinstance(tc, dict):
        return False
    if tc.get("ok") is False or tc.get("error") or tc.get("gated"):
        return True
    blob = json.dumps(tc).lower()
    return any(s in blob for s in ("error", "timeout", "hung", "killed", "gated", "fail"))


def _classify(traj: dict, blob: str) -> tuple[str, list[str]]:
    # explicit failure_mode wins if it maps to a known mode
    fm = (traj.get("failure_mode") or "").lower()
    sigs: list[str] = []
    for mode, keys in _MODES:
        hit = [k for k in keys if k in fm]
        if hit:
            return mode, hit
    # spiral: a tool repeated many times consecutively
    tools = [_tool_name(tc) for tc in (traj.get("tool_calls") or [])]
    for i in range(len(tools) - 2):
        if tools[i] and tools[i] == tools[i + 1] == tools[i + 2]:
            return "spiral", [f"{tools[i]} x>=3 consecutive"]
    for mode, keys in _MODES:
        hit = [k for k in keys if k in blob]
        if hit:
            return mode, hit
    return "unknown", sigs


def _root_cause(traj: dict, step_idx: int, mode: str) -> str | None:
    prompt = ("Given this FAILED agent trajectory, in ONE sentence state the root cause "
              f"(the failure mode looks like '{mode}', failing around step {step_idx}). "
              "Be specific and actionable.\n\n" + json.dumps(traj, default=str)[:4000])
    async def _go():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        box = {}
        try:
            async with streamablehttp_client(ESCALATION_URL) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("conductor_steer", {"prompt": prompt, "max_tokens": 300})
                    txt = getattr(res.content[0], "text", "") if res.content else ""
                    d = res.structuredContent or (json.loads(txt) if txt else {})
                    box["v"] = d.get("result", d) if isinstance(d, dict) else {}
        except BaseException:  # noqa: BLE001
            if "v" in box:
                return box["v"]
            raise
        return box["v"]
    try:
        import asyncio
        d = asyncio.run(asyncio.wait_for(_go(), timeout=45))
        if isinstance(d, dict) and not d.get("proceed_local") and d.get("content"):
            return str(d["content"]).strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def localize(trajectory: dict, reflect: bool = True) -> dict[str, Any]:
    """Isolate the failing step and classify the failure mode. Returns
    {failing_step_index, failing_tool, failure_mode, signals, root_cause}."""
    tcs = trajectory.get("tool_calls") or []
    blob = json.dumps(trajectory, default=str).lower()
    # failing step: first tool call that shows an error, else the last step
    fail_idx = next((i for i, tc in enumerate(tcs) if _step_failed(tc)), None)
    if fail_idx is None:
        fail_idx = len(tcs) - 1 if tcs else -1
    mode, sigs = _classify(trajectory, blob)
    root = _root_cause(trajectory, fail_idx, mode) if reflect else None
    return {"ok": True,
            "failing_step_index": fail_idx,
            "failing_tool": _tool_name(tcs[fail_idx]) if 0 <= fail_idx < len(tcs) else None,
            "failure_mode": mode, "signals": sigs,
            "root_cause": root,
            "total_steps": len(tcs)}
