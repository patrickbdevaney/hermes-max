// The six postures `hm mode` toggles, with the plain-language framing the mode
// switcher popover shows. These mirror config/modes.example.yaml; the server is the
// source of truth for which is active (status.mode) and applies changes via
// /api/config — this catalog is only the human-facing labels + ordering.

export interface ModeInfo {
  key: string;
  title: string;
  blurb: string;
  cost: "free" | "low" | "paid";
}

export const MODES: ModeInfo[] = [
  { key: "free", title: "Free", blurb: "Local executes · free cloud plans. $0 by default.", cost: "free" },
  { key: "full-local", title: "Full · local", blurb: "Local drives everything it can; free cloud fills gaps.", cost: "free" },
  { key: "full", title: "Full", blurb: "Best free + low-cost rungs across the chain.", cost: "low" },
  { key: "frontier-local", title: "Frontier · local", blurb: "Local drives; frontier model only on escalation.", cost: "paid" },
  { key: "frontier", title: "Frontier", blurb: "Frontier models lead. Highest quality, highest cost.", cost: "paid" },
  { key: "local", title: "Local only", blurb: "No cloud at all. Fully private, GPU/endpoint only.", cost: "free" },
];

export function modeInfo(key: string | undefined | null): ModeInfo | undefined {
  return MODES.find((m) => m.key === key);
}
