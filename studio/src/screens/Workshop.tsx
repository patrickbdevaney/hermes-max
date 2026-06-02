// The workshop: a thin studio bar over the full Phase 0-7 web UI. The web UI
// loads in an iframe pointed at the Python backend it's served by, so it talks
// to its own same-origin backend and is FULLY functional and unmodified — the
// composer, conductor swimlane, cost shadow, controls, everything. The studio
// bar adds the friendly chrome: ← Projects, an editable project name, a plain-
// language status phrase, and the live cost — fed by Rust's livelog bridge.
import { useEffect, useRef, useState } from "react";
import { renameProject, openProjectFolder, type Project } from "../lib/projects";
import { startWorkshop, stopWorkshop, onWorkshopStatus, type WorkshopStatus } from "../lib/workshop";
import { computeShadow, fmtMoney, fmtMultiple } from "../lib/shadow";
import { StatusDot } from "../components/StatusDot";
import { CompletionCard } from "../components/CompletionCard";
import type { DetectResult } from "../lib/detect";

const WEB_UI_URL = "http://127.0.0.1:7080";

export function Workshop({ project, detect, onExit }:
  { project: Project; detect: DetectResult | null; onExit: () => void }) {
  void detect;
  const [status, setStatus] = useState<WorkshopStatus | null>(null);
  const [name, setName] = useState(project.name);
  const [receipt, setReceipt] = useState<WorkshopStatus | null>(null);
  const wasRunning = useRef(false);

  useEffect(() => {
    startWorkshop(project.dir).catch(() => void 0);
    const un = onWorkshopStatus((s) => {
      setStatus(s);
      // S5: when a run settles into completion, surface the receipt once.
      if (wasRunning.current && !s.running && s.done) setReceipt(s);
      wasRunning.current = s.running;
    });
    return () => { un.then((f) => f()); stopWorkshop().catch(() => void 0); };
  }, [project.dir]);

  function exit() {
    if (status?.running && !confirm("A build is still running. Leave the workshop anyway?")) return;
    onExit();
  }
  function commitName() {
    const n = name.trim();
    if (n && n !== project.name) renameProject(project.id, n).catch(() => void 0);
  }

  const running = !!status?.running;
  const phrase = running ? status!.phrase : status?.done ? "All done ✓" : "What should I build?";
  const cost = status?.cost_usd ?? 0;
  const shadow = computeShadow(cost, status?.tokens ?? 0);

  return (
    <div className="flex h-screen flex-col bg-bg-base">
      <div className="flex h-9 shrink-0 items-center gap-3 border-b border-ink-800 px-3 text-xs">
        <button type="button" onClick={exit} className="shrink-0 text-mist-300 hover:text-mist-100">← Projects</button>
        <StatusDot tone={running ? "accent" : status?.done ? "good" : "muted"} pulse={running} />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={commitName}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          className="w-40 shrink-0 truncate bg-transparent font-medium text-mist-100 outline-none focus:text-accent"
          aria-label="project name"
        />
        <span className="text-mist-600">•</span>
        <span className="min-w-0 flex-1 truncate text-mist-300">{phrase}</span>
        {status && status.total > 0 && (
          <span className="shrink-0 font-mono text-mist-500">{status.step}/{status.total}</span>
        )}
        <span className="shrink-0 font-mono text-mist-300" title={shadow.savedUsd > 0 ? `saved ${fmtMoney(shadow.savedUsd)} (${fmtMultiple(shadow.multiple)}) vs premium AI` : undefined}>
          {fmtMoney(cost)}
        </span>
      </div>

      {/* The full web UI — unmodified, talking to its own same-origin backend. */}
      <iframe title="hermes-max" src={WEB_UI_URL} className="min-h-0 flex-1 border-0" />

      {receipt && (
        <CompletionCard
          name={project.name}
          status={receipt}
          onClose={() => setReceipt(null)}
          onOpenFolder={() => openProjectFolder(project.dir)}
        />
      )}
    </div>
  );
}
