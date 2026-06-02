// Settings (the studio shell — distinct from the web UI's own settings). Three
// sections: Your AI (change source + test), Notifications (per-event toggles),
// Display (reduced motion). Persisted to studio.conf via save_studio_settings.
import { useEffect, useState } from "react";
import { probeCapabilities, type DetectResult } from "../lib/detect";
import { loadStudioConfig, saveStudioSettings } from "../lib/studioConfig";
import { ConnectAI } from "../components/ConnectAI";

type Prefs = Record<string, boolean>;
const DEFAULTS: Prefs = {
  notifications: true, notify_complete: true, notify_attention: true, notify_conductor: true, reduce_motion: false,
};

function host(url?: string | null): string {
  if (!url) return "";
  try { return new URL(url).host; } catch { return url; }
}

export function Settings({ detect, onBack, onChanged }:
  { detect: DetectResult | null; onBack: () => void; onChanged: () => void }) {
  const [prefs, setPrefs] = useState<Prefs>(DEFAULTS);
  const [d, setD] = useState<DetectResult | null>(detect);
  const [changing, setChanging] = useState(false);

  useEffect(() => {
    loadStudioConfig().then((c) => setPrefs({ ...DEFAULTS, ...(c.settings as Prefs) })).catch(() => void 0);
  }, []);
  useEffect(() => { applyDisplay(prefs.reduce_motion); }, [prefs.reduce_motion]);

  function set(key: string, val: boolean) {
    const next = { ...prefs, [key]: val };
    setPrefs(next);
    saveStudioSettings(next).catch(() => void 0);
  }
  function applyDisplay(reduce: boolean) {
    if (typeof document !== "undefined") document.documentElement.dataset.reduceMotion = reduce ? "1" : "0";
  }
  function retest() {
    probeCapabilities().then((r) => { setD(r); onChanged(); }).catch(() => void 0);
  }

  const aiSummary = d?.endpoint_url
    ? `Using your own model at ${host(d.endpoint_url)}${d.endpoint_model ? ` · ${d.endpoint_model}` : ""}`
    : d?.keys_configured.length
      ? `Using ${d.keys_configured[0]} — pay-as-you-go per project`
      : "No AI connected yet";

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <button type="button" onClick={onBack} className="mb-4 text-xs text-mist-400 hover:text-mist-100">← Projects</button>
      <h1 className="mb-6 font-display text-2xl font-semibold tracking-tight2 text-mist-100">Settings</h1>

      <Section title="Your AI">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-mist-300">{aiSummary}</p>
          <div className="flex shrink-0 gap-2">
            <button type="button" onClick={retest} className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 hover:bg-ink-850">Test connection</button>
            <button type="button" onClick={() => setChanging((c) => !c)} className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 hover:bg-ink-850">{changing ? "Cancel" : "Change"}</button>
          </div>
        </div>
        {changing && <div className="mt-4"><ConnectAI onConnected={() => { setChanging(false); retest(); }} /></div>}
      </Section>

      <Section title="Notifications">
        <Toggle label="Notifications" hint="Master switch for desktop alerts." checked={prefs.notifications} onChange={(v) => set("notifications", v)} />
        <Toggle label="Build complete" checked={prefs.notify_complete} onChange={(v) => set("notify_complete", v)} />
        <Toggle label="Needs attention" hint="Tests failing repeatedly." checked={prefs.notify_attention} onChange={(v) => set("notify_attention", v)} />
        <Toggle label="Planner stepped in" hint="When the window isn't focused." checked={prefs.notify_conductor} onChange={(v) => set("notify_conductor", v)} />
      </Section>

      <Section title="Display">
        <Toggle label="Reduced motion" hint="Freeze animations (status is always colour + icon + label)." checked={prefs.reduce_motion} onChange={(v) => set("reduce_motion", v)} />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-4 rounded-lg border border-ink-800 bg-ink-900 p-4">
      <h2 className="mb-3 text-sm font-medium text-mist-200">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Toggle({ label, hint, checked, onChange }:
  { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div><div className="text-sm text-mist-100">{label}</div>{hint && <div className="text-[11px] text-mist-500">{hint}</div>}</div>
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${checked ? "bg-accent" : "bg-ink-700"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-mist-100 transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </button>
    </div>
  );
}
