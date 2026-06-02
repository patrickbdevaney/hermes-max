// PART III.1 Activity — the history of runs, from ANY origin (Fix 4): runs launched
// here, in `hm dev`, or bare in a terminal (via the shell wrapper) all surface through
// the server registry. Active runs show a live dot and appear within ~1s of starting.
// Each row deep-links to #/run/:id, where the live stream re-opens from that run's
// recorded global-log offset. The local journal supplies prompt labels for UI-launched
// runs. No secrets — only cwd/prompt/mode/timestamps.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Badge, Dot } from "./ui";
import { hrefFor } from "../lib/router";
import { journal } from "../lib/runjournal";
import type { RunSummary } from "../types";

function ago(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

interface Row extends RunSummary { _prompt: string; _ts: number }

function merge(server: RunSummary[]): Row[] {
  const j = new Map(journal.list().map((r) => [r.run_id, r]));
  const seen = new Set<string>();
  const rows: Row[] = [];
  for (const s of server) {
    seen.add(s.run_id);
    const jr = j.get(s.run_id);
    rows.push({
      ...s,
      _prompt: s.prompt || jr?.prompt || "(no prompt recorded)",
      _ts: (s.start_ts ? s.start_ts * 1000 : jr?.start_ts) || 0,
    });
  }
  // journal-only runs the server no longer has (e.g. after a restart) — still listable
  for (const jr of journal.list()) {
    if (seen.has(jr.run_id)) continue;
    rows.push({ run_id: jr.run_id, cwd: jr.cwd, mode: jr.mode, origin: "ui",
                status: "exited", active: false, _prompt: jr.prompt, _ts: jr.start_ts });
  }
  return rows.sort((a, b) => b._ts - a._ts);
}

export function ActivityPage() {
  const [rows, setRows] = useState<Row[]>([]);

  useEffect(() => {
    let stop = false;
    const tick = () => api.runs()
      .then((r) => { if (!stop) setRows(merge(r.runs)); })
      .catch(() => { if (!stop) setRows(merge([])); });
    tick();
    const id = setInterval(tick, 2000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  const live = rows.filter((r) => r.active);
  const past = rows.filter((r) => !r.active);

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Activity</h1>
          <p className="mt-1 text-sm text-mist-400">
            Every run — launched here, in <span className="font-mono">hm dev</span>, or in a terminal.
          </p>
        </div>
        <a href={hrefFor("run")} className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 hover:opacity-90">
          + new run
        </a>
      </header>

      {live.length > 0 && (
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-mist-400">
            <Dot tone="good" pulse /> Live ({live.length})
          </h2>
          <ul className="space-y-2">{live.map((r) => <RunRow key={r.run_id} r={r} />)}</ul>
        </section>
      )}

      <section>
        {live.length > 0 && <h2 className="mb-2 text-xs uppercase tracking-wide text-mist-400">Recent</h2>}
        {past.length === 0 && live.length === 0 ? (
          <div className="rounded-lg border border-ink-800 bg-ink-900 p-8 text-center text-sm text-mist-400">
            No runs yet. <a href={hrefFor("run")} className="text-accent">Start one →</a>
            <div className="mt-2 text-[11px]">Terminal runs appear here too — see Setup for the shell opt-in.</div>
          </div>
        ) : (
          <ul className="space-y-2">{past.map((r) => <RunRow key={r.run_id} r={r} />)}</ul>
        )}
      </section>
    </div>
  );
}

function RunRow({ r }: { r: Row }) {
  return (
    <li className="rounded-lg border border-ink-800 bg-ink-900 p-3">
      <div className="flex items-start gap-3">
        <a href={hrefFor("run", r.run_id)} className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-mist-400">{r.run_id}</span>
            {r.active
              ? <Badge tone="good"><Dot tone="good" pulse />running</Badge>
              : <Badge tone="muted">{r.status || "exited"}</Badge>}
            {r.origin && <Badge tone="muted">{r.origin}</Badge>}
            {r.mode && <Badge tone="accent">{r.mode}</Badge>}
          </div>
          <p className="mt-1 truncate text-sm text-mist-100">{r._prompt}</p>
          {r.cwd && <p className="mt-0.5 truncate font-mono text-[11px] text-mist-400">{r.cwd}</p>}
        </a>
        <span className="shrink-0 text-[11px] text-mist-400">{r._ts ? ago(r._ts) : ""}</span>
      </div>
    </li>
  );
}
