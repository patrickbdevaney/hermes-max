// First-run: detect-and-bless. ONE welcoming screen that branches on what was
// probed — not a wizard. Three states:
//   A  endpoint already reachable → "Your AI is ready" → open a project
//   B  nothing configured → connect a local endpoint OR a cloud provider key
//   C  hermes binary missing → install prompt + re-check
import { useState } from "react";
import { configureEndpoint, saveProviderKey, openUrl, type Provider } from "../lib/firstrun";
import { probeCapabilities, type DetectResult } from "../lib/detect";
import { ProviderGrid } from "../components/ProviderGrid";
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
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<{ model: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [provider, setProvider] = useState<Provider | null>(null);
  const [key, setKey] = useState("");

  async function testEndpoint() {
    setBusy(true); setErr(null); setOk(null);
    try {
      const r = await configureEndpoint(url.trim());
      if (r.ok) setOk({ model: r.model ?? null });
      else setErr(r.error ?? "Couldn't connect.");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function connectKey() {
    if (!provider) return;
    setBusy(true); setErr(null); setOk(null);
    try {
      const r = await saveProviderKey(provider.id, provider.env, key.trim());
      if (r.ok) setOk({ model: r.model ?? null });
      else setErr(r.error ?? "Couldn't connect.");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (ok) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-good"><StatusDot tone="good" /> Connected{ok.model ? <span className="font-mono text-mist-300">· {ok.model}</span> : null}</div>
        <button type="button" onClick={onConnected}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90">Open a project →</button>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-medium text-mist-100">Connect your AI to get started.</h1>

      <div className="space-y-2">
        <p className="text-sm text-mist-300">I have my own AI running <span className="text-mist-500">(local model, LM Studio, Ollama…)</span></p>
        <div className="flex gap-2">
          <input value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="http://localhost:11434/v1"
            className="flex-1 rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />
          <button type="button" onClick={testEndpoint} disabled={busy || !url.trim()}
            className="rounded-md border border-ink-700 px-3 py-2 text-xs text-mist-200 hover:bg-ink-850 disabled:opacity-40">
            {busy ? "Testing…" : "Test connection"}
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 text-[11px] text-mist-500">
        <span className="h-px flex-1 bg-ink-800" /> or use a cloud AI service <span className="h-px flex-1 bg-ink-800" />
      </div>

      <ProviderGrid selected={provider?.id} onSelect={(p) => { setProvider(p); setErr(null); }} />

      {provider && (
        <div className="space-y-2">
          <input type="password" value={key} onChange={(e) => setKey(e.target.value)}
            placeholder={`Paste your ${provider.name} API key`}
            className="w-full rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />
          <div className="flex items-center gap-3 text-[11px]">
            <button type="button" onClick={connectKey} disabled={busy || !key.trim()}
              className="rounded-md bg-accent px-3 py-1.5 font-medium text-ink-950 hover:opacity-90 disabled:opacity-40">
              {busy ? "Connecting…" : "Connect"}
            </button>
            <button type="button" onClick={() => openUrl(provider.keyUrl)} className="text-accent hover:underline">Where do I get a key? →</button>
            <button type="button" onClick={() => openUrl(provider.pricingUrl)} className="text-mist-400 hover:text-mist-200">
              {provider.free ? "Free tier available →" : "How much does it cost? →"}
            </button>
          </div>
        </div>
      )}

      {err && <p className="text-xs text-bad">{err}</p>}
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
