// First-run (detect-and-bless) — S0 placeholder; the full three-state experience
// (endpoint ready / connect AI / install hermes) lands in S1.
import type { DetectResult } from "../lib/detect";

export function FirstRun({ detect, onReady }: { detect: DetectResult | null; onReady: () => void }) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-5 bg-bg-base px-6 text-center">
      <div className="flex items-center gap-2">
        <span className="h-3 w-3 rounded-full bg-conductor" aria-hidden />
        <span className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Hermes Studio</span>
      </div>
      <p className="max-w-sm text-sm text-mist-400">
        {detect?.endpoint_reachable ? "Your AI is ready." : "Connect your AI to get started."}
      </p>
      <button type="button" onClick={onReady}
        className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">
        Open a project →
      </button>
    </div>
  );
}
