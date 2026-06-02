// The connect-your-AI form — a local OpenAI-compatible endpoint OR a cloud
// provider key. Shared by first-run (State B) and Settings ("change AI"). On
// success it persists Rust-side (studio.conf + keychain), restarts the stack so
// the backend reloads, and calls onConnected.
import { useState } from "react";
import { configureEndpoint, saveProviderKey, openUrl, type Provider } from "../lib/firstrun";
import { ProviderGrid } from "./ProviderGrid";
import { StatusDot } from "./StatusDot";

export function ConnectAI({ onConnected }: { onConnected: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<{ model: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [provider, setProvider] = useState<Provider | null>(null);
  const [key, setKey] = useState("");

  async function testEndpoint(force = false) {
    setBusy(true); setErr(null);
    try {
      const r = await configureEndpoint(url.trim(), force);
      if (r.ok) { setOk({ model: r.model ?? null }); onConnected(); }
      else setErr(r.error ?? "Couldn't connect.");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }
  async function connectKey() {
    if (!provider) return;
    setBusy(true); setErr(null);
    try {
      const r = await saveProviderKey(provider.id, provider.env, key.trim());
      if (r.ok) { setOk({ model: r.model ?? null }); onConnected(); }
      else setErr(r.error ?? "Couldn't connect.");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (ok) {
    return (
      <div className="flex items-center gap-2 text-good">
        <StatusDot tone="good" /> Connected{ok.model ? <span className="font-mono text-mist-300">· {ok.model}</span> : null}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <p className="text-sm text-mist-300">I have my own AI running <span className="text-mist-500">(local model, LM Studio, Ollama…)</span></p>
        <div className="flex gap-2">
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:11434/v1"
            className="flex-1 rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />
          <button type="button" onClick={() => testEndpoint(false)} disabled={busy || !url.trim()}
            className="rounded-md border border-ink-700 px-3 py-2 text-xs text-mist-200 hover:bg-ink-850 disabled:opacity-40">
            {busy ? "Testing…" : "Test connection"}
          </button>
        </div>
        {err && url.trim() && (
          <button type="button" onClick={() => testEndpoint(true)} disabled={busy}
            className="text-[11px] text-accent hover:underline disabled:opacity-40">
            Use this endpoint anyway →
          </button>
        )}
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
