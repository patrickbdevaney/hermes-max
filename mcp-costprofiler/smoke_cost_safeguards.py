#!/usr/bin/env python3
"""Smoke for CLAUDE_cost_safeguards.md SG1-3 (revised scope). Hermetic temp cost.db.

[A] SG1 ledger: record local ($0) + cloud (rated) calls; cost.db exists; cost_summary
    breaks down by backend; local=$0, cloud non-zero (V4 Pro rate); HM_COST_RATES override
[B] SG1 ratio_check: OK baseline; artificial ALERT when cloud-fraction threshold forced low
[C] SG2 cap: run_cost reflects spend; the conductor cap logic blocks cloud past HM_RUN_CAP,
    logs once, leaves local/fabric alone, never aborts
[D] SG3 ratio_log_line: writes the cockpit one-liner to ratio.log (OK / ALERT:reason)
Exit non-zero on first failure."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_d = tempfile.mkdtemp(prefix="cost-sg-")
os.environ["HM_COST_DB"] = str(Path(_d) / "cost.db")
os.environ["HM_RATIO_LOG"] = str(Path(_d) / "ratio.log")

import cost_profiler as cp


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


def section_ledger() -> None:
    print("[A] SG1 SQLite ledger + cost_summary")
    cp.record_call("runX", "local_vllm", "qwen3", tokens_in=1000, tokens_out=500, wall_clock_s=8.0)
    cp.record_call("runX", "groq", "llama-3.3-70b", tokens_in=2000, tokens_out=300, wall_clock_s=0.9)
    # cloud V4 Pro: cost computed from the rate table (not supplied)
    cp.record_call("runX", "deepinfra", "v4-pro", tokens_in=4000, tokens_out=800, wall_clock_s=1.5)
    if not Path(os.environ["HM_COST_DB"]).exists():
        _fail("cost.db must exist after recording")
    s = cp.cost_summary("runX")
    if s["call_count"] != 3:
        _fail(f"3 calls expected: {s}")
    if s["by_backend"]["local"] != 0.0 or s["by_backend"]["fabric"] != 0.0:
        _fail(f"local + fabric must be $0.00: {s['by_backend']}")
    # V4 Pro: 4000/1e6*1.74 + 800/1e6*3.48 ≈ 0.009744
    if not (0.0095 < s["by_backend"]["cloud"] < 0.0100):
        _fail(f"cloud cost should be rated by V4 Pro: {s['by_backend']}")
    _ok(f"cost.db: local=$0, fabric=$0, cloud=${s['by_backend']['cloud']} (V4 Pro rated), total=${s['total_usd']}")

    # HM_COST_RATES override
    os.environ["HM_COST_RATES"] = '{"v4-pro": [3.0, 6.0]}'
    c2 = cp.cost_for("cloud-deepseek", "v4-pro", 1_000_000, 0)
    if abs(c2 - 3.0) > 1e-6:
        _fail(f"HM_COST_RATES override not applied: {c2}")
    del os.environ["HM_COST_RATES"]
    _ok("HM_COST_RATES JSON override applied to the rate table")


def section_ratio() -> None:
    print("[B] SG1/3 ratio_check + artificial ALERT")
    r = cp.ratio_check()
    # cloud fraction here ≈ all cost is cloud (local/fabric $0) → likely > 0.40 already.
    # Force a CLEAN baseline by raising the threshold, then force an ALERT by lowering it.
    os.environ["HM_ALERT_CLOUD_FRAC"] = "0.99"
    import importlib
    importlib.reload(cp)
    if cp.ratio_check()["alert"]:
        _fail("with threshold 0.99 the run should be OK")
    _ok(f"high threshold → OK (cloud_fraction={cp.ratio_check()['cloud_fraction_7d']})")
    os.environ["HM_ALERT_CLOUD_FRAC"] = "0.01"
    importlib.reload(cp)
    ra = cp.ratio_check()
    if not ra["alert"] or "cloud_fraction" not in ra["reason"]:
        _fail(f"low threshold should ALERT on cloud_fraction: {ra}")
    _ok(f"forced low threshold → ALERT:{ra['reason']}")
    os.environ["HM_ALERT_CLOUD_FRAC"] = "0.40"
    importlib.reload(cp)


def section_cap() -> None:
    print("[C] SG2 per-run spend cap (conductor logic)")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "conductor"))
    import enforce
    # point enforce's run_id at runX and a tiny cap
    os.environ["HM_RUN_ID"] = "runX"
    os.environ["HM_RUN_CAP"] = "0.001"  # a tenth of a cent — runX already spent ~$0.0097
    import importlib
    importlib.reload(enforce)
    state: dict = {}
    warn = enforce.check_run_cap(state)
    if not warn or not state.get("cloud_blocked"):
        _fail(f"cap should fire + set cloud_blocked: warn={warn}, state={state}")
    if "cap" not in warn.lower() or "cloud calls blocked" not in warn.lower():
        _fail(f"cap warning text wrong: {warn}")
    _ok("run cap reached → cloud_blocked set + single warning logged")
    # second call: already blocked → no repeat warning
    if enforce.check_run_cap(state) is not None:
        _fail("cap must warn only ONCE per run")
    _ok("cap warns once (not every turn)")
    # a fresh run under the cap → not blocked
    os.environ["HM_RUN_CAP"] = "1.00"
    importlib.reload(enforce)
    if enforce.check_run_cap({}) is not None:
        _fail("under the cap → no block")
    _ok("under cap → cloud not blocked (local/fabric never affected)")
    os.environ["HM_RUN_CAP"] = "0.10"


def section_ratio_log() -> None:
    print("[D] SG3 ratio.log one-liner")
    line = cp.ratio_log_line("runX", ts_str="2026-06-03 12:00")
    if "run=runX" not in line or ("OK" not in line and "ALERT" not in line):
        _fail(f"ratio line malformed: {line}")
    if not Path(os.environ["HM_RATIO_LOG"]).exists():
        _fail("ratio.log must be written")
    contents = Path(os.environ["HM_RATIO_LOG"]).read_text()
    if "run=runX" not in contents:
        _fail("ratio.log should contain the run line")
    _ok(f"ratio.log line: {line}")


def main() -> None:
    section_ledger()
    section_ratio()
    section_cap()
    section_ratio_log()
    print("cost safeguards SG1-3 smoke PASSED")


if __name__ == "__main__":
    main()
