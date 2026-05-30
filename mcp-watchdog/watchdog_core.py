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
# This is the GLOBAL backstop budget — Stage 1 refines it with a PER-TOOL registry
# (see TOOL_BUDGETS below) so legitimately-long work is not killed prematurely and
# genuinely-hung work still is. The global budget remains the fallback for any tool
# not in the registry.
TOOL_BUDGET_S = float(os.environ.get("WATCHDOG_TOOL_BUDGET_S", "120"))

# Heartbeat liveness window (s). A tool that is OVER its budget is killed only if it
# has produced NO heartbeat for longer than this — that is the do-nothing-hang
# detector. A tool that keeps heartbeating is "working", never killed, even past its
# estimate. (Separate knob from the budget itself, per the Stage-1 spec.)
HEARTBEAT_TIMEOUT_S = float(os.environ.get("HEARTBEAT_TIMEOUT_S", "90"))


# ── Stage 1: per-tool adaptive budget registry + look-ahead estimation ───────
# Each tool declares an expected-duration class, a HARD ceiling (the most it may
# ever run, heartbeat or not), and the look-ahead input that drives its estimate.
# Ceilings are overridable per-tool via BUDGET_<TOOL>_S in .env (TOOL upper-cased,
# non-alphanumerics -> '_'); e.g. BUDGET_INDEX_REPO_S, BUDGET_DEEP_RESEARCH_S.
#
# The principle: BEFORE a variable-duration tool runs, estimate how long it SHOULD
# take (look-ahead) and log it; WHILE it runs, require a heartbeat; kill only when
# (elapsed > ceiling) OR (elapsed > budget AND no heartbeat for > HEARTBEAT_TIMEOUT).
def _budget_env(tool: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in tool).upper()
    return f"BUDGET_{safe}_S"


def _env_ceiling(tool: str, default: float) -> float:
    try:
        return float(os.environ.get(_budget_env(tool), default))
    except (TypeError, ValueError):
        return float(default)


# expected: human label · ceiling_s: hard ceiling (env-overridable) · lookahead:
# the input the estimator uses (None = fixed-cost tool, no look-ahead needed).
_TOOL_BUDGET_DEFAULTS: dict[str, dict[str, Any]] = {
    "quick_check":    {"expected": "seconds",            "ceiling_s": 60,   "lookahead": "file size"},
    "lint":           {"expected": "seconds",            "ceiling_s": 60,   "lookahead": "file size"},
    "type":           {"expected": "seconds",            "ceiling_s": 60,   "lookahead": "file size"},
    "verify":         {"expected": "tens of seconds",    "ceiling_s": 300,  "lookahead": "test count"},
    "index_repo":     {"expected": "scales with repo",   "ceiling_s": 1800, "lookahead": "file count x avg size"},
    "search_code":    {"expected": "sub-second",         "ceiling_s": 30,   "lookahead": None},
    "rag_query":      {"expected": "sub-second",         "ceiling_s": 30,   "lookahead": None},
    "kg_query":       {"expected": "milliseconds",       "ceiling_s": 15,   "lookahead": None},
    "kg_record":      {"expected": "milliseconds",       "ceiling_s": 15,   "lookahead": None},
    "fetch_clean":    {"expected": "seconds-per-page",   "ceiling_s": 90,   "lookahead": "page count"},
    "deep_research":  {"expected": "minutes",            "ceiling_s": 900,  "lookahead": "query count x per-source"},
    "parallel_draft": {"expected": "seconds (concurrent)", "ceiling_s": 120, "lookahead": "pool size"},
    "synth":          {"expected": "seconds",            "ceiling_s": 120,  "lookahead": None},
    "steer":          {"expected": "seconds",            "ceiling_s": 120,  "lookahead": None},
    "escalate":       {"expected": "seconds",            "ceiling_s": 120,  "lookahead": None},
}

# Per-item rates used by the look-ahead estimators (env-overridable). These are
# rough "what's normal" priors — the heartbeat is the real liveness signal; the
# estimate only tells the watchdog/operator what to expect and catches a doomed run
# whose estimate alone already blows past the hard ceiling.
EST_INDEX_PER_FILE_S = float(os.environ.get("EST_INDEX_PER_FILE_S", "0.077"))
EST_INDEX_PER_MB_S = float(os.environ.get("EST_INDEX_PER_MB_S", "0.5"))
EST_RESEARCH_PER_SOURCE_S = float(os.environ.get("EST_RESEARCH_PER_SOURCE_S", "30"))
EST_FETCH_PER_PAGE_S = float(os.environ.get("EST_FETCH_PER_PAGE_S", "8"))


def tool_budget(tool_name: str) -> dict[str, Any]:
    """Return the registered budget for a tool: expected class, soft budget, hard
    ceiling (env-overridable), and look-ahead input. Unknown tools fall back to the
    global TOOL_BUDGET_S as both budget and ceiling so nothing is left unbudgeted."""
    reg = _TOOL_BUDGET_DEFAULTS.get(tool_name)
    if reg is None:
        # Unknown tool: soft budget = global default, NO hard ceiling. It is judged
        # purely by budget + heartbeat — so an arbitrary long-lived process (a dev
        # server, a watcher) that keeps heartbeating is never killed for being slow.
        return {"tool": tool_name, "known": False, "expected": "unknown",
                "budget_s": TOOL_BUDGET_S, "ceiling_s": None,
                "heartbeat_timeout_s": HEARTBEAT_TIMEOUT_S, "lookahead": None}
    ceiling = _env_ceiling(tool_name, reg["ceiling_s"])
    # Soft budget = the global default, but never above the tool's hard ceiling.
    budget = min(TOOL_BUDGET_S, ceiling)
    return {"tool": tool_name, "known": True, "expected": reg["expected"],
            "budget_s": budget, "ceiling_s": ceiling,
            "heartbeat_timeout_s": HEARTBEAT_TIMEOUT_S, "lookahead": reg["lookahead"]}


def estimate_duration(tool_name: str, **inputs: Any) -> dict[str, Any]:
    """Look-ahead: estimate how long a variable-duration tool SHOULD take BEFORE it
    runs, so the watchdog knows what's normal and the operator knows what to expect.

    Recognised inputs:
      index_repo:    file_count, total_bytes (or avg_file_bytes)
      deep_research: query_count, per_source_s
      fetch_clean:   page_count, per_page_s
      verify:        test_count
    Returns est_s, the hard ceiling, exceeds_ceiling (a doomed run to chunk/raise),
    and a human-readable basis string. Emits a tool_estimate span."""
    b = tool_budget(tool_name)
    ceiling = b["ceiling_s"]
    est_s = 0.0
    basis = "fixed-cost tool — no look-ahead"

    if tool_name == "index_repo":
        n = int(inputs.get("file_count", 0) or 0)
        total_bytes = inputs.get("total_bytes")
        if total_bytes is None and inputs.get("avg_file_bytes") is not None:
            total_bytes = float(inputs["avg_file_bytes"]) * n
        mb = (float(total_bytes) / 1_048_576) if total_bytes else 0.0
        est_s = max(0.5, n * EST_INDEX_PER_FILE_S + mb * EST_INDEX_PER_MB_S) if n else 0.0
        basis = f"{n:,} files" + (f", {mb:.1f}MB" if mb else "") + \
                f" x ~{EST_INDEX_PER_FILE_S:.3f}s/file = est ~{est_s:.0f}s"
    elif tool_name == "deep_research":
        q = int(inputs.get("query_count", 0) or 0)
        per = float(inputs.get("per_source_s", EST_RESEARCH_PER_SOURCE_S))
        est_s = q * per
        basis = f"{q} planned queries x ~{per:.0f}s/source = est ~{est_s:.0f}s"
    elif tool_name == "fetch_clean":
        pages = int(inputs.get("page_count", 1) or 1)
        per = float(inputs.get("per_page_s", EST_FETCH_PER_PAGE_S))
        est_s = pages * per
        basis = f"{pages} page(s) x ~{per:.0f}s/page = est ~{est_s:.0f}s"
    elif tool_name == "verify":
        t = int(inputs.get("test_count", 0) or 0)
        per = float(inputs.get("per_test_s", 0.25))
        est_s = max(2.0, t * per) if t else 0.0
        basis = f"{t} tests x ~{per:.2f}s = est ~{est_s:.0f}s" if t else basis

    exceeds = bool(est_s and ceiling is not None and est_s > ceiling)
    out = {
        "ok": True, "tool": tool_name, "est_s": round(est_s, 1),
        "ceiling_s": ceiling, "budget_s": b["budget_s"],
        "exceeds_ceiling": exceeds, "basis": basis,
        "advice": ("estimate alone exceeds the hard ceiling — chunk the work or "
                   f"raise {_budget_env(tool_name)} before starting"
                   if exceeds else "within ceiling — run with heartbeat liveness"),
    }
    otel_emit.record("tool_estimate", {"tool": tool_name, "est_s": out["est_s"],
                                       "ceiling_s": ceiling, "exceeds_ceiling": exceeds,
                                       "basis": basis},
                     status="error" if exceeds else "ok")
    return out


def record_heartbeat(task_id: str, tool_name: str, progress: str | None = None,
                     done: int | None = None, total: int | None = None) -> dict[str, Any]:
    """Stamp a liveness heartbeat for an in-flight long-running tool. A tool that
    heartbeats (per file-batch, per research source) is proven WORKING; check_stall
    reads the freshness of this stamp to decide hung-vs-waiting. Emits a
    tool_heartbeat span carrying current progress (item N/total) for the live log."""
    now = time.time()
    with _lock:
        st = _load(task_id)
        hb = st.get("heartbeats", {})
        hb[tool_name] = {"ts": now, "progress": progress, "done": done, "total": total}
        st["heartbeats"] = hb
        _save(task_id, st)
    pct = (round(100 * done / total, 1) if (done is not None and total) else None)
    otel_emit.record("tool_heartbeat", {"task_id": task_id, "tool": tool_name,
                                        "progress": progress, "done": done, "total": total,
                                        "pct": pct})
    return {"ok": True, "tool": tool_name, "ts": now, "progress": progress,
            "done": done, "total": total, "pct": pct}


def _heartbeat_age(task_id: str, tool_name: str) -> float | None:
    st = _load(task_id)
    hb = st.get("heartbeats", {}).get(tool_name)
    if not hb:
        return None
    return max(0.0, time.time() - float(hb["ts"]))

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
                per_tool_budget_s: float | None = None,
                task_id: str | None = None) -> dict[str, Any]:
    """Decide if a single in-flight tool call is HUNG vs legitimately WAITING.

    Per-tool adaptive model (Stage 1):
      * budget  = per_tool_budget_s if given, else the tool's registered soft budget
                  (which falls back to the global TOOL_BUDGET_S for unknown tools).
      * ceiling = the tool's registered HARD ceiling — the most it may EVER run,
                  heartbeat or not (env-overridable via BUDGET_<TOOL>_S).
      * heartbeat age — supplied directly, OR looked up from the watchdog state by
                  task_id (record_heartbeat stamps it). Fresh within
                  HEARTBEAT_TIMEOUT_S ⇒ the process is WORKING.

    KILL only when (elapsed > ceiling)  — a hard runaway, OR
                  (elapsed > budget AND no fresh heartbeat for > HEARTBEAT_TIMEOUT_S).
    A tool that keeps heartbeating is never killed for being slow — it is
    "slow-but-alive" (the OpenHands kill-the-waiter trap, avoided). Over budget but
    still heartbeating emits tool_slow_but_alive; a kill emits tool_killed_hung.
    """
    reg = tool_budget(tool_name)
    budget = float(per_tool_budget_s if per_tool_budget_s is not None else reg["budget_s"])
    # ceiling is None for unknown tools (no hard cap — heartbeat governs entirely).
    ceiling = float(reg["ceiling_s"]) if reg["ceiling_s"] is not None else None
    hb_timeout = HEARTBEAT_TIMEOUT_S
    elapsed = float(elapsed_s)

    # Resolve heartbeat age: explicit arg wins; else look it up from state by task_id.
    hb_age = last_heartbeat_age_s
    if hb_age is None and task_id is not None:
        hb_age = _heartbeat_age(task_id, tool_name)
    if hb_age is not None:
        expecting_heartbeat = True

    over_budget = elapsed > budget
    over_ceiling = ceiling is not None and elapsed > ceiling
    fresh_heartbeat = (
        expecting_heartbeat and hb_age is not None and float(hb_age) <= hb_timeout
    )

    base = {"ok": True, "budget_s": budget, "ceiling_s": ceiling,
            "heartbeat_timeout_s": hb_timeout, "elapsed_s": elapsed,
            "heartbeat_age_s": (round(float(hb_age), 1) if hb_age is not None else None)}

    # 1. Hard ceiling — killed regardless of heartbeat (a genuine runaway).
    if over_ceiling:
        reason = (f"exceeded HARD ceiling ({elapsed:.0f}s > {ceiling:.0f}s) — "
                  "killed even though heartbeating" if fresh_heartbeat
                  else f"exceeded HARD ceiling ({elapsed:.0f}s > {ceiling:.0f}s)")
        otel_emit.record("tool_killed_hung", {"tool": tool_name, "elapsed_s": elapsed,
                                              "ceiling_s": ceiling, "cause": "ceiling",
                                              "reason": reason}, status="error")
        return {**base, "hung": True, "waiting": False, "cause": "ceiling", "reason": reason}

    # 2. Within budget — fine.
    if not over_budget:
        return {**base, "hung": False, "waiting": False, "cause": None,
                "reason": "within per-tool budget"}

    # 3. Over budget but heartbeating — slow-but-alive, do NOT kill.
    if fresh_heartbeat:
        reason = (f"over budget ({elapsed:.0f}s > {budget:.0f}s) but heartbeating "
                  f"({float(hb_age):.0f}s ago) — legitimately working, do NOT kill")
        otel_emit.record("tool_slow_but_alive", {"tool": tool_name, "elapsed_s": elapsed,
                                                 "budget_s": budget, "ceiling_s": ceiling,
                                                 "heartbeat_age_s": round(float(hb_age), 1)})
        return {**base, "hung": False, "waiting": True, "cause": None, "reason": reason}

    # 4. Over budget and silent past the heartbeat timeout — hung.
    if expecting_heartbeat and hb_age is not None:
        why = f"heartbeat went stale ({float(hb_age):.0f}s > {hb_timeout:.0f}s)"
    elif expecting_heartbeat:
        why = "expected a heartbeat but none recorded"
    else:
        why = "silent"
    reason = (f"over per-tool budget with no heartbeat — {why} "
              f"({elapsed:.0f}s > {budget:.0f}s)")
    otel_emit.record("tool_killed_hung", {"tool": tool_name, "elapsed_s": elapsed,
                                          "budget_s": budget, "cause": "budget+no-heartbeat",
                                          "reason": reason}, status="error")
    # Keep the legacy span too so existing Phoenix dashboards still light up.
    otel_emit.record("poll_hang_caught", {"tool": tool_name, "elapsed_s": elapsed,
                                          "budget_s": budget, "reason": reason}, status="error")
    return {**base, "hung": True, "waiting": False, "cause": "budget+no-heartbeat",
            "reason": reason}


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
        "heartbeat_timeout_s": HEARTBEAT_TIMEOUT_S,
        "tool_budgets": {t: tool_budget(t) for t in _TOOL_BUDGET_DEFAULTS},
        "spiral_thresholds": {
            "ngram": SPIRAL_NGRAM, "dup_ratio": SPIRAL_DUP_RATIO, "top_freq": SPIRAL_TOP_FREQ,
            "compress_ratio_max": SPIRAL_COMPRESS, "seg_sim": SPIRAL_SEG_SIM,
        },
        "state_dir": str(STATE_DIR),
    }
