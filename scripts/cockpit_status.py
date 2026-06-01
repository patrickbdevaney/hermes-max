#!/usr/bin/env python3
"""cockpit_status.py — the cockpit status pane's live RUN-CONTEXT block (Fix 2).

Prints a compact, live-updating "── current run ──" block (mode · cost · task ·
phase · rung · elapsed) that sits ABOVE the stack's server table (status.sh). Reads
the same signals the rest of hermes-max uses — the cost ledger, the resolved
executor backend (driver), and the live activity log — so it never fabricates state.
When nothing is active it shows the calm idle/ready summary.
"""
from __future__ import annotations

import json
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from lib.inference import ledger, roles  # noqa: E402

LOG_DIR = os.path.expanduser(os.environ.get(
    "HERMES_MAX_LOG_DIR", os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
JSONL = os.path.join(LOG_DIR, "live.jsonl")
BASE_FILE = os.path.expanduser("~/.hermes-max/ui/cockpit_base")

_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
def _c(code: str) -> str:
    return f"\033[{code}m" if _TTY else ""
RESET, DIM, GRN, YEL, CYN, BOLD = _c("0"), _c("2"), _c("32"), _c("33"), _c("36"), _c("1")


def _tail(n: int) -> list[dict]:
    try:
        with open(JSONL, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 60000))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for ln in data.splitlines()[-n:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _session_baseline(today_usd: float) -> float:
    try:
        with open(BASE_FILE) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return today_usd  # no baseline → session == 0 so far


def _fmt_usd(x: float) -> str:
    return f"${x:.6f}"


def _now() -> float:
    # time.time is allowed here (real script, not a workflow); used for "elapsed".
    return time.time()


def _activity(recs: list[dict]) -> tuple[str, str, float, float]:
    """Return (task, phase, burst_start_ts, last_ts) derived from the live log."""
    task, phase = "", ""
    last_ts = 0.0
    burst_start = 0.0
    prev_ts = None
    for r in recs:
        ts = r.get("ts") or 0.0
        if prev_ts is not None and ts - prev_ts > 90:
            burst_start = ts  # a gap > 90s starts a new activity burst
        if burst_start == 0.0:
            burst_start = ts
        prev_ts = ts
        last_ts = ts or last_ts
        if r.get("kind") == "span":
            name = (r.get("span") or "").lower()
            if "task_classification" in name and r.get("reason"):
                task = str(r["reason"])
            if "tier_routing" in name and r.get("phase"):
                phase = str(r["phase"])
        elif r.get("kind") == "start" and not task:
            task = str(r.get("input") or r.get("tool") or "")
    return task, phase, burst_start, last_ts


def main() -> int:
    mode = roles.active_mode_name()
    try:
        rep = ledger.report("today")
    except Exception:  # noqa: BLE001
        rep = {"total_usd": 0.0, "free_tok": 0, "paid_tok": 0}
    today = float(rep.get("total_usd", 0.0))
    session = max(0.0, today - _session_baseline(today))

    # rung / driver — the configured executor backend (no network probe here).
    try:
        b = roles.executor_backend(mode)
        host = ""
        base = b.get("base_url") or ""
        if base:
            host = base.split("//", 1)[-1].split("/", 1)[0]
        loc = "local" if b.get("local") else "cloud"
        if b.get("local") and host and not host.startswith(("localhost", "127.0.0.1")):
            loc = "remote"
        rung = f"{b.get('provider', '?')} ({loc})" + (f" @ {host}" if host else "")
        model = b.get("model_id") or ""
    except Exception:  # noqa: BLE001
        rung, model = "(unresolved)", ""

    recs = _tail(400)
    task, phase, burst_start, last_ts = _activity(recs)
    now = _now()
    idle = (not last_ts) or (now - last_ts > 30)
    elapsed = 0 if idle else max(0, int(now - (burst_start or last_ts)))

    bar = f"{DIM}{'─' * 2} current run {'─' * 30}{RESET}"
    print(bar)
    paid = int(rep.get("paid_tok", 0) or 0)
    free = int(rep.get("free_tok", 0) or 0)
    cost_line = (f"{_fmt_usd(session)} this session "
                 f"{DIM}({_fmt_usd(today)} today · {free:,} free · {paid:,} paid tok){RESET}")
    print(f"  {CYN}mode{RESET}  {mode} · {cost_line}")
    if idle:
        last_seen = f"{int(now - last_ts)}s ago" if last_ts else "no activity yet"
        print(f"  {CYN}state{RESET} {GRN}ready{RESET} {DIM}· last activity {last_seen}{RESET}")
        if task:
            print(f"  {CYN}last{RESET}  {DIM}{task[:60]}{RESET}")
    else:
        print(f"  {CYN}task{RESET}  {task[:60] or '(working)'}")
        ph = phase or "working"
        print(f"  {CYN}step{RESET}  phase: {YEL}{ph}{RESET}")
        em, es = divmod(elapsed, 60)
        print(f"  {CYN}time{RESET}  {em}:{es:02d} elapsed")
    print(f"  {CYN}rung{RESET}  {rung}" + (f" {DIM}· {model}{RESET}" if model else ""))
    print(f"{DIM}{'─' * 2} mcp servers {'─' * 30}{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
