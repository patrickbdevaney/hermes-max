// Phase 4.4 — the checkpoint timeline. Lists the conductor's verified git
// checkpoints and lets you FORK from any one (safe: auto-stashes, branches at the
// commit — your current work is preserved). Plain-language: "go back to here and
// try a different way".
import { useEffect, useState } from "react";
import { checkpoints, forkCheckpoint, type Checkpoint } from "../lib/project";

function ago(ts: number): string {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function CheckpointsPanel({ cwd, onClose }: { cwd: string; onClose: () => void }) {
  const [cps, setCps] = useState<Checkpoint[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => { checkpoints(cwd).then(setCps).catch((e) => setErr((e as Error).message)); }, [cwd]);

  async function fork(c: Checkpoint) {
    setNote(null);
    try { const r = await forkCheckpoint(cwd, c.commit, c.short); setNote(`Forked to a new branch: ${r.branch}`); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-6" onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl border border-ink-700 bg-ink-overlay p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold text-mist-100">Checkpoints</h2>
          <button type="button" onClick={onClose} className="text-mist-500 hover:text-mist-100" aria-label="close">✕</button>
        </div>
        {note && <p className="mb-2 rounded-md border border-good/40 bg-good/10 px-3 py-1.5 text-xs text-good">{note}</p>}
        {err ? <p className="text-xs text-bad">{err}</p>
          : !cps ? <p className="text-sm text-mist-400">loading…</p>
          : cps.length === 0 ? <p className="text-sm text-mist-500">No checkpoints yet — they appear as the agent commits verified work.</p>
          : (
            <ul className="max-h-[50vh] space-y-1 overflow-auto">
              {cps.map((c) => (
                <li key={c.commit} className="flex items-center gap-3 rounded-md border border-ink-800 bg-ink-900 px-3 py-2 text-xs">
                  <span className="min-w-0 flex-1 truncate text-mist-200">{c.subject}</span>
                  <span className="shrink-0 font-mono text-mist-500">{c.short} · {ago(c.ts)}</span>
                  <button type="button" onClick={() => fork(c)}
                    className="shrink-0 rounded-md border border-accent/40 px-2 py-0.5 text-accent hover:bg-accent-soft/15">Fork from here</button>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}
