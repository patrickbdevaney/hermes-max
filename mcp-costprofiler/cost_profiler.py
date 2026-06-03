"""cost_profiler.py — Safeguard 1: the SQLite cost ledger (the measurement substrate).

Every LLM call is recorded to an append-only SQLite ledger at ~/.hermes-max/cost.db. This is
the single source of truth the per-run spend cap (Safeguard 2) and the ratio alert
(Safeguard 3) gate on. Deterministic, no LLM, never raises (a DB error degrades to a no-op so
the agent loop is never broken).

Backends: 'local' | 'fabric' | 'cloud-deepseek'. Dollar rates (per 1M in/out), overridable
via the HM_COST_RATES env var (JSON):
    V4 Flash  $0.14 / $0.28      V4 Pro  $1.74 / $3.48      fabric/local  $0.00
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

# alert thresholds (env-overridable so the DoD can force an artificial ALERT)
ALERT_CLOUD_FRACTION = float(os.environ.get("HM_ALERT_CLOUD_FRAC", "0.40"))
ALERT_COST_PER_TASK = float(os.environ.get("HM_ALERT_COST_PER_TASK", "0.15"))

# steady-state $/1M (in, out). HM_COST_RATES JSON merges over these by model-substring key.
_DEFAULT_RATES = {"v4-flash": (0.14, 0.28), "flash": (0.14, 0.28), "driver": (0.14, 0.28),
                  "v4-pro": (1.74, 3.48), "pro": (1.74, 3.48), "planner": (1.74, 3.48)}


def _rates() -> dict[str, tuple]:
    rates = dict(_DEFAULT_RATES)
    raw = os.environ.get("HM_COST_RATES", "").strip()
    if raw:
        try:
            for k, v in json.loads(raw).items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    rates[str(k).lower()] = (float(v[0]), float(v[1]))
        except Exception:  # noqa: BLE001
            pass
    return rates


def _db_path() -> str:
    return os.path.expanduser(os.environ.get("HM_COST_DB", "~/.hermes-max/cost.db"))


def _connect() -> Optional[sqlite3.Connection]:
    try:
        Path(_db_path()).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(_db_path(), timeout=10)
        con.execute(
            "CREATE TABLE IF NOT EXISTS calls ("
            "id INTEGER PRIMARY KEY, ts REAL, run_id TEXT, provider TEXT, model TEXT, "
            "backend TEXT, tokens_in INTEGER, tokens_out INTEGER, tokens_cached INTEGER, "
            "cost_usd REAL, wall_clock_s REAL)")
        return con
    except Exception:  # noqa: BLE001
        return None


def cost_for(backend: str, model: str, tokens_in: int, tokens_out: int) -> float:
    """Dollar cost at current rates. fabric/local are $0; cloud is rated by model substring."""
    if backend in ("local", "fabric"):
        return 0.0
    m = (model or "").lower()
    for key, (ci, co) in _rates().items():
        if key in m:
            return round(tokens_in / 1e6 * ci + tokens_out / 1e6 * co, 6)
    # unknown cloud model → assume Flash rate (conservative-low; surfaced via ratio_check)
    ci, co = _rates()["flash"]
    return round(tokens_in / 1e6 * ci + tokens_out / 1e6 * co, 6)


def classify_backend(provider: str, model: str = "") -> str:
    """provider/model → 'local' | 'fabric' | 'cloud-deepseek'."""
    p = f"{provider} {model}".lower()
    if any(n in p for n in ("local", "vllm", "thor", "qwen")):
        return "local"
    if any(n in p for n in ("groq", "cerebras")):
        return "fabric"
    return "cloud-deepseek"  # deepinfra/deepseek/openrouter-paid/etc.


def record_call(run_id: str, provider: str, model: str, backend: str = "",
                tokens_in: int = 0, tokens_out: int = 0, tokens_cached: int = 0,
                cost_usd: Optional[float] = None, wall_clock_s: float = 0.0,
                ts: Optional[float] = None) -> dict[str, Any]:
    """Append one call to the ledger. backend inferred from provider/model if blank; cost
    computed from rates if not supplied. Never raises (DB down → silent no-op)."""
    backend = backend or classify_backend(provider, model)
    if cost_usd is None:
        cost_usd = cost_for(backend, model, tokens_in, tokens_out)
    row = (ts if ts is not None else time.time(), run_id, provider, model, backend,
           int(tokens_in), int(tokens_out), int(tokens_cached), round(float(cost_usd), 6),
           float(wall_clock_s))
    con = _connect()
    if con is None:
        return {"ok": False, "recorded": False}
    try:
        con.execute("INSERT INTO calls (ts, run_id, provider, model, backend, tokens_in, "
                    "tokens_out, tokens_cached, cost_usd, wall_clock_s) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        con.commit()
    except Exception:  # noqa: BLE001
        return {"ok": False, "recorded": False}
    finally:
        con.close()
    return {"ok": True, "recorded": True, "backend": backend, "cost_usd": round(cost_usd, 6)}


def _bucket(backend: str) -> str:
    """Collapse the stored backend into the {local, fabric, cloud} summary bucket."""
    return "local" if backend == "local" else ("fabric" if backend == "fabric" else "cloud")


def cost_summary(run_id: str) -> dict[str, Any]:
    """Per-run breakdown: {total_usd, by_backend: {local, fabric, cloud}, call_count}."""
    by = {"local": 0.0, "fabric": 0.0, "cloud": 0.0}
    total = 0.0
    n = 0
    con = _connect()
    if con is not None:
        try:
            for backend, cost in con.execute(
                    "SELECT backend, cost_usd FROM calls WHERE run_id = ?", (run_id,)):
                n += 1
                c = float(cost or 0.0)
                total += c
                by[_bucket(backend)] = round(by[_bucket(backend)] + c, 6)
        except Exception:  # noqa: BLE001
            pass
        finally:
            con.close()
    return {"run_id": run_id, "total_usd": round(total, 6), "by_backend": by, "call_count": n}


def run_cost(run_id: str) -> float:
    """Total dollars spent so far on this run (the cap reads this before each cloud call)."""
    return cost_summary(run_id)["total_usd"]


def ratio_check() -> dict[str, Any]:
    """7-day rolling: {cost_per_task_7d, cloud_fraction_7d, alert, reason}. alert=True if
    cloud_fraction > 0.40 OR cost_per_task_7d > 0.15. Deterministic; zeros with no history.

    cloud_fraction is the share of CALLS (work) routed to cloud-deepseek — NOT a dollar
    fraction (local + fabric are free, so a dollar fraction is degenerate at ~1.0 whenever
    any cloud call fires; the spec's 0.12/0.71 examples are work fractions)."""
    floor = time.time() - 7 * 86400
    total_cost = 0.0
    n_calls = cloud_calls = 0
    runs: set[str] = set()
    con = _connect()
    if con is not None:
        try:
            for run_id, backend, cost in con.execute(
                    "SELECT run_id, backend, cost_usd FROM calls WHERE ts >= ?", (floor,)):
                total_cost += float(cost or 0.0)
                n_calls += 1
                runs.add(run_id)
                if _bucket(backend) == "cloud":
                    cloud_calls += 1
        except Exception:  # noqa: BLE001
            pass
        finally:
            con.close()
    cloud_fraction = round(cloud_calls / n_calls, 4) if n_calls else 0.0
    cost_per_task = round(total_cost / len(runs), 6) if runs else 0.0
    reasons = []
    if cloud_fraction > ALERT_CLOUD_FRACTION:
        reasons.append("cloud_fraction")
    if cost_per_task > ALERT_COST_PER_TASK:
        reasons.append("cost_per_task")
    return {"cost_per_task_7d": cost_per_task, "cloud_fraction_7d": cloud_fraction,
            "tasks_7d": len(runs), "alert": bool(reasons), "reason": ",".join(reasons)}


def _ratio_log_path() -> str:
    return os.path.expanduser(os.environ.get("HM_RATIO_LOG", "~/.hermes-max/ratio.log"))


def ratio_log_line(run_id: str, ts_str: str = "") -> str:
    """Compute ratio_check + per-run cost, append one line to ratio.log, return the line.
    Called at end of run (Safeguard 3). Observability only — never blocks."""
    rc = ratio_check()
    cost = run_cost(run_id)
    status = f"ALERT:{rc['reason']}" if rc["alert"] else "OK"
    stamp = f"[{ts_str}] " if ts_str else ""
    line = f"{stamp}run={run_id} cost=${cost:.4f} cloud={rc['cloud_fraction_7d']} {status}"
    try:
        Path(_ratio_log_path()).parent.mkdir(parents=True, exist_ok=True)
        with open(_ratio_log_path(), "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    return line


def cost_profiler_stats() -> dict[str, Any]:
    return {"db": _db_path(), "ratio_log": _ratio_log_path(),
            "rates": {"v4_flash": _rates()["flash"], "v4_pro": _rates()["pro"]},
            "alert_thresholds": {"cloud_fraction": ALERT_CLOUD_FRACTION,
                                 "cost_per_task": ALERT_COST_PER_TASK}}
