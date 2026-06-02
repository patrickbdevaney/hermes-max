// Phase 6 — user settings (theme/density/reduced-motion/sound/notifications).
// Persisted in localStorage; document-level settings (density, forced reduced
// motion) are applied to <html> on load so they take effect before first paint.
// `sound` lives under its own key (hmx.sound) so lib/toast can read it without a
// dependency on this module.
export interface Settings {
  density: "comfortable" | "compact";
  reduceMotion: boolean;
  notify: boolean;
}

const KEY = "hmx.settings";
const DEFAULTS: Settings = { density: "comfortable", reduceMotion: false, notify: false };

export function getSettings(): Settings {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; }
  catch { return { ...DEFAULTS }; }
}

export function setSettings(patch: Partial<Settings>): Settings {
  const next = { ...getSettings(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* quota */ }
  applySettings();
  return next;
}

export function getSound(): boolean { try { return localStorage.getItem("hmx.sound") === "1"; } catch { return false; } }
export function setSound(on: boolean) { try { localStorage.setItem("hmx.sound", on ? "1" : "0"); } catch { /**/ } }

export function applySettings() {
  if (typeof document === "undefined") return;
  const s = getSettings();
  document.documentElement.dataset.density = s.density;
  document.documentElement.dataset.reduceMotion = s.reduceMotion ? "1" : "0";
}
