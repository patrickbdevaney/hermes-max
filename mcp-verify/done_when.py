"""done_when.py — execute a plan step's DONE-WHEN command and assert exit + output.

A bolt-on for the deterministic planner (PLANNER_PROMPT_SPEC §1 STEPS: each step's
'DONE-WHEN:' is an exact command + expected exit/output). run_done_when runs that command
under the same safety envelope as the verify gate — a destructive+network+install blocklist,
cwd confinement, a hard 60s cap, and 4000-char output truncation — and returns a machine-
parsable VERDICT uniform with the verification skill. Never raises.

Presence-gated and additive: if mcp-verify is down the executor's own bash verify still runs;
nothing here touches the agent loop.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Optional

# Inherit the deterministic-destruction blocklist AND block network/install commands — a
# DONE-WHEN is a LOCAL assertion, never a fetch or an install.
_VERIFY_BLOCKLIST = re.compile(
    r"(rm\s+-rf\s+/|sudo\s+rm|mkfs\.|dd\s+if=|format\s+[cC]:|"
    r":\(\)\s*\{|fork\s*bomb|>\s*/dev/sd|shutdown|reboot|halt|"
    r"chmod\s+777\s+/|curl.*\|\s*bash|wget.*\|\s*sh|"
    r"\bcurl\b|\bwget\b|pip3?\s+install|npm\s+install|"
    r"apt(?:-get)?\s+install|apt-get\b|brew\s+install|snap\s+install|"
    r"docker\s+(?:pull|run))",
    re.IGNORECASE,
)

_DONE_WHEN_RE = re.compile(r"DONE[\s\-]?WHEN:\s*`([^`]+)`\s*(?:→|->)\s*(.+)", re.IGNORECASE)
_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(PASS|FAIL|PARTIAL)\s*$", re.IGNORECASE | re.MULTILINE)
_TEST_RUNNER_RE = re.compile(
    r"\b(pytest|cargo\s+test|go\s+test|jest|vitest|mocha|unittest|tox|nose2?)\b", re.IGNORECASE)

_MAX_OUT = 4000
_HARD_CAP = 60


def run_done_when(command: str, expected_output: Optional[str] = None,
                  expected_exit_code: int = 0, timeout: int = 60, cwd: str = "") -> dict[str, Any]:
    """Run `command`, assert exit==expected_exit_code AND expected_output⊆output.
    Returns {passed, exit_code, output(≤4000), reason, verdict}. Never raises."""
    command = (command or "").strip()
    timeout = max(1, min(int(timeout or 60), _HARD_CAP))  # caller cannot raise above 60
    if not command:
        return _fail("empty command")
    if _VERIFY_BLOCKLIST.search(command):
        return _fail(f"[Blocked] verify blocklist (no destructive/network/install): {command[:80]}")

    workdir = cwd or os.environ.get("AGENT_WORK_DIR") or os.getcwd()
    if not os.path.isdir(workdir):
        return _fail(f"[Error] cwd is not a directory: {workdir[:120]}")

    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                              cwd=workdir, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _fail(f"[Timeout] {timeout}s exceeded: {command[:80]}")
    except Exception as e:  # noqa: BLE001 - never raise into the gate
        return _fail(f"[Error] {type(e).__name__}: {e}"[:200])

    raw = proc.stdout or ""
    if proc.stderr:
        raw += f"\n[stderr]\n{proc.stderr}"

    exit_ok = proc.returncode == expected_exit_code
    # quote-lenient containment: 'DONE-WHEN: … → "2 passed"' should match bare `2 passed`.
    needle = (expected_output or "").strip().strip("'\"")
    content_ok = (expected_output is None) or (needle in raw)
    passed = exit_ok and content_ok

    if passed:
        reason, verdict = "PASS", "VERDICT: PASS"
    else:
        bits = []
        if not exit_ok:
            bits.append(f"exit {proc.returncode} (expected {expected_exit_code})")
        if not content_ok:
            bits.append(f"missing expected output: {needle[:60]!r}")
        reason, verdict = "FAIL: " + "; ".join(bits), "VERDICT: FAIL"

    out: dict[str, Any] = {"passed": passed, "exit_code": proc.returncode,
                           "output": raw[:_MAX_OUT], "reason": reason, "verdict": verdict}
    # A test-runner DONE-WHEN is a correctness-critical check: exit 0 is necessary but NOT
    # sufficient. Surface the recommendation to also route the target through the formal/
    # property ladder (which needs the file path the command alone doesn't carry).
    if _TEST_RUNNER_RE.search(command):
        out["recommend_formal"] = True
        out["note"] = ("core-logic check — also run verify_formal / property_test on the target; "
                       "a green exit proves the test ran, not that the logic is correct")
    return out


def _fail(reason: str) -> dict[str, Any]:
    return {"passed": False, "exit_code": -1, "output": "", "reason": reason, "verdict": "VERDICT: FAIL"}


def parse_done_when(step_text: str) -> Optional[dict]:
    """Parse a step's 'DONE-WHEN: `cmd` → expected' into run_done_when kwargs. 'expected' of
    the form 'exit N' becomes expected_exit_code; otherwise it is an expected_output substring."""
    m = _DONE_WHEN_RE.search(step_text or "")
    if not m:
        return None
    cmd, exp = m.group(1).strip(), m.group(2).strip()
    em = re.match(r"exit\s+(?:code\s+)?(\d+)\s*$", exp, re.IGNORECASE)
    if em:
        return {"command": cmd, "expected_exit_code": int(em.group(1))}
    return {"command": cmd, "expected_output": exp, "expected_exit_code": 0}


def parse_verdict(text: str) -> Optional[str]:
    """Read the literal VERDICT line from a verification report (item 2): PASS|FAIL|PARTIAL,
    or None when absent. A MISSING verdict is treated as NON-pass by the caller."""
    m = _VERDICT_RE.search(text or "")
    return m.group(1).upper() if m else None
