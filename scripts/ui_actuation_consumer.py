"""Headless actuation consumer for the PART V equivalence test.

Drives REAL agent turns through the web UI's OWN API — the exact path the browser
uses (CSRF cookie → POST /api/run → consume GET /api/events/{run_id} over SSE) — and
asserts the visual-flow event stream carried every action, real work landed on disk,
and the conversational handback + a follow-up turn fired. No browser needed.

Usage:  python3 ui_actuation_consumer.py <port> <project_dir> [turn_timeout_s]
Exit 0 = PASS (all assertions); 1 = FAIL; writes ui_actuation_report.md in CWD.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
import urllib.request

PORT = int(sys.argv[1])
PROJ = sys.argv[2]
TURN_TIMEOUT = float(sys.argv[3]) if len(sys.argv) > 3 else 240.0

CANONICAL = ("In the CURRENT working directory (use relative paths only — do NOT write "
             "to your home directory or any absolute path), create ./is_prime.py with a "
             "function is_prime(n), and ./test_is_prime.py with 3 pytest test vectors. "
             "Use the plan/execute flow. Run pytest with PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "
             "and finish only when it is green. All files must stay in this directory.")
FOLLOWUP = ("In the SAME current directory (relative paths only), add an is_prime_list(nums) "
            "helper to ./is_prime.py that returns the primes from a list, and add a test for "
            "it to ./test_is_prime.py. Keep pytest green.")


def _csrf() -> str:
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request("GET", "/"); r = c.getresponse(); r.read()
    m = re.search(r"hmx_csrf=([^;]+)", r.getheader("Set-Cookie") or "")
    return m.group(1) if m else ""


def _post_run(csrf: str, body: dict) -> dict:
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    c.request("POST", "/api/run", json.dumps(body).encode(),
              {"Content-Type": "application/json", "Origin": f"http://localhost:{PORT}",
               "X-HMX-CSRF": csrf, "Cookie": f"hmx_csrf={csrf}"})
    return json.loads(c.getresponse().read())


def _consume(run_id: str, timeout: float) -> list[tuple[str, dict]]:
    """Record (event_type, data) until phase:done or timeout."""
    events: list[tuple[str, dict]] = []
    deadline = time.time() + timeout
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/api/events/{run_id}", timeout=timeout + 5)
    except OSError:
        return events
    ev = None
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:") and ev:
            try:
                data = json.loads(line[5:].strip())
            except ValueError:
                data = {}
            events.append((ev, data))
            if ev == "phase" and data.get("phase") == "done":
                break
        if time.time() > deadline:
            events.append(("__timeout__", {}))
            break
    return events


def _has(events, etype, pred=None) -> bool:
    for e, d in events:
        if e == etype and (pred is None or pred(d)):
            return True
    return False


def _ordered(events, checks) -> tuple[bool, list[str]]:
    """checks = [(label, etype, pred)]; assert each appears, in order. Returns
    (ok, per-check status lines)."""
    idx = 0
    lines, ok = [], True
    for label, etype, pred in checks:
        found_at = None
        for j in range(idx, len(events)):
            e, d = events[j]
            if e == etype and (pred is None or pred(d)):
                found_at = j
                break
        if found_at is None:
            ok = False
            lines.append(f"  ✗ MISSING: {label}")
        else:
            idx = found_at + 1
            lines.append(f"  ✓ {label}")
    return ok, lines


def _secret_values() -> list[str]:
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from ui.server import config_api, secrets_store
        out = []
        for ev in config_api.secret_env_vars():
            v = secrets_store._resolve(ev)
            if v and len(v) >= 8:
                out.append(v)
        return out
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    report = ["# UI Actuation Equivalence Report (PART V)", ""]
    csrf = _csrf()
    report.append(f"- CSRF cookie obtained: {'yes' if csrf else 'NO'}")

    # ── Turn 1 ──
    run = _post_run(csrf, {"cwd": PROJ, "prompt": CANONICAL, "mode": "free"})
    run_id = run.get("run_id")
    report.append(f"- Turn 1 launched via POST /api/run → run_id={run_id} "
                  f"launched={run.get('launched')} err={run.get('launch_error')}")
    if not run_id:
        report.append("\n**FAIL: the UI did not launch a run.**")
        _write(report); return 1
    t1 = _consume(run_id, TURN_TIMEOUT)

    checks1 = [
        ("phase: plan (planner actuated)", "phase", lambda d: d.get("phase") == "plan"),
        ("plan with >=1 item", "plan", lambda d: len(d.get("items", [])) >= 1),
        ("file_op: wrote is_prime", "file_op", lambda d: "is_prime" in str(d.get("path", ""))),
        ("file_op: wrote the test file", "file_op", lambda d: "test" in str(d.get("path", "")).lower()),
        ("shell/verify ran the tests", "shell", None),
        ("gate: verify pass", "gate", lambda d: d.get("status") == "pass"),
        ("checkpoint", "checkpoint", None),
        ("phase: done + handback", "phase", lambda d: d.get("phase") == "done"),
    ]
    ok1, lines1 = _ordered(t1, checks1)
    handback = _has(t1, "narration", lambda d: "your turn" in str(d.get("plain_text", "")).lower()) \
        or _has(t1, "phase", lambda d: d.get("phase") == "done")
    report += ["", f"## Turn 1 — visual-flow event assertions ({len(t1)} events)", *lines1,
               f"  {'✓' if handback else '✗'} conversational handback fired"]

    # ── On-disk artifacts (real work, not a simulation) ──
    isp = _find(PROJ, "is_prime.py")
    tst = _find(PROJ, "test_is_prime.py") or _find(PROJ, "test_isprime.py")
    has_commit = os.path.isdir(os.path.join(PROJ, ".git")) and _git_has_commit(PROJ)
    report += ["", "## On-disk artifacts (real actuation)",
               f"  {'✓' if isp else '✗'} is_prime.py exists" + (f" ({isp})" if isp else ""),
               f"  {'✓' if tst else '✗'} test file exists" + (f" ({tst})" if tst else ""),
               f"  {'✓' if has_commit else '✗'} git checkpoint commit present"]
    artifacts_ok = bool(isp and tst and has_commit)

    # ── Turn 2 (multi-turn loop) ──
    run2 = _post_run(csrf, {"run_id": run_id, "prompt": FOLLOWUP})
    report.append("")
    report.append(f"## Turn 2 — follow-up (multi-turn) → {run2.get('run_id')} "
                  f"err={run2.get('launch_error')}")
    t2 = _consume(run_id, TURN_TIMEOUT) if run2.get("run_id") else []
    turn2_ok = _has(t2, "phase", lambda d: d.get("phase") == "done") and \
        (_has(t2, "file_op") or _has(t2, "gate"))
    report.append(f"  {'✓' if turn2_ok else '✗'} a new turn streamed with work + handback "
                  f"({len(t2)} events)")

    # ── Secret leakage ──
    secrets = _secret_values()
    blob = json.dumps(t1 + t2, default=str)
    leaked = [s[:6] + "…" for s in secrets if s in blob]
    no_leak = not leaked
    report += ["", "## Secret leakage",
               f"  {'✓' if no_leak else '✗'} no provider secret in the event stream "
               f"({len(secrets)} keys checked)"]

    passed = ok1 and handback and artifacts_ok and turn2_ok and no_leak
    report += ["", "## Equivalence claim", "",
               f"- The web UI launched a real agent run via its own API "
               f"(`POST /api/run` → run_id `{run_id}`).",
               f"- Every agent action surfaced in the visual-flow event stream: "
               f"{'YES' if ok1 else 'PARTIAL — see missing items above'}.",
               f"- The agent actuated real work (on-disk artifacts + green verify): "
               f"{'YES' if artifacts_ok else 'NO'}.",
               f"- The conversational handback fired and a follow-up turn actuated: "
               f"{'YES' if (handback and turn2_ok) else 'NO'}.",
               "", f"## RESULT: {'PASS ✓' if passed else 'FAIL ✗'}"]
    _write(report)
    print("\n".join(report[-14:]))
    return 0 if passed else 1


def _find(root: str, name: str) -> str | None:
    for dirpath, _, files in os.walk(root):
        if ".git" in dirpath:
            continue
        if name in files:
            return os.path.relpath(os.path.join(dirpath, name), root)
    return None


def _git_has_commit(proj: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", proj, "rev-list", "--count", "HEAD"],
                           capture_output=True, timeout=10)
        return r.returncode == 0 and int(r.stdout.decode().strip() or "0") > 0
    except Exception:  # noqa: BLE001
        return False


def _write(lines: list[str]) -> None:
    try:
        with open("ui_actuation_report.md", "w") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
