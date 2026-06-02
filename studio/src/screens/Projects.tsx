// Projects (the primary loop) — S0 placeholder; the full card grid + new-project
// flow lands in S2.
import type { Project } from "../lib/projects";

export function Projects({ onOpen, onSettings }: { onOpen: (p: Project) => void; onSettings: () => void }) {
  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Your projects</h1>
        <button type="button" onClick={onSettings} className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-300 hover:bg-ink-850">Settings</button>
      </header>
      <button type="button"
        onClick={() => onOpen({ id: "demo", name: "Untitled", dir: "", created_ts: Date.now() })}
        className="rounded-lg border border-dashed border-ink-700 px-4 py-6 text-sm text-mist-300 hover:border-accent hover:text-mist-100">
        + New Project
      </button>
    </div>
  );
}
