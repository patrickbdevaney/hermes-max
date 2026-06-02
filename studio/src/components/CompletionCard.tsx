// S5.1 — the completion receipt. A brief, dismissable celebration when a build
// finishes: what it cost, what it would have cost on a frontier model, and the
// savings. Same honest data as the web UI's shadow meter (real ledger tokens
// re-priced at the configured frontier rate), framed as delight not a metric.
import { useEffect } from "react";
import { computeShadow, rateLabel, fmtMoney, FRONTIER } from "../lib/shadow";
import type { WorkshopStatus } from "../lib/workshop";

export function CompletionCard({ name, status, onClose, onOpenFolder }:
  { name: string; status: WorkshopStatus; onClose: () => void; onOpenFolder: () => void }) {
  const r = computeShadow(status.cost_usd, status.tokens);

  // auto-dismiss after a few seconds (dismissable sooner)
  useEffect(() => {
    const t = setTimeout(onClose, 8000);
    return () => clearTimeout(t);
  }, [onClose]);

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-6">
      <div className="pointer-events-auto w-full max-w-lg animate-risein rounded-xl border border-conductor/40 bg-ink-overlay p-5 shadow-2xl">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <span className="text-conductor" aria-hidden>✓</span>
            <h3 className="text-sm font-semibold text-mist-100">{name} is ready</h3>
          </div>
          <button type="button" onClick={onClose} className="text-mist-500 hover:text-mist-100" aria-label="dismiss">✕</button>
        </div>

        <div className="mt-3 space-y-0.5 text-sm">
          <p className="text-mist-200">Built for <span className="font-mono text-good">{fmtMoney(r.actualUsd)}</span></p>
          {r.shadowUsd > 0 && (
            <p className="text-mist-400">
              The same build would cost ~<span className="font-mono text-mist-200">{fmtMoney(r.shadowUsd)}</span> on {FRONTIER.model}
            </p>
          )}
          {r.savedUsd > 0 && (
            <p className="text-conductor">You saved {r.savedPct.toFixed(0)}% 🎉</p>
          )}
        </div>

        <div className="mt-4 flex items-center gap-2">
          <button type="button" onClick={onOpenFolder}
            className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 hover:bg-ink-850">Open the project folder</button>
          <button type="button" onClick={onClose}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 hover:opacity-90">Build something else</button>
        </div>

        <p className="mt-3 text-[10px] text-mist-500">{status.tokens.toLocaleString()} tokens · priced against {rateLabel()}</p>
      </div>
    </div>
  );
}
