// A project card — plain-language status, last build, cost, and a Continue
// action. The ⋯ menu opens project actions (open folder, rename, forget).
import { useState } from "react";
import { openProjectFolder, renameProject, deleteProject, type Project } from "../lib/projects";
import { StatusDot } from "./StatusDot";

const STATUS: Record<string, { tone: "good" | "accent" | "warn" | "muted"; label: string; pulse?: boolean }> = {
  ready: { tone: "muted", label: "Ready" },
  building: { tone: "accent", label: "Building…", pulse: true },
  done: { tone: "good", label: "Done ✓" },
  attention: { tone: "warn", label: "Needs attention ⚠" },
};

function ago(ts?: number | null): string {
  if (!ts) return "Not built yet";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "Last built just now";
  if (s < 3600) return `Last built ${Math.round(s / 60)}m ago`;
  if (s < 86400) return `Last built ${Math.round(s / 3600)}h ago`;
  return `Last built ${Math.round(s / 86400)}d ago`;
}

export function ProjectCard({ project, onOpen, onChanged }:
  { project: Project; onOpen: (p: Project) => void; onChanged: () => void }) {
  const [menu, setMenu] = useState(false);
  const st = STATUS[project.last_status ?? "ready"] ?? STATUS.ready;

  const cost = project.lifetime_cost_usd ?? 0;
  const sub = [
    ago(project.last_run_ts),
    project.last_step && project.last_total ? `step ${project.last_step}/${project.last_total}` : null,
    cost > 0 ? `$${cost.toFixed(2)}` : null,
  ].filter(Boolean).join(" · ");

  async function rename() {
    setMenu(false);
    const name = prompt("Rename project", project.name);
    if (name && name.trim()) { await renameProject(project.id, name.trim()); onChanged(); }
  }
  async function forget() {
    setMenu(false);
    if (confirm(`Forget "${project.name}"? This only removes it from Studio — your files are kept.`)) {
      await deleteProject(project.id); onChanged();
    }
  }

  return (
    <div className="flex flex-col rounded-lg border border-ink-800 bg-ink-900 p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="truncate font-medium text-mist-100" title={project.name}>{project.name}</h3>
        <span className="flex shrink-0 items-center gap-1.5 text-[11px] text-mist-400">
          <StatusDot tone={st.tone} pulse={st.pulse} />{st.label}
        </span>
      </div>
      {project.prompt && <p className="mt-1 line-clamp-2 text-sm text-mist-400">“{project.prompt}”</p>}
      <p className="mt-2 font-mono text-[11px] text-mist-500" title={cost > 0 ? "saved vs premium AI shown in the receipt" : undefined}>{sub}</p>

      <div className="mt-3 flex items-center gap-2">
        <button type="button" onClick={() => onOpen(project)}
          className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 hover:opacity-90">
          {project.last_run_ts ? "▶ Continue" : "▶ Open"}
        </button>
        <div className="relative">
          <button type="button" onClick={() => setMenu((m) => !m)} aria-label="project menu"
            className="rounded-md border border-ink-700 px-2 py-1.5 text-xs text-mist-300 hover:bg-ink-850">⋯</button>
          {menu && (
            <div className="absolute left-0 z-10 mt-1 w-40 rounded-md border border-ink-700 bg-ink-overlay py-1 text-xs shadow-lg">
              <button type="button" onClick={() => { setMenu(false); openProjectFolder(project.dir); }}
                className="block w-full px-3 py-1.5 text-left text-mist-200 hover:bg-ink-850">Open folder</button>
              <button type="button" onClick={rename} className="block w-full px-3 py-1.5 text-left text-mist-200 hover:bg-ink-850">Rename</button>
              <button type="button" onClick={forget} className="block w-full px-3 py-1.5 text-left text-bad hover:bg-ink-850">Forget</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
