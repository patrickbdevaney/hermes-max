"""Phase 4 — persistent, searchable run history over the JSONL livelog.

The append-only JSONL livelog stays the LIVE source of truth (offset-addressable,
perfect for streaming + replay-on-reconnect). This module is a POST-COMPLETION
index only: on run completion the run's slice of the global livelog is ingested
into SQLite — one row per translated SSE event + a `runs` summary row — and FTS5
is enabled for full-text search across runs. Python's `sqlite3` is stdlib, so the
zero-pip-dependency backend constraint is preserved (no DuckDB, no ORM).

Replay re-emits the SAME translated events the live stream produced (we ingest by
running each livelog record through feeds._translate), so the frontend reducer
sees an identical event sequence whether live or replayed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

from . import feeds

_lock = threading.Lock()
_FTS_OK: Optional[bool] = None   # detected once on first connect


def db_path() -> str:
    d = os.path.expanduser(os.environ.get("HERMES_MAX_STATE_DIR", "~/.hermes-max")) + "/ui"
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "history.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _FTS_OK
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
          run_id TEXT PRIMARY KEY,
          prompt TEXT, cwd TEXT, mode TEXT, origin TEXT,
          start_ts REAL, end_ts REAL, status TEXT,
          step_count INTEGER, turn_count INTEGER,
          cost_usd REAL, free_tok INTEGER, paid_tok INTEGER,
          conductor_fires INTEGER, verify_pass INTEGER, verify_fail INTEGER,
          tokps_peak REAL,
          offset_start INTEGER, offset_end INTEGER, ingested_ts REAL
        );
        CREATE TABLE IF NOT EXISTS events (
          run_id TEXT, seq INTEGER, ts REAL, hms TEXT,
          event TEXT, data TEXT,
          PRIMARY KEY (run_id, seq)
        );
        """
    )
    if _FTS_OK is None:
        try:
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
                  USING fts5(run_id UNINDEXED, text);
                """
            )
            _FTS_OK = True
        except sqlite3.OperationalError:
            _FTS_OK = False  # this sqlite build lacks FTS5 — fall back to LIKE
    conn.commit()


# ── searchable text flattening (what FTS indexes per event) ───────────────────
def _event_text(event: str, data: dict[str, Any]) -> str:
    bits = [event]
    for k in ("tool", "reason", "plain_text", "path", "cmd", "model", "tier",
              "result", "result_summary", "input_summary", "kind", "phase", "file"):
        v = data.get(k)
        if v:
            bits.append(str(v))
    return " ".join(bits)[:600]


def _read_slice(offset_start: int, offset_end: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """Read livelog records from `offset_start` to `offset_end` (or current EOF).
    Bounding lets backfill scope each run to its own slice of the shared log."""
    path = feeds.livelog_path()
    recs: list[dict[str, Any]] = []
    try:
        eof = os.path.getsize(path)
    except OSError:
        return recs, offset_start
    end = eof if offset_end is None else min(offset_end, eof)
    try:
        with open(path, "r") as f:
            f.seek(offset_start)
            while True:
                if f.tell() >= end:
                    break
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return recs, end


def ingest_run(run: dict[str, Any], offset_end: int | None = None) -> bool:
    """Ingest a (completed) run's livelog slice into SQLite. Idempotent — re-ingest
    replaces prior rows. Best-effort: never raises into the caller. `offset_end`
    bounds the slice (used by backfill to separate interleaved runs)."""
    try:
        run_id = run["run_id"]
        if run_id == "live":
            return False  # the synthetic attach run isn't a real, indexable run
        offset_start = int(run.get("start_offset", 0))
        recs, offset_end = _read_slice(offset_start, offset_end)
        if not recs:
            return False

        # Re-translate exactly as the live stream does, so replay is identical.
        calls: dict[str, list[int]] = {}
        seq = [0]
        events: list[tuple[int, float, str, str, str]] = []
        steps = turns = fires = vpass = vfail = 0
        cost_usd = free_tok = paid_tok = 0
        tokps_peak = 0.0
        n = 0
        for rec in recs:
            for event, data in feeds._translate(rec, run_id, calls, seq):
                n += 1
                ts = float(data.get("ts") or rec.get("ts") or 0.0)
                hms = str(data.get("hms") or rec.get("hms") or "")
                events.append((n, ts, hms, event, json.dumps(data, default=str)))
                if event == "cost":
                    cost_usd = float(data.get("total_usd") or cost_usd)
                    free_tok = int(data.get("free_tok") or free_tok)
                    paid_tok = int(data.get("paid_tok") or paid_tok)
                elif event == "conductor":
                    ce = data.get("event")
                    if ce == "llm_call":
                        turns += 1
                        steps = max(steps, int(data.get("step") or 0))
                    elif ce == "trigger":
                        fires += 1
                    elif ce == "verify_pass":
                        vpass += 1
                    elif ce == "verify_fail":
                        vfail += 1

        # Carry the registry's status (running / exited / attached) so an
        # incomplete run is labelled honestly, not as a clean completion.
        status = str(run.get("status") or "exited")
        end_ts = time.time()
        summary = (
            run_id, run.get("prompt"), run.get("cwd"), run.get("mode"),
            run.get("origin", "?"), float(run.get("start_ts", 0.0)), end_ts, status,
            steps, turns, cost_usd, free_tok, paid_tok, fires, vpass, vfail, tokps_peak,
            offset_start, offset_end, time.time(),
        )

        with _lock:
            conn = _connect()
            try:
                conn.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
                conn.execute(
                    "INSERT OR REPLACE INTO runs VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", summary,
                )
                conn.executemany(
                    "INSERT INTO events (run_id, seq, ts, hms, event, data) VALUES (?,?,?,?,?,?)",
                    [(run_id, *e) for e in events],
                )
                if _FTS_OK:
                    conn.execute("DELETE FROM events_fts WHERE run_id = ?", (run_id,))
                    conn.executemany(
                        "INSERT INTO events_fts (run_id, text) VALUES (?, ?)",
                        [(run_id, _event_text(e[3], json.loads(e[4]))) for e in events],
                    )
                conn.commit()
            finally:
                conn.close()
        return True
    except Exception:  # noqa: BLE001 - history is best-effort, never break a run
        return False


def _indexed_ids() -> set[str]:
    with _lock:
        conn = _connect()
        try:
            return {r["run_id"] for r in conn.execute("SELECT run_id FROM runs")}
        finally:
            conn.close()


def sync_registry(reindex: bool = False) -> int:
    """Index registered runs into SQLite so history is never blank or stale:
    terminal / hm dev / past UI runs, and runs that EXITED without a clean
    completion (interrupted, crashed, server-down). Each run is scoped to its own
    slice of the shared livelog by bounding at the next run's start offset.

    Currently-ACTIVE runs are skipped — they're surfaced live from the registry
    and re-ingested on completion (so the index never holds a half-written run).
    With reindex=True, already-indexed inactive runs are refreshed too. Returns
    the number ingested."""
    from . import runs as _runs
    try:
        summaries = _runs.list_runs(limit=500)
    except Exception:  # noqa: BLE001
        return 0
    indexed = _indexed_ids()
    rows: list[tuple[dict[str, Any], bool]] = []
    for s in summaries:
        rid = s.get("run_id")
        if not rid or rid == "live":
            continue
        d = _runs.get_run(rid)
        if d:
            d = {**d, "status": s.get("status"), "prompt": d.get("prompt") or s.get("prompt")}
            rows.append((d, bool(s.get("active"))))
    rows.sort(key=lambda t: int(t[0].get("start_offset", 0)))
    starts = [int(d.get("start_offset", 0)) for d, _ in rows]
    n = 0
    for i, (d, active) in enumerate(rows):
        if active:
            continue                              # live run → shown live, not indexed
        if d["run_id"] in indexed and not reindex:
            continue                              # already indexed
        end = starts[i + 1] if i + 1 < len(starts) and starts[i + 1] > starts[i] else None
        if ingest_run(d, offset_end=end):
            n += 1
    return n


# Back-compat alias for the earlier name.
def backfill() -> int:
    return sync_registry(reindex=True)


def list_history(q: str = "", status: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Run summaries, newest first. `q` does a full-text search (FTS5 when
    available, LIKE fallback) across event text + prompt. Registered-but-not-yet-
    indexed runs (incl. incomplete ones) are folded in on each call."""
    sync_registry()
    with _lock:
        conn = _connect()
        try:
            run_ids: Optional[set[str]] = None
            if q.strip():
                run_ids = set()
                if _FTS_OK:
                    try:
                        for r in conn.execute(
                            "SELECT DISTINCT run_id FROM events_fts WHERE events_fts MATCH ? LIMIT 5000",
                            (q,),
                        ):
                            run_ids.add(r["run_id"])
                    except sqlite3.OperationalError:
                        pass  # malformed FTS query — fall through to prompt LIKE
                like = f"%{q}%"
                for r in conn.execute("SELECT run_id FROM runs WHERE prompt LIKE ?", (like,)):
                    run_ids.add(r["run_id"])
                if not _FTS_OK:
                    for r in conn.execute(
                        "SELECT DISTINCT run_id FROM events WHERE data LIKE ? LIMIT 5000", (like,)
                    ):
                        run_ids.add(r["run_id"])

            sql = "SELECT * FROM runs"
            clauses, args = [], []
            if status:
                clauses.append("status = ?")
                args.append(status)
            if run_ids is not None:
                if not run_ids:
                    return []
                clauses.append("run_id IN (%s)" % ",".join("?" * len(run_ids)))
                args.extend(run_ids)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY start_ts DESC LIMIT ?"
            args.append(limit)
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    """A run's summary + its full translated event list, for replay/scrubbing."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            events = [
                {"event": r["event"], "data": json.loads(r["data"]), "seq": r["seq"],
                 "ts": r["ts"], "hms": r["hms"]}
                for r in conn.execute(
                    "SELECT seq, ts, hms, event, data FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
                )
            ]
            return {"summary": dict(row), "events": events}
        finally:
            conn.close()
