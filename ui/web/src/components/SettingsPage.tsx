// Phase 6.1 — Settings. Density, forced reduced-motion, sound cues, and desktop
// notifications. All client-side and applied immediately.
import { useState } from "react";
import { getSettings, setSettings, getSound, setSound } from "../lib/settings";
import { playCue } from "../lib/toast";

export function SettingsPage() {
  const [s, setS] = useState(getSettings());
  const [sound, setSnd] = useState(getSound());
  const [notifyPerm, setNotifyPerm] = useState(typeof Notification !== "undefined" ? Notification.permission : "denied");

  const update = (patch: Partial<typeof s>) => setS(setSettings(patch));

  async function enableNotify(on: boolean) {
    update({ notify: on });
    if (on && typeof Notification !== "undefined" && Notification.permission === "default") {
      setNotifyPerm(await Notification.requestPermission());
    }
  }

  return (
    <div className="max-w-2xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Settings</h1>
        <p className="mt-1 text-sm text-mist-400">Tune the interface to taste — applied instantly, stored locally.</p>
      </header>

      <Group title="Display">
        <Row label="Density" hint="Compact scales the type ramp down a notch.">
          <Segmented value={s.density} options={[["comfortable", "comfortable"], ["compact", "compact"]]}
            onChange={(v) => update({ density: v as typeof s.density })} />
        </Row>
        <Toggle label="Reduced motion" hint="Freeze animations regardless of OS preference (state is always colour + icon + label)."
          checked={s.reduceMotion} onChange={(v) => update({ reduceMotion: v })} />
      </Group>

      <Group title="Alerts">
        <Toggle label="Sound cues" hint="A soft tone on verify-pass / conductor-fire / completion. Off by default."
          checked={sound} onChange={(v) => { setSound(v); setSnd(v); if (v) playCue("pass"); }} />
        <Toggle label="Desktop notifications"
          hint={notifyPerm === "denied" ? "Blocked by the browser — enable notifications for this site." : "Notify on run completion / conductor fires when backgrounded."}
          checked={s.notify} onChange={enableNotify} />
      </Group>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <h2 className="mb-3 text-sm font-medium text-mist-200">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div><div className="text-sm text-mist-100">{label}</div>{hint && <div className="text-[11px] text-mist-500">{hint}</div>}</div>
      {children}
    </div>
  );
}

function Toggle({ label, hint, checked, onChange }:
  { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <Row label={label} hint={hint}>
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${checked ? "bg-accent" : "bg-ink-700"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-mist-100 transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </button>
    </Row>
  );
}

function Segmented({ value, options, onChange }:
  { value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
      {options.map(([v, label]) => (
        <button key={v} type="button" onClick={() => onChange(v)}
          className={`rounded px-2.5 py-1 capitalize transition-colors ${value === v ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>{label}</button>
      ))}
    </div>
  );
}
