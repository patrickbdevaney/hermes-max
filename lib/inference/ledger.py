"""Central cost ledger — every inference call, tokens + USD in $0.000000.

One append-only JSONL plus a queryable rollup. Free providers record real token
counts at $0.000000 (so you see VOLUME even at zero cost); paid calls record the
USD computed from inference.yaml. ``report`` powers ``hm cost``: provider/model/
role breakdown, a free-vs-paid split, and remaining daily free budget per model.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

from . import buckets, config

_lock = threading.Lock()


def _ledger_path() -> str:
    """Read the env at use-time (not import) so overrides + tests are honored."""
    return os.path.expanduser(
        os.environ.get("INFERENCE_LEDGER_PATH", "~/.hermes-max/inference/ledger.jsonl"))


def fmt_usd(x: float) -> str:
    """Always six decimals — research fan-out costs live in the 4th-6th."""
    return f"${float(x):.6f}"


def record(*, role: str, provider: str, model: str, in_tok: int, out_tok: int,
           cached_tok: int = 0, cost_usd: float = 0.0, wall_ms: int = 0,
           mode: str = "", rate_headers: Optional[dict[str, str]] = None,
           thinking_tok: int = 0, ts: Optional[float] = None) -> dict[str, Any]:
    """Append one call to the ledger. Returns the row.

    `thinking_tok` = reasoning/thinking tokens the model spent (role-aware budgets,
    Fix 3) — recorded alongside in/out so plan-quality-vs-reasoning can be studied."""
    row = {
        "ts": ts if ts is not None else time.time(),
        "role": role, "provider": provider, "model": model,
        "in_tok": int(in_tok), "out_tok": int(out_tok),
        "cached_tok": int(cached_tok),
        "thinking_tok": int(thinking_tok),
        "cost_usd": round(float(cost_usd), 6),
        "wall_ms": int(wall_ms), "mode": mode,
        "rate_headers": rate_headers or {},
    }
    with _lock:
        try:
            path = _ledger_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass
    return row


def _rows(since: Optional[float] = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(_ledger_path()) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if since is None or r.get("ts", 0) >= since:
                    rows.append(r)
    except OSError:
        pass
    return rows


def _window_start(window: str) -> Optional[float]:
    now = time.time()
    if window == "today":
        return now - 86400
    if window == "week":
        return now - 7 * 86400
    if window == "month":
        return now - 30 * 86400
    return None                          # "all"


def report(window: str = "today") -> dict[str, Any]:
    """Spend + token rollup for a window. Free-vs-paid split + remaining free RPD.

    Returns a dict ready for `hm cost` to render; all USD are floats (format with
    fmt_usd at the edge)."""
    rows = _rows(_window_start(window))
    total_usd = 0.0
    free_tok = paid_tok = 0
    by_provider: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_role: dict[str, dict[str, Any]] = {}

    def bump(d: dict[str, dict[str, Any]], k: str, usd: float, tok: int) -> None:
        e = d.setdefault(k, {"usd": 0.0, "tok": 0, "calls": 0})
        e["usd"] += usd
        e["tok"] += tok
        e["calls"] += 1

    for r in rows:
        usd = float(r.get("cost_usd", 0.0))
        tok = int(r.get("in_tok", 0)) + int(r.get("out_tok", 0))
        total_usd += usd
        if usd > 0:
            paid_tok += tok
        else:
            free_tok += tok
        bump(by_provider, r.get("provider", "?"), usd, tok)
        bump(by_model, f"{r.get('provider','?')}.{r.get('model','?')}", usd, tok)
        bump(by_role, r.get("role", "?"), usd, tok)

    # remaining free daily budget per free model in the config
    free_budget: dict[str, Optional[int]] = {}
    for pname, pblock in config.providers().items():
        if config.tier(pname) != "free":
            continue
        for mkey in (pblock.get("models") or {}):
            rem = buckets.remaining_rpd(pname, mkey)
            if rem is not None:
                free_budget[f"{pname}.{mkey}"] = rem

    return {
        "window": window,
        "calls": len(rows),
        "total_usd": round(total_usd, 6),
        "free_tok": free_tok,
        "paid_tok": paid_tok,
        "by_provider": by_provider,
        "by_model": by_model,
        "by_role": by_role,
        "free_budget_remaining": free_budget,
    }
