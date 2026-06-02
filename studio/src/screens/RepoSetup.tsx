// v2 1.6 — first-run repo resolution. On a clean machine HERMES_MAX_ROOT /
// walk-up / compile-time fallback all miss, so Studio can't find ui/server to
// sidecar. We ask once, validate (must contain ui/server), and persist — then
// the stack can start. Plain-language: "point Studio at where hermes-max lives".
import { useState } from "react";
import { setRepoRoot, pickDirectory } from "../lib/studioConfig";
import { StatusDot } from "../components/StatusDot";

export function RepoSetup({ onResolved }: { onResolved: () => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function pick() {
    const d = await pickDirectory();
    if (d) setPath(d);
  }
  async function go() {
    setBusy(true); setErr(null);
    try { await setRepoRoot(path.trim()); onResolved(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-bg-base px-6">
      <div className="w-full max-w-lg space-y-4">
        <div className="flex items-center gap-2">
          <StatusDot tone="accent" />
          <span className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Hermes Studio</span>
        </div>
        <h1 className="text-lg font-medium text-mist-100">Point Studio at where hermes-max lives.</h1>
        <p className="text-sm text-mist-400">
          Studio runs the hermes-max engine for you. Tell it which folder hermes-max is in
          (the one containing <span className="font-mono text-mist-300">ui/server</span>).
        </p>
        <div className="flex gap-2">
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/home/you/hermes-max"
            className="flex-1 rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />
          <button type="button" onClick={pick} className="rounded-md border border-ink-700 px-3 py-2 text-xs text-mist-200 hover:bg-ink-850">Browse…</button>
        </div>
        {err && <p className="text-xs text-bad">{err}</p>}
        <button type="button" onClick={go} disabled={busy || !path.trim()}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90 disabled:opacity-40">
          {busy ? "Checking…" : "Continue →"}
        </button>
      </div>
    </div>
  );
}
