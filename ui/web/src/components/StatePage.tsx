// Phase 6.1 — the Memory/state inspector. Renders the agent's on-disk state
// files live (EXECUTION_STATE.json, .hermes-conductor/state.json, .hermes.md,
// PLAN.md) — the verify-parse fallback ground truth made visible. Auto-refreshes
// so you watch state change as the run advances.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { journal } from "../lib/runjournal";
import { CodeBlock } from "./run/CodeBlock";
import { SkeletonRows, ErrorState, EmptyMoment } from "./ui";
import type { StateFilesPayload, StateFile } from "../types";

export function StatePage({ runId }: { runId: string | null }) {
  const [cwd, setCwd] = useState(() => (runId ? journal.get(runId)?.cwd ?? "" : ""));
  const [data, setData] = useState<StateFilesPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!cwd) return;
    let stop = false;
    const tick = () => api.state(cwd)
      .then((d) => { if (!stop) { setData(d); setErr(null); } })
      .catch((e) => { if (!stop) setErr((e as Error).message); });
    tick();
    const id = setInterval(tick, 4000);
    return () => { stop = true; clearInterval(id); };
  }, [cwd]);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">State</h1>
        <p className="mt-1 text-sm text-mist-400">The agent's on-disk memory — live ground truth.</p>
      </header>

      <input value={cwd} onChange={(e) => setCwd(e.target.value)} placeholder="/path/to/working/dir"
        className="w-full rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />

      {!cwd ? <EmptyMoment icon="◇" title="No directory" hint="Enter a run's working directory to inspect its state files." />
        : err ? <ErrorState detail={err} />
        : !data ? <SkeletonRows rows={4} />
        : <div className="space-y-3">{data.files.map((f) => <FileBlock key={f.name} f={f} />)}</div>}
    </div>
  );
}

function FileBlock({ f }: { f: StateFile }) {
  const [open, setOpen] = useState(f.exists);
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <span className={f.exists ? "text-good" : "text-mist-600"}>{f.exists ? "●" : "○"}</span>
        <span className="font-mono text-xs text-mist-200">{f.name}</span>
        {f.exists && f.size != null && <span className="text-[10px] text-mist-500">{f.size.toLocaleString()} B</span>}
        {!f.exists && <span className="text-[10px] text-mist-500">absent</span>}
        <span className="ml-auto text-[10px] text-mist-500">{open ? "▲" : "▼"}</span>
      </button>
      {open && f.exists && (
        <div className="border-t border-ink-800 p-2">
          {f.json !== undefined
            ? <CodeBlock text={JSON.stringify(f.json, null, 2)} lang="json" />
            : <CodeBlock text={f.content || ""} lang={f.name.endsWith(".md") ? "markdown" : undefined} />}
        </div>
      )}
    </section>
  );
}
