// Projects — the primary loop. A card grid (the "+ New Project" card is always
// first). Opening a project enters the workshop. An exist-OK fix-it banner shows
// at the top when the configured AI has gone unreachable (S1.3) — advisory, not
// a wall.
import { useEffect, useState } from "react";
import { listProjects, createProject, pickDirectory, type Project } from "../lib/projects";
import { probeCapabilities, type DetectResult } from "../lib/detect";
import { ProjectCard } from "../components/ProjectCard";

export function Projects({ onOpen, onSettings }: { onOpen: (p: Project) => void; onSettings: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [detect, setDetect] = useState<DetectResult | null>(null);
  const [creating, setCreating] = useState(false);

  const refresh = () => listProjects().then(setProjects).catch(() => setProjects([]));
  useEffect(() => { refresh(); probeCapabilities().then(setDetect).catch(() => void 0); }, []);

  const aiDown = detect && detect.suggested_mode === "NeedsSetup";

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Your projects</h1>
        <button type="button" onClick={onSettings}
          className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-300 hover:bg-ink-850">Settings</button>
      </header>

      {aiDown && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-warn/40 bg-warn-soft/15 px-3 py-2 text-xs text-warn">
          <span>Your AI isn't responding — projects still open, but builds won't run until it's reconnected.</span>
          <button type="button" onClick={onSettings} className="shrink-0 underline">Check settings</button>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <button type="button" onClick={() => setCreating(true)}
          className="flex min-h-[120px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-ink-700 text-sm text-mist-300 transition-colors hover:border-accent hover:text-mist-100">
          <span className="text-2xl">+</span> New Project
        </button>
        {projects.map((p) => <ProjectCard key={p.id} project={p} onOpen={onOpen} onChanged={refresh} />)}
      </div>

      {creating && <NewProject onCancel={() => setCreating(false)} onCreated={(p) => { setCreating(false); refresh(); onOpen(p); }} />}
    </div>
  );
}

function NewProject({ onCancel, onCreated }: { onCancel: () => void; onCreated: (p: Project) => void }) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [dir, setDir] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function pick() {
    const d = await pickDirectory();
    if (d) setDir(d);
  }
  async function go() {
    setBusy(true); setErr(null);
    try {
      const p = await createProject(name.trim(), mode === "existing" ? dir : null, mode === "new");
      onCreated(p);
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-6" onClick={onCancel}>
      <div className="w-full max-w-md rounded-xl border border-ink-700 bg-ink-overlay p-5" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 font-display text-lg font-semibold text-mist-100">What are you building?</h2>

        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
          placeholder="e.g. A todo app with user accounts"
          className="w-full rounded-md border border-ink-700 bg-ink-input px-3 py-2 text-sm text-mist-100 outline-none focus:border-accent" />

        <p className="mt-4 mb-1 text-xs text-mist-400">Where should I put the files?</p>
        <div className="flex flex-col gap-2 text-sm">
          <label className="flex items-center gap-2 text-mist-200">
            <input type="radio" checked={mode === "new"} onChange={() => setMode("new")} className="accent-current text-accent" />
            Create a new folder for me
          </label>
          <label className="flex items-center gap-2 text-mist-200">
            <input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")} className="accent-current text-accent" />
            Use an existing folder
          </label>
          {mode === "existing" && (
            <div className="flex gap-2 pl-6">
              <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="/path/to/folder"
                className="flex-1 rounded-md border border-ink-700 bg-ink-input px-2 py-1.5 font-mono text-xs text-mist-100 outline-none focus:border-accent" />
              <button type="button" onClick={pick} className="rounded-md border border-ink-700 px-2 py-1.5 text-xs text-mist-200 hover:bg-ink-850">Browse…</button>
            </div>
          )}
        </div>

        {err && <p className="mt-3 text-xs text-bad">{err}</p>}

        <div className="mt-5 flex items-center justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-md px-3 py-1.5 text-xs text-mist-400 hover:text-mist-100">Cancel</button>
          <button type="button" onClick={go} disabled={busy || !name.trim() || (mode === "existing" && !dir.trim())}
            className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-ink-950 hover:opacity-90 disabled:opacity-40">
            {busy ? "Setting up…" : "Let's go →"}
          </button>
        </div>
      </div>
    </div>
  );
}
