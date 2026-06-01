// PART III.1 Activity — the history of runs this UI launched (from the local run
// journal). Each row deep-links to #/run/:id, where the live stream re-opens from
// that run's log offset (replay is best-effort: if the server has cycled, the Run
// view says so honestly). No secrets here — only the prompts the operator typed.
import { useEffect, useState } from "react";
import { Badge, Dot } from "./ui";
import { hrefFor } from "../lib/router";
import { journal } from "../lib/runjournal";
import type { RunRecord } from "../lib/runjournal";

function ago(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function ActivityPage() {
  const [runs, setRuns] = useState<RunRecord[]>([]);
  useEffect(() => { setRuns(journal.list()); }, []);

  function remove(id: string) {
    journal.remove(id);
    setRuns(journal.list());
  }

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Activity</h1>
          <p className="mt-1 text-sm text-mist-400">Runs you've launched from this browser.</p>
        </div>
        <a href={hrefFor("run")} className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 hover:opacity-90">
          + new run
        </a>
      </header>

      {runs.length === 0 ? (
        <div className="rounded-lg border border-ink-800 bg-ink-900 p-8 text-center text-sm text-mist-400">
          No runs yet. <a href={hrefFor("run")} className="text-accent">Start one →</a>
        </div>
      ) : (
        <ul className="space-y-2">
          {runs.map((r) => (
            <li key={r.run_id} className="rounded-lg border border-ink-800 bg-ink-900 p-3">
              <div className="flex items-start gap-3">
                <a href={hrefFor("run", r.run_id)} className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-mist-400">{r.run_id}</span>
                    <Badge tone="muted">{r.turns} turn{r.turns === 1 ? "" : "s"}</Badge>
                    {r.mode && <Badge tone="accent">{r.mode}</Badge>}
                  </div>
                  <p className="mt-1 truncate text-sm text-mist-100">{r.prompt}</p>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-mist-400">{r.cwd}</p>
                </a>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <span className="flex items-center gap-1.5 text-[11px] text-mist-400">
                    <Dot tone="muted" /> {ago(r.start_ts)}
                  </span>
                  <button
                    type="button"
                    onClick={() => remove(r.run_id)}
                    className="text-[11px] text-mist-400 hover:text-bad"
                  >
                    remove
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
