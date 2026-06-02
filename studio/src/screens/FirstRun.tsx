// First-run: detect-and-bless. ONE welcoming screen that branches on what was
// probed — not a wizard. Three states:
//   A  endpoint already reachable → "Your AI is ready" → open a project
//   B  nothing configured → connect a local endpoint OR a cloud provider key
//   C  hermes binary missing → install prompt + re-check
import { useState } from "react";
import { openUrl } from "../lib/firstrun";
import { probeCapabilities, type DetectResult } from "../lib/detect";
import { ConnectAI } from "../components/ConnectAI";
import { StatusDot } from "../components/StatusDot";

function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try { return new URL(url).host; } catch { return url; }
}

export function FirstRun({ detect, onReady }: { detect: DetectResult | null; onReady: () => void }) {
  const [d, setD] = useState<DetectResult | null>(detect);
  // recheck after the user installs hermes / changes something
  const recheck = () => probeCapabilities().then(setD).catch(() => void 0);

  const state: "A" | "B" | "C" =
    d && !d.hermes_present ? "C" : d?.endpoint_reachable ? "A" : "B";

  return (
    <div className="flex h-screen items-center justify-center bg-bg-base px-6">
      <div className="w-full max-w-lg">
        <div className="mb-6 flex items-center gap-2">
          <StatusDot tone="accent" />
          <span className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Hermes Studio</span>
        </div>

        {state === "A" && <ReadyState d={d!} onReady={onReady} />}
        {state === "B" && <ConnectState onConnected={onReady} />}
        {state === "C" && <InstallState onRecheck={recheck} />}
      </div>
    </div>
  );
}

function ReadyState({ d, onReady }: { d: DetectResult; onReady: () => void }) {
  return (
    <div className="space-y-3">
      <h1 className="text-lg font-medium text-mist-100">Your AI is ready.</h1>
      {d.endpoint_model && <p className="font-mono text-sm text-mist-300">{d.endpoint_model}</p>}
      <p className="text-sm text-mist-400">Running at {hostOf(d.endpoint_url) || "your endpoint"}</p>
      <button type="button" onClick={onReady}
        className="mt-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">
        Open a project →
      </button>
    </div>
  );
}

function ConnectState({ onConnected }: { onConnected: () => void }) {
  const [done, setDone] = useState(false);
  if (done) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-good"><StatusDot tone="good" /> Connected</div>
        <button type="button" onClick={onConnected}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">Open a project →</button>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <h1 className="text-lg font-medium text-mist-100">Connect your AI to get started.</h1>
      <ConnectAI onConnected={() => setDone(true)} />
    </div>
  );
}

function InstallState({ onRecheck }: { onRecheck: () => void }) {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-medium text-mist-100">One thing to install first.</h1>
      <p className="text-sm text-mist-400">
        Hermes Agent needs to be installed — it's the AI engine that does the work.
      </p>
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => openUrl("https://github.com/patrickbdevaney/hermes-max#install")}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">Install Hermes →</button>
        <button type="button" onClick={onRecheck} className="text-xs text-mist-300 hover:text-mist-100">Already installed? Check again</button>
      </div>
    </div>
  );
}
