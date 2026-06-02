// Settings (studio shell) — S0 placeholder; the full AI / notifications / display
// sections land in S4.
import type { DetectResult } from "../lib/detect";

export function Settings({ detect, onBack, onChanged }:
  { detect: DetectResult | null; onBack: () => void; onChanged: () => void }) {
  void detect; void onChanged;
  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <button type="button" onClick={onBack} className="mb-4 text-xs text-mist-400 hover:text-mist-100">← Projects</button>
      <h1 className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Settings</h1>
    </div>
  );
}
