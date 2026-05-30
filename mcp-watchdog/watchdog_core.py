"""Deterministic self-check signals for the agent — the missing NON-turn-based
detection layer.

The two field-observed failures (a CoT/thinking spiral and a server-poll hang)
were both *single unbounded operations within one turn*, so Hermes' turn-based
`tool_loop_guardrails` never saw them. This server provides deterministic,
model-free signals the workflow skills call to self-check WITHIN a turn:

  * check_spiral  — n-gram repetition + LZ-compressibility + consecutive-segment
                    similarity on recent reasoning text (the CoT-spiral detector).
  * check_stall   — distinguishes a HUNG tool call from one that is legitimately
                    waiting/heartbeating (the OpenHands #5355 false-kill trap:
                    NEVER report hung if the process is still producing output).
  * check_progress— progress-delta across calls; flags no_progress when files /
                    tests / checkpoints all stall over N calls.
  * start_task_budget / check_budget — per-task wall-clock / turns / USD budget
                    (the part Hermes config has no native knob for).

No model calls, no randomness (apart from wall-clock time, which the budget API
exposes an override for so the smoke test is deterministic). Every signal is a
pure function of its inputs + a small per-task state file. If this server is
down, the skills simply skip the self-check and the agent keeps working on
Hermes' native turn-based guardrails alone — graceful degradation.
"""

from __future__ import annotations

import json
import os
import threading
import time
import zlib
from pathlib import Path
from typing import Any

import otel_emit

STATE_DIR = Path(os.path.expanduser(
    os.environ.get("WATCHDOG_STATE_DIR", "~/.hermes-max/watchdog")
))

# Default per-tool wall-clock budget (s). Mirrors the lowered native
# terminal.timeout; a single tool call exceeding this WITHOUT a heartbeat is hung.
TOOL_BUDGET_S = float(os.environ.get("WATCHDOG_TOOL_BUDGET_S", "120"))

# Spiral thresholds (env-overridable). A spiral trips if ANY fires.
SPIRAL_NGRAM = int(os.environ.get("WATCHDOG_SPIRAL_NGRAM", "4"))
SPIRAL_DUP_RATIO = float(os.environ.get("WATCHDOG_SPIRAL_DUP_RATIO", "0.45"))
SPIRAL_TOP_FREQ = float(os.environ.get("WATCHDOG_SPIRAL_TOP_FREQ", "0.12"))
SPIRAL_COMPRESS = float(os.environ.get("WATCHDOG_SPIRAL_COMPRESS", "0.32"))
SPIRAL_SEG_SIM = float(os.environ.get("WATCHDOG_SPIRAL_SEG_SIM", "0.80"))

_lock = threading.Lock()


# ── per-task state ───────────────────────────────────────────────────────────
def _state_path(task_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (task_id or "default"))
    return STATE_DIR / f"{safe}.json"


def _load(task_id: str) -> dict[str, Any]:
    try:
        with open(_state_path(task_id)) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        return {}


def _save(task_id: str, st: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _state_path(task_id)
    tmp = str(p) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, p)


# ── check_spiral ─────────────────────────────────────────────────────────────
def check_spiral(recent_thinking_text: str, ngram: int | None = None) -> dict[str, Any]:
    """Detect a reasoning spiral in supplied recent reasoning text.

    Combines three model-free signals: repeated n-gram ratio, LZ (zlib)
    compressibility, and average Jaccard similarity of consecutive segments.
    Returns spiral_detected + reason + the raw metrics so the skill can log why.
    """
    text = (recent_thinking_text or "").strip()
    n = ngram or SPIRAL_NGRAM
    words = text.split()
    metrics: dict[str, Any] = {
        "words": len(words),
        "dup_ngram_ratio": 0.0,
        "top_ngram_freq": 0.0,
        "compress_ratio": 1.0,
        "consecutive_seg_sim": 0.0,
    }
    # Too little text to judge — never a spiral.
    if len(words) < max(2 * n, 12):
        return {"ok": True, "spiral_detected": False, "reason": "insufficient text to judge",
                "metrics": metrics}

    # 1. repeated n-gram ratio + top n-gram frequency
    grams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
    total = len(grams)
    counts: dict[tuple, int] = {}
    for g in grams:
        counts[g] = counts.get(g, 0) + 1
    unique = len(counts)
    dup_ratio = 1.0 - (unique / total) if total else 0.0
    top_freq = (max(counts.values()) / total) if total else 0.0

    # 2. LZ compressibility: repetitive text compresses far smaller.
    raw = text.encode("utf-8", "ignore")
    comp_ratio = (len(zlib.compress(raw, 6)) / len(raw)) if raw else 1.0

    # 3. consecutive-segment similarity (semantic-ish loop on near-identical lines)
    segs = [s for s in (ln.strip() for ln in text.splitlines()) if s]
    if len(segs) < 3:
        # fall back to fixed-size word windows when there are few line breaks
        win = max(n * 2, 8)
        segs = [" ".join(words[i:i + win]) for i in range(0, len(words), win)]
    sims: list[float] = []
    for a, b in zip(segs, segs[1:]):
        sa, sb = set(a.lower().split()), set(b.lower().split())
        if not sa or not sb:
            continue
        sims.append(len(sa & sb) / len(sa | sb))
    seg_sim = (sum(sims) / len(sims)) if sims else 0.0

    metrics.update({
        "dup_ngram_ratio": round(dup_ratio, 4),
        "top_ngram_freq": round(top_freq, 4),
        "compress_ratio": round(comp_ratio, 4),
        "consecutive_seg_sim": round(seg_sim, 4),
    })

    reasons = []
    if dup_ratio >= SPIRAL_DUP_RATIO:
        reasons.append(f"repeated {n}-grams (dup_ratio={dup_ratio:.2f}>={SPIRAL_DUP_RATIO})")
    if top_freq >= SPIRAL_TOP_FREQ:
        reasons.append(f"one {n}-gram dominates (top_freq={top_freq:.2f}>={SPIRAL_TOP_FREQ})")
    if comp_ratio <= SPIRAL_COMPRESS:
        reasons.append(f"highly compressible (compress_ratio={comp_ratio:.2f}<={SPIRAL_COMPRESS})")
    if seg_sim >= SPIRAL_SEG_SIM:
        reasons.append(f"near-identical consecutive segments (sim={seg_sim:.2f}>={SPIRAL_SEG_SIM})")

    detected = bool(reasons)
    if detected:
        otel_emit.record("spiral_detected", {"reason": "; ".join(reasons), **metrics}, status="error")
    return {
        "ok": True,
        "spiral_detected": detected,
        "reason": "; ".join(reasons) if detected else "no repetition signature above thresholds",
        "metrics": metrics,
    }


# ── check_stall ──────────────────────────────────────────────────────────────
def check_stall(tool_name: str, elapsed_s: float, expecting_heartbeat: bool = False,
                last_heartbeat_age_s: float | None = None,
                per_tool_budget_s: float | None = None) -> dict[str, Any]:
    """Decide if a single in-flight tool call is HUNG vs legitimately WAITING.

    A call is hung only if it has exceeded its per-tool budget AND is not
    producing output. A process that is heartbeating (recent output, or a fresh
    last_heartbeat_age_s) is WAITING, not hung — never report it hung (the
    OpenHands kill-the-waiter trap). If it WAS heartbeating but the heartbeat is
    now older than the budget, it has gone silent and IS hung.
    """
    budget = float(per_tool_budget_s if per_tool_budget_s is not None else TOOL_BUDGET_S)
    elapsed = float(elapsed_s)
    over_budget = elapsed > budget

    heartbeating = (
        expecting_heartbeat
        and last_heartbeat_age_s is not None
        and float(last_heartbeat_age_s) <= budget
    )

    if not over_budget:
        return {"ok": True, "hung": False, "waiting": False, "budget_s": budget,
                "elapsed_s": elapsed, "reason": "within per-tool budget"}
    if heartbeating:
        return {"ok": True, "hung": False, "waiting": True, "budget_s": budget,
                "elapsed_s": elapsed,
                "reason": "over budget but heartbeating — legitimately waiting, do NOT kill"}

    # over budget and silent -> hung
    reason = ("over per-tool budget with no heartbeat — "
              + ("heartbeat went stale" if expecting_heartbeat else "silent")
              + f" ({elapsed:.0f}s > {budget:.0f}s)")
    otel_emit.record("poll_hang_caught", {"tool": tool_name, "elapsed_s": elapsed,
                                          "budget_s": budget, "reason": reason}, status="error")
    return {"ok": True, "hung": True, "waiting": False, "budget_s": budget,
            "elapsed_s": elapsed, "reason": reason}


# ── check_progress ───────────────────────────────────────────────────────────
def check_progress(task_id: str, signals: dict | None = None, n: int = 3) -> dict[str, Any]:
    """Progress-delta since the last call for this task_id.

    `signals` carries monotonic observables: files_touched (cumulative distinct
    files edited), tests_passing (current count), checkpoints (cumulative green
    checkpoints), turn (current turn number). Progress this call = any of
    files_touched / tests_passing / checkpoints increased. After `n` consecutive
    no-progress calls, flags no_progress=True (the within-task stall signal).
    """
    sig = signals or {}
    cur = {
        "files_touched": int(sig.get("files_touched", 0) or 0),
        "tests_passing": int(sig.get("tests_passing", 0) or 0),
        "checkpoints": int(sig.get("checkpoints", 0) or 0),
        "turn": int(sig.get("turn", 0) or 0),
    }
    with _lock:
        st = _load(task_id)
        prog = st.get("progress", {})
        prev = prog.get("last")
        no_progress_count = int(prog.get("no_progress_count", 0))
        last_green_turn = int(prog.get("last_green_turn", 0))

        if prev is None:
            deltas = {k: None for k in ("files_touched", "tests_passing", "checkpoints")}
            made_progress = True  # first call establishes a baseline
        else:
            deltas = {k: cur[k] - prev[k] for k in ("files_touched", "tests_passing", "checkpoints")}
            made_progress = any(deltas[k] > 0 for k in deltas)

        if made_progress:
            no_progress_count = 0
        else:
            no_progress_count += 1
        if prev is None or (deltas.get("checkpoints") or 0) > 0:
            last_green_turn = cur["turn"]

        prog.update({"last": cur, "no_progress_count": no_progress_count,
                     "last_green_turn": last_green_turn})
        st["progress"] = prog
        _save(task_id, st)

    no_progress = no_progress_count >= n
    turns_since_last_green = max(0, cur["turn"] - last_green_turn)
    if no_progress:
        otel_emit.record("no_progress", {"task_id": task_id, "count": no_progress_count,
                                        "turns_since_last_green": turns_since_last_green},
                         status="error")
    return {
        "ok": True,
        "no_progress": no_progress,
        "no_progress_count": no_progress_count,
        "n": n,
        "deltas": deltas,
        "turns_since_last_green": turns_since_last_green,
        "current": cur,
    }


# ── task budget ──────────────────────────────────────────────────────────────
def start_task_budget(task_id: str, wall_clock_s: float | None = None,
                      max_turns: int | None = None, usd_cap: float | None = None) -> dict[str, Any]:
    """Begin per-task budget tracking. Any limit left None is unbounded."""
    with _lock:
        st = _load(task_id)
        st["budget"] = {
            "start_ts": time.time(),
            "wall_clock_s": wall_clock_s,
            "max_turns": max_turns,
            "usd_cap": usd_cap,
        }
        _save(task_id, st)
    return {"ok": True, "task_id": task_id, "budget": st["budget"]}


def check_budget(task_id: str, turns_used: int | None = None, usd_spent: float | None = None,
                 elapsed_s_override: float | None = None) -> dict[str, Any]:
    """Report whether any per-task budget limit is exceeded and which.

    elapsed_s_override lets a caller (and the smoke test) supply elapsed time
    deterministically instead of reading the wall clock.
    """
    st = _load(task_id)
    b = st.get("budget")
    if not b:
        return {"ok": False, "error": f"no budget started for task '{task_id}' "
                "(call start_task_budget first)"}
    elapsed = float(elapsed_s_override) if elapsed_s_override is not None else (time.time() - b["start_ts"])
    exceeded: list[str] = []
    if b.get("wall_clock_s") is not None and elapsed >= float(b["wall_clock_s"]):
        exceeded.append("wall_clock")
    if b.get("max_turns") is not None and turns_used is not None and int(turns_used) >= int(b["max_turns"]):
        exceeded.append("max_turns")
    if b.get("usd_cap") is not None and usd_spent is not None and float(usd_spent) >= float(b["usd_cap"]):
        exceeded.append("usd")
    budget_exceeded = bool(exceeded)
    if budget_exceeded:
        otel_emit.record("budget_exceeded", {"task_id": task_id, "limits": ",".join(exceeded),
                                            "elapsed_s": round(elapsed, 2)}, status="error")
    return {
        "ok": True,
        "budget_exceeded": budget_exceeded,
        "exceeded": exceeded,
        "elapsed_s": round(elapsed, 2),
        "limits": {"wall_clock_s": b.get("wall_clock_s"), "max_turns": b.get("max_turns"),
                   "usd_cap": b.get("usd_cap")},
        "observed": {"turns_used": turns_used, "usd_spent": usd_spent},
    }


def status() -> dict[str, Any]:
    return {
        "tool_budget_s": TOOL_BUDGET_S,
        "spiral_thresholds": {
            "ngram": SPIRAL_NGRAM, "dup_ratio": SPIRAL_DUP_RATIO, "top_freq": SPIRAL_TOP_FREQ,
            "compress_ratio_max": SPIRAL_COMPRESS, "seg_sim": SPIRAL_SEG_SIM,
        },
        "state_dir": str(STATE_DIR),
    }
