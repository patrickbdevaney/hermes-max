#!/usr/bin/env python3
"""observe.py — live observability terminal (R-Stage 5): see exactly where the
wall time goes. Renders, refreshing ~1/s:

  • backend throughput  — vLLM /metrics: running/waiting requests, tokens/sec
    (delta of generation_tokens_total), avg TTFT + inter-token latency, KV-cache %;
  • in-flight tools      — every tool call started-but-not-ended, with LIVE elapsed
    (what is eating time right now);
  • time breakdown       — wall time aggregated by tool over the view window, plus
    an IDLE estimate (wall not covered by any tool = model reasoning between calls).
    This is the "where did the 1500s go — research vs implement vs idle" answer;
  • tool waterfall       — the last completed tool calls with a latency bar;
  • span tail            — the most recent raw live-log spans.

Data sources: the existing $HERMES_MAX_LOG_DIR/live.jsonl (per-span timing) and
$VLLM_BASE_URL/metrics. Stdlib only (curses + urllib) — no venv, runs anywhere.
Read-only; Ctrl-C to quit. Degrades to a plain periodic snapshot if not a TTY.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections import OrderedDict, deque

LIVE = os.path.expanduser(os.environ.get(
    "HERMES_MAX_LIVE_JSONL",
    os.path.join(os.environ.get("HERMES_MAX_LOG_DIR", "~/.hermes-max/logs"), "live.jsonl")))
VLLM_BASE = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
METRICS_URL = (VLLM_BASE[:-3] if VLLM_BASE.endswith("/v1") else VLLM_BASE).rstrip("/") + "/metrics" if VLLM_BASE else ""
REFRESH = float(os.environ.get("OBSERVE_REFRESH_S", "1.0"))
METRICS_EVERY = float(os.environ.get("OBSERVE_METRICS_EVERY_S", "3.0"))
WINDOW_S = float(os.environ.get("OBSERVE_WINDOW_S", "1800"))  # time-breakdown window


# ── vLLM metrics (Prometheus text) ───────────────────────────────────────────
def _scrape():
    if not METRICS_URL:
        return {}
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=4) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return {}
    agg = {}
    for ln in text.splitlines():
        if not ln or ln[0] == "#" or not ln.startswith("vllm:"):
            continue
        try:
            name = ln.split("{", 1)[0].split(" ", 1)[0]
            val = float(ln.rsplit(" ", 1)[1])
        except Exception:  # noqa: BLE001
            continue
        agg[name] = agg.get(name, 0.0) + val
    return agg


def _derive(prev, cur, dt):
    """Per-second rates + averages from two metric snapshots."""
    out = {}
    def g(k):
        return cur.get(k)
    out["running"] = g("vllm:num_requests_running")
    out["waiting"] = g("vllm:num_requests_waiting")
    out["kv"] = g("vllm:gpu_cache_usage_perc") or g("vllm:kv_cache_usage_perc")
    if prev and dt > 0:
        gt = (cur.get("vllm:generation_tokens_total", 0) - prev.get("vllm:generation_tokens_total", 0))
        out["tok_s"] = max(0.0, gt / dt)
        dcount = (cur.get("vllm:time_to_first_token_seconds_count", 0)
                  - prev.get("vllm:time_to_first_token_seconds_count", 0))
        dsum = (cur.get("vllm:time_to_first_token_seconds_sum", 0)
                - prev.get("vllm:time_to_first_token_seconds_sum", 0))
        out["ttft"] = (dsum / dcount) if dcount > 0 else None
        dic = (cur.get("vllm:inter_token_latency_seconds_count", 0)
               - prev.get("vllm:inter_token_latency_seconds_count", 0))
        dis = (cur.get("vllm:inter_token_latency_seconds_sum", 0)
               - prev.get("vllm:inter_token_latency_seconds_sum", 0))
        out["itl"] = (dis / dic) if dic > 0 else None
    return out


# ── live.jsonl incremental reader ────────────────────────────────────────────
class LiveReader:
    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.events = deque(maxlen=4000)

    def poll(self):
        try:
            sz = os.path.getsize(self.path)
        except OSError:
            return
        if sz < self.pos:  # truncated/rotated
            self.pos = 0
        try:
            with open(self.path, "r") as f:
                f.seek(self.pos)
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        self.events.append(json.loads(ln))
                    except Exception:  # noqa: BLE001
                        pass
                self.pos = f.tell()
        except OSError:
            return


def _analyze(events, now):
    """Return (inflight, completed, by_tool, idle, last_ts) from the event window."""
    cutoff = now - WINDOW_S
    starts = {}              # tool -> [start_ts,...]
    completed = deque(maxlen=14)
    by_tool = OrderedDict()  # tool -> total secs
    covered = 0.0
    last_ts = None
    win = [e for e in events if (e.get("ts") or 0) >= cutoff]
    for e in win:
        ts = e.get("ts")
        if ts:
            last_ts = ts
        kind = e.get("kind", "")
        tool = e.get("tool") or e.get("span") or "?"
        if kind == "start":
            starts.setdefault(tool, []).append(ts or now)
        elif kind in ("end", "fail"):
            st = starts.get(tool)
            if st:
                st.pop(0)
            secs = e.get("secs")
            if secs is None and ts:
                secs = 0.0
            secs = float(secs or 0.0)
            by_tool[tool] = by_tool.get(tool, 0.0) + secs
            covered += secs
            completed.append((e.get("hms", ""), tool, secs, kind == "end"))
    inflight = []
    for tool, lst in starts.items():
        for st in lst:
            inflight.append((tool, max(0.0, now - st)))
    span = (last_ts - win[0]["ts"]) if (win and last_ts and win[0].get("ts")) else 0.0
    idle = max(0.0, span - covered)
    by_tool = OrderedDict(sorted(by_tool.items(), key=lambda kv: kv[1], reverse=True))
    return inflight, completed, by_tool, idle, covered, span, last_ts


def _bar(frac, width):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "█" * n + "·" * (width - n)


def _fmt(secs):
    if secs is None:
        return "—"
    if secs < 60:
        return f"{secs:.0f}s"
    return f"{secs/60:.1f}m"


# ── curses render ─────────────────────────────────────────────────────────────
def run_curses(stdscr):
    import curses
    curses.curs_set(0)
    stdscr.nodelay(True)
    try:
        curses.start_color(); curses.use_default_colors()
        for i, c in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED,
                               curses.COLOR_CYAN, curses.COLOR_MAGENTA), 1):
            curses.init_pair(i, c, -1)
        CG, CY, CR, CC, CM = (curses.color_pair(i) for i in range(1, 6))
    except Exception:  # noqa: BLE001
        CG = CY = CR = CC = CM = 0

    reader = LiveReader(LIVE)
    started = time.time()
    prev_m, prev_mt, metrics = {}, 0.0, {}
    last_metric_poll = 0.0

    while True:
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break
        now = time.time()
        reader.poll()
        if now - last_metric_poll >= METRICS_EVERY:
            cur = _scrape()
            if cur:
                metrics = _derive(prev_m, cur, now - prev_mt if prev_mt else 0.0)
                prev_m, prev_mt = cur, now
            last_metric_poll = now

        inflight, completed, by_tool, idle, covered, span, last_ts = _analyze(reader.events, now)
        H, W = stdscr.getmaxyx()
        stdscr.erase()
        row = 0

        def line(s, attr=0):
            nonlocal row
            if row < H - 1:
                stdscr.addnstr(row, 0, s, W - 1, attr)
                row += 1

        age = (now - last_ts) if last_ts else None
        line(f"hermes-max OBSERVE  ·  up {_fmt(now-started)}  ·  live.jsonl "
             f"{'(idle %ds)' % age if age is not None else '(no events)'}  ·  q to quit", CC)
        line("─" * (W - 1))

        # backend
        m = metrics
        if m:
            line("BACKEND (vLLM)", CM)
            line(f"  running {m.get('running','?')}  waiting {m.get('waiting','?')}   "
                 f"throughput {('%.0f tok/s' % m['tok_s']) if m.get('tok_s') is not None else '—'}   "
                 f"TTFT {('%.2fs' % m['ttft']) if m.get('ttft') else '—'}   "
                 f"ITL {('%.0fms' % (m['itl']*1000)) if m.get('itl') else '—'}   "
                 f"KV {('%.0f%%' % (m['kv']*100)) if m.get('kv') is not None else '—'}")
        else:
            line("BACKEND (vLLM)  — /metrics unavailable", CY)
        line("")

        # in-flight
        line("IN-FLIGHT TOOLS", CM)
        if inflight:
            for tool, el in sorted(inflight, key=lambda x: -x[1])[:6]:
                a = CR if el > 120 else (CY if el > 30 else CG)
                line(f"  ⟳ {tool:24.24} {_fmt(el):>7}  (running now)", a)
        else:
            line("  (none — agent reasoning or between calls)")
        line("")

        # time breakdown — where the wall time went
        line(f"TIME BREAKDOWN  (last {_fmt(span)} of activity)", CM)
        denom = max(1e-6, span)
        for tool, secs in list(by_tool.items())[:8]:
            line(f"  {tool:22.22} {_bar(secs/denom, 24)} {_fmt(secs):>7}  {100*secs/denom:4.0f}%")
        line(f"  {'· idle (reasoning)':22.22} {_bar(idle/denom, 24)} {_fmt(idle):>7}  {100*idle/denom:4.0f}%",
             CY)
        line("")

        # waterfall
        line("RECENT TOOL CALLS", CM)
        mx = max([s for _, _, s, _ in completed] + [1.0])
        for hms, tool, secs, ok in list(completed)[-min(10, H):][::-1]:
            a = CG if ok else CR
            line(f"  {hms:8.8} {tool:20.20} {_bar(secs/mx, 18)} {_fmt(secs):>7}", a)

        stdscr.refresh()
        time.sleep(REFRESH)


def run_plain():
    """Non-TTY fallback: print a snapshot every few seconds."""
    reader = LiveReader(LIVE)
    prev_m, prev_mt = {}, 0.0
    while True:
        now = time.time()
        reader.poll()
        cur = _scrape()
        m = _derive(prev_m, cur, now - prev_mt if prev_mt else 0.0) if cur else {}
        if cur:
            prev_m, prev_mt = cur, now
        inflight, completed, by_tool, idle, covered, span, _ = _analyze(reader.events, now)
        print(f"\n=== observe {time.strftime('%H:%M:%S')} ===")
        if m:
            print(f"backend: running={m.get('running')} waiting={m.get('waiting')} "
                  f"tok/s={('%.0f'%m['tok_s']) if m.get('tok_s') is not None else '—'} "
                  f"TTFT={('%.2fs'%m['ttft']) if m.get('ttft') else '—'}")
        if inflight:
            print("in-flight: " + ", ".join(f"{t}({_fmt(e)})" for t, e in inflight))
        print(f"time breakdown (last {_fmt(span)}):")
        for tool, secs in list(by_tool.items())[:8]:
            print(f"  {tool:22.22} {_fmt(secs):>7}  {100*secs/max(1e-6,span):4.0f}%")
        print(f"  {'idle':22.22} {_fmt(idle):>7}  {100*idle/max(1e-6,span):4.0f}%")
        time.sleep(max(2.0, METRICS_EVERY))


if __name__ == "__main__":
    if not sys.stdout.isatty():
        try:
            run_plain()
        except KeyboardInterrupt:
            pass
    else:
        try:
            import curses
            curses.wrapper(run_curses)
        except KeyboardInterrupt:
            pass
        except Exception as e:  # noqa: BLE001 - curses unavailable -> plain
            print(f"(curses unavailable: {e}; falling back to plain snapshots)")
            try:
                run_plain()
            except KeyboardInterrupt:
                pass
