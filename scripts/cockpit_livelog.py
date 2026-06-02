#!/usr/bin/env python3
"""cockpit_livelog.py — the cockpit's structured LIVE activity stream (Fix 2).

Tails ~/.hermes-max/logs/live.jsonl and renders each record as a scannable, fixed-
width step entry — the MCP server + tool, a brief input summary, status glyph, and
elapsed time — instead of opaque repeating spinner lines. Deep-research fan-outs
(parallel sub-calls) render as indented sub-entries under their parent.

  [HH:MM:SS] ✓ codebase_rag    search        "rate limiter token bucket"     0.8s
  [HH:MM:SS] ⟳ deep_research   fan-out       "token bucket algorithms"
                ├ groq           web           "token bucket survey"          0.3s
  [HH:MM:SS] ✓ verify           run_tests     test_rate_limiter.py    1.2s  12 pass
  [HH:MM:SS] ◆ checkpoint       git commit    a1b2c3d "verified-green"

Read-only; safe to start/stop anytime; waits for the file if it doesn't exist yet.
"""
from __future__ import annotations

import json
import os
import sys
import time

LOG_DIR = os.path.expanduser(os.environ.get(
    "HERMES_MAX_LOG_DIR", os.environ.get("HMX_LOG_DIR", "~/.hermes-max/logs")))
JSONL = os.path.join(LOG_DIR, "live.jsonl")

_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
def _c(code: str) -> str:
    return f"\033[{code}m" if _TTY else ""
RESET, DIM, GRN, RED, YEL, CYN, BLU, MAG = (
    _c("0"), _c("2"), _c("32"), _c("31"), _c("33"), _c("36"), _c("34"), _c("35"))

# Tools whose start opens a parallel fan-out (children indent until it ends).
_FANOUT = ("deep_research", "research_topic", "fanout", "fan_out", "multi_search")


def _trunc(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _col(s: str, w: int) -> str:
    s = str(s)
    return s[:w].ljust(w) if len(s) > w else s.ljust(w)


def _line(hms: str, glyph: str, gcol: str, server: str, tool: str,
          detail: str, right: str = "", indent: int = 0) -> str:
    pad = "    " + ("├ " if indent else "") if indent else ""
    return (f"{DIM}[{hms or '--:--:--'}]{RESET} {gcol}{glyph}{RESET} {pad}"
            f"{_col(server, 14 - (len(pad) if indent else 0))} "
            f"{CYN}{_col(tool, 12)}{RESET} {_col(detail, 36)} "
            f"{DIM}{right}{RESET}".rstrip())


def render(rec: dict, open_calls: dict, fanout: dict) -> list[str]:
    kind = rec.get("kind")
    hms = rec.get("hms", "")
    ts = rec.get("ts") or 0.0
    out: list[str] = []

    # Expire a stale fan-out: if the parent never emitted an end or a long gap
    # passed, stop nesting subsequent unrelated calls under it.
    if fanout.get("active") and ts and fanout.get("ts") and ts - fanout["ts"] > 90:
        fanout["active"] = None

    if kind == "start":
        tool = rec.get("tool", "tool")
        server = rec.get("server") or "—"
        inp = _trunc(rec.get("input", ""), 34)
        open_calls[tool] = {"ts": ts, "server": server, "input": inp}
        is_fan = any(k in tool.lower() for k in _FANOUT)
        if is_fan:
            fanout["active"] = tool
            fanout["ts"] = ts
            out.append(_line(hms, "⟳", YEL, server, "fan-out", f'"{inp}"', "…"))
        else:
            # a child only if a fan-out is active and this isn't the parent itself
            indent = 1 if (fanout.get("active") and fanout["active"] != tool) else 0
            out.append(_line(hms, "⟳", DIM, server, tool, f'"{inp}"' if inp else "", "…", indent))

    elif kind == "end":
        tool = rec.get("tool", "tool")
        oc = open_calls.pop(tool, {})
        secs = rec.get("secs")
        elapsed = f"{secs:.1f}s" if isinstance(secs, (int, float)) else ""
        ret = _trunc(rec.get("returned", ""), 22)
        detail = f'"{oc.get("input","")}"' if oc.get("input") else _trunc(tool, 34)
        indent = 1 if (fanout.get("active") and fanout["active"] != tool) else 0
        out.append(_line(hms, "✓", GRN, oc.get("server", rec.get("server") or "—"),
                         tool, detail, f"{elapsed}  {ret}".strip(), indent))
        if fanout.get("active") == tool:
            fanout["active"] = None

    elif kind == "fail":
        tool = rec.get("tool", "tool")
        oc = open_calls.pop(tool, {})
        reason = _trunc(rec.get("reason", "failed"), 30)
        fb = rec.get("fallback")
        right = f"{reason}" + (f" → {fb}" if fb else "")
        out.append(_line(hms, "✗", RED, oc.get("server", rec.get("server") or "—"),
                         tool, f'"{oc.get("input","")}"' if oc.get("input") else "", right))
        if fanout.get("active") == tool:
            fanout["active"] = None

    elif kind == "slow":
        tool = rec.get("tool", "tool")
        el = rec.get("elapsed_s")
        right = f"{el:.0f}s — still working" if isinstance(el, (int, float)) else "still working"
        out.append(_line(hms, "⟳", DIM, "—", tool, "(heartbeating, not stuck)", right))

    elif kind == "heartbeat":
        # Only surface heartbeats that carry progress (done/total) — skip keep-alives.
        done, total = rec.get("done"), rec.get("total")
        if done is not None and total:
            tool = rec.get("tool", "tool")
            item = _trunc(rec.get("item") or "", 30)
            # nest only if this is sub-progress of the active fan-out parent
            indent = 1 if fanout.get("active") == tool else 0
            out.append(_line(hms, "·", DIM, rec.get("server") or "—", tool, item,
                             f"{done}/{total}", indent))

    elif kind == "decision":
        fanout["active"] = None  # a routing decision means we've moved past any fan-out
        choice = rec.get("choice", "")
        reason = _trunc(rec.get("reason", ""), 40)
        gcol = RED if rec.get("error") else MAG
        out.append(_line(hms, "◆", gcol, "decision", rec.get("decision", ""),
                         f"{choice}", reason))

    elif kind == "span":
        nm = (rec.get("span") or "").lower()
        # terminal/work spans close any open fan-out so later calls don't nest.
        if any(k in nm for k in ("file_write", "write_file", "wrote_file", "edit_file",
                                 "apply_patch", "verify", "gate", "run_tests",
                                 "checkpoint", "commit")):
            fanout["active"] = None
        out += _render_span(rec, hms)

    return out


def _render_span(rec: dict, hms: str) -> list[str]:
    name = (rec.get("span") or "").lower()

    def attr(*keys):
        for k in keys:
            v = rec.get(k)
            if v not in (None, ""):
                return v
        return ""

    if any(k in name for k in ("file_write", "write_file", "wrote_file", "edit_file", "apply_patch")):
        path = _trunc(attr("path", "file", "reason"), 34)
        lines = attr("lines", "added", "diff")
        right = f"+{lines} lines" if lines else _trunc(attr("returned"), 18)
        return [_line(hms, "✎", BLU, "file", "write", path, right)]
    if any(k in name for k in ("verify", "gate", "tests_pass", "test_pass", "run_tests")):
        ok = str(rec.get("status", "ok")).lower() != "error" and "fail" not in str(attr("reason", "returned")).lower()
        target = _trunc(attr("target", "file", "reason"), 30)
        npass = attr("passed", "n_pass", "tests")
        right = (f"{npass} pass" if npass else ("green" if ok else "FAILED"))
        return [_line(hms, "✓" if ok else "✗", GRN if ok else RED, "verify", "run_tests", target, right)]
    if any(k in name for k in ("checkpoint", "commit", "verified_green")):
        h = _trunc(attr("commit", "hash", "reason", "returned"), 30)
        return [_line(hms, "◆", GRN, "checkpoint", "git commit", h)]
    if "task_classification" in name:
        return [_line(hms, "◷", DIM, "plan", "classify", _trunc(attr("reason"), 36))]
    if "plan_revision" in name:
        q = _trunc(attr("question"), 30)
        res = "resolved" if str(attr("resolved")).lower() == "true" else "bounded"
        return [_line(hms, "◷", YEL, "plan", "revision", q, res)]
    if "plan_lint" in name:
        complete = str(attr("complete")).lower() == "true"
        miss = _trunc(attr("missing"), 30)
        return [_line(hms, "◷", DIM if complete else YEL, "plan", "lint",
                      "complete" if complete else miss)]
    if "role_resolved" in name:
        # An LLM call resolved through the conductor — show it as such, with the
        # thinking budget/tokens (Fix 3) when present (the planner's reasoning).
        tb, tt = attr("thinking_budget"), attr("thinking_tok")
        out = attr("out_tok")
        bits = []
        if out:
            bits.append(f"{out} tok")
        if tt:
            bits.append(f"thinking {tt}")
        elif tb:
            bits.append(f"budget {tb}")
        return [_line(hms, "✓", GRN, attr("provider"), f"LLM·{attr('role')}",
                      _trunc(attr("model"), 30), " · ".join(bits))]
    # tier_routing / estimate / other internal spans → suppressed from the stream (L2).
    return []


def main() -> int:
    print(f"{DIM}── hermes-max cockpit · live activity  (tool · input · status · timing)"
          f"{RESET}")
    os.makedirs(LOG_DIR, exist_ok=True)
    open_calls: dict = {}
    fanout: dict = {"active": None}

    f = None
    inode = None
    # Seek to near the end so we show the live tail, not the whole history.
    while True:
        try:
            if f is None:
                f = open(JSONL, "r")
                st = os.fstat(f.fileno())
                inode = st.st_ino
                f.seek(max(0, st.st_size - 4000))
                f.readline()  # discard a partial line
            line = f.readline()
            if not line:
                # rotation/truncation check
                try:
                    if os.stat(JSONL).st_ino != inode:
                        f.close(); f = None; continue
                except OSError:
                    pass
                time.sleep(0.3)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for ln in render(rec, open_calls, fanout):
                print(ln, flush=True)
        except FileNotFoundError:
            time.sleep(0.5)
        except KeyboardInterrupt:
            return 0
        except OSError:
            time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
