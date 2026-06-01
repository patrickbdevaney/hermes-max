// Shared provider-key UI: a single KeyRow (paste → save to the secret store → test
// live) and the ordered list. Used by BOTH the onboarding wizard and the Providers
// page so there's one source of truth for the secret discipline: the key rides in
// component state only until POSTed, is cleared immediately after, is never echoed
// back (the API returns presence booleans), never localStorage, never logged.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Dot, Glyph } from "../ui";
import type { KeysStatus, ProviderKeyStatus, TestResult } from "../../types";

const TIER_ORDER = { free: 0, paid: 1, frontier: 2, local: 3 } as Record<string, number>;

export function ProviderKeyList({ onChange }: { onChange?: () => void }) {
  const [ks, setKs] = useState<KeysStatus | null>(null);
  const reload = () => api.keysStatus().then(setKs).catch(() => void 0);
  useEffect(() => { reload(); }, []);

  if (!ks) return <div className="text-sm text-mist-400">loading…</div>;

  const keyed = ks.providers.filter((p) => !p.keyless)
    .sort((a, b) => (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        <Badge tone={ks.is_keychain ? "good" : "warn"}>
          <Glyph name="checkpoint" /> stored in: {ks.backend_label}
        </Badge>
      </div>
      <div className="space-y-2">
        {keyed.map((p) => (
          <KeyRow key={p.name} p={p} onSaved={() => { reload(); onChange?.(); }} />
        ))}
      </div>
      <p className="text-xs text-mist-400">
        Keys are sent once to the local backend and written to the store above. They are never
        returned to this page, never stored in the browser, and never logged.
      </p>
    </div>
  );
}

export function KeyRow({ p, onSaved }: { p: ProviderKeyStatus; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<TestResult | "testing" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setSaving(true); setErr(null);
    try {
      const r = await api.storeKey(p.name, value);
      if (!r.ok) setErr(r.error || "failed");
      setValue("");           // clear the secret from component state immediately
      onSaved();
    } catch (e) { setErr((e as Error).message); }
    finally { setSaving(false); }
  }
  async function runTest() {
    setTest("testing");
    try { setTest(await api.testConnection(p.name)); }
    catch (e) { setTest({ ok: false, error: (e as Error).message }); }
  }

  const tierTone = p.tier === "free" ? "good" : p.tier === "frontier" ? "warn" : "info";
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-850 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-mist-100">{p.name}</span>
        <Badge tone={tierTone as any}>{p.tier}</Badge>
        {p.present
          ? <Badge tone="good"><Dot tone="good" />configured</Badge>
          : <Badge tone="muted"><Dot tone="muted" />not set</Badge>}
        <span className="ml-auto font-mono text-[11px] text-mist-400">{p.api_key_env}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          type="password"
          value={value}
          autoComplete="off"
          onChange={(e) => setValue(e.target.value)}
          placeholder={p.present ? "replace key…" : "paste API key…"}
          className="min-w-[200px] flex-1 rounded-md border border-ink-700 bg-ink-950 px-3 py-1.5 font-mono text-sm text-mist-100 outline-none focus:border-accent"
        />
        <button
          type="button"
          disabled={saving || !value.trim()}
          onClick={save}
          className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {saving ? "saving…" : "Save"}
        </button>
        <button
          type="button"
          disabled={!p.present || test === "testing"}
          onClick={runTest}
          className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 transition-colors hover:bg-ink-800 disabled:opacity-40"
        >
          {test === "testing" ? "testing…" : "Test"}
        </button>
      </div>
      {err && <p className="mt-2 text-xs text-bad">{err}</p>}
      {typeof test === "object" && test && (
        <p className={`mt-2 flex items-center gap-2 text-xs ${test.ok ? "text-good" : "text-bad"}`}>
          <Glyph name={test.ok ? "ok" : "fail"} />
          {test.ok
            ? `connected · ${test.latency_ms}ms · ${test.model ?? "model ok"}`
            : `failed · ${test.error ?? "no connection"}`}
        </p>
      )}
    </div>
  );
}
