// Phase 6.1 — the Config surface. The operator owns this. A raw view of the live
// config plus the two writable knobs (mode, vLLM base URL) with a
// validate-before-apply gate (the server returns applied[] / warnings[]). A full
// schema-aware form / Monaco editor is intentionally deferred — the raw view +
// guarded apply is the high-leverage 80%.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { CodeBlock } from "./run/CodeBlock";
import { SkeletonRows, Badge } from "./ui";
import { MODES } from "../lib/modes";
import type { ConfigResult } from "../types";

export function ConfigPage({ refreshStatus }: { refreshStatus: () => void }) {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [vllm, setVllm] = useState("");
  const [result, setResult] = useState<ConfigResult | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.config().then((c) => {
    setCfg(c);
    setVllm(String((c as any).vllm_base_url ?? (c as any).vllm?.base_url ?? ""));
  }).catch(() => setCfg({}));
  useEffect(() => { load(); }, []);

  const mode = String((cfg as any)?.mode ?? "");

  async function apply(body: { mode?: string; vllm_base_url?: string }) {
    setBusy(true); setResult(null);
    try {
      const r = await api.applyConfig(body);
      setResult(r);
      if (r.ok) { await load(); refreshStatus(); }
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Config</h1>
        <p className="mt-1 text-sm text-mist-400">The plumbing surface — change posture and endpoints, validated before apply.</p>
      </header>

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <h2 className="mb-3 text-sm font-medium text-mist-200">Mode</h2>
        <div className="flex flex-wrap gap-2">
          {MODES.map((m) => (
            <button key={m.key} type="button" disabled={busy} onClick={() => apply({ mode: m.key })}
              className={`rounded-md border px-2.5 py-1.5 text-xs transition-colors ${mode === m.key ? "border-accent text-accent bg-accent-soft/15" : "border-ink-700 text-mist-300 hover:bg-ink-850"}`}
              title={m.blurb}>
              {m.title}
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <h2 className="mb-3 text-sm font-medium text-mist-200">Local executor (vLLM base URL)</h2>
        <div className="flex gap-2">
          <input value={vllm} onChange={(e) => setVllm(e.target.value)} placeholder="http://127.0.0.1:8000/v1"
            className="flex-1 rounded-md border border-ink-700 bg-ink-input px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent" />
          <button type="button" disabled={busy} onClick={() => apply({ vllm_base_url: vllm })}
            className="rounded-md bg-accent px-3 py-2 text-xs font-medium text-ink-950 disabled:opacity-40 hover:opacity-90">Apply</button>
        </div>
      </section>

      {result && (
        <div className={`rounded-lg border px-3 py-2 text-xs ${result.ok ? "border-good/40 text-good" : "border-bad/40 text-bad"}`}>
          {result.ok ? "Applied." : (result.error || "Apply failed.")}
          {result.applied?.length ? <span className="text-mist-300"> · {result.applied.join(", ")}</span> : null}
          {result.warnings?.map((w, i) => <div key={i} className="text-warn">⚠ {w}</div>)}
        </div>
      )}

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-mist-200">Live config</h2>
          <Badge tone="muted">read-only</Badge>
        </div>
        {cfg ? <CodeBlock text={JSON.stringify(cfg, null, 2)} lang="json" /> : <SkeletonRows rows={5} />}
      </section>
    </div>
  );
}
