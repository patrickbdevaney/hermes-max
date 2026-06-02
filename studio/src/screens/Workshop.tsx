// Workshop — S0 placeholder. The full studio bar + embedded Phase 0-7 web UI
// (a Tauri webview pointed at the Python backend) + live status sync lands in S3.
import type { DetectResult } from "../lib/detect";
import type { Project } from "../lib/projects";

const WEB_UI_URL = "http://127.0.0.1:7080";

export function Workshop({ project, detect, onExit }:
  { project: Project; detect: DetectResult | null; onExit: () => void }) {
  void detect;
  return (
    <div className="flex h-screen flex-col bg-bg-base">
      <div className="flex h-9 shrink-0 items-center gap-3 border-b border-ink-800 px-3 text-xs">
        <button type="button" onClick={onExit} className="text-mist-300 hover:text-mist-100">← Projects</button>
        <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
        <span className="font-medium text-mist-100">{project.name}</span>
      </div>
      {/* In the Tauri app this region is a separate WebviewWindow pointed at the
          Python backend; in browser dev we show an iframe so the shell is testable. */}
      <iframe title="hermes-max" src={WEB_UI_URL} className="min-h-0 flex-1 border-0" />
    </div>
  );
}
