// Phase 6.3 — the toast viewport. Bottom-right stack, each toast colour + icon +
// label, actionable (click to jump). Mounted once at the app root.
import { useEffect, useState } from "react";
import { subscribeToasts, dismissToast, type Toast, type ToastTone } from "../lib/toast";

const ICON: Record<ToastTone, string> = { info: "·", good: "✓", warn: "⚠", bad: "✗", conductor: "⚡" };
const RING: Record<ToastTone, string> = {
  info: "border-ink-700", good: "border-good/40", warn: "border-warn/50",
  bad: "border-bad/50", conductor: "border-conductor/50",
};
const TONE: Record<ToastTone, string> = {
  info: "text-mist-200", good: "text-good", warn: "text-warn", bad: "text-bad", conductor: "text-conductor",
};

export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => subscribeToasts(setToasts), []);

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id}
          className={`pointer-events-auto animate-risein rounded-lg border bg-ink-overlay px-3 py-2.5 shadow-lg ${RING[t.tone]}`}>
          <div className="flex items-start gap-2">
            <span className={TONE[t.tone]} aria-hidden>{ICON[t.tone]}</span>
            <div className="min-w-0 flex-1">
              <div className={`text-xs font-medium ${TONE[t.tone]}`}>{t.title}</div>
              {t.detail && <div className="mt-0.5 truncate text-[11px] text-mist-400">{t.detail}</div>}
              {t.onAction && (
                <button type="button"
                  onClick={() => { t.onAction?.(); dismissToast(t.id); }}
                  className="mt-1 text-[11px] text-accent hover:underline">
                  {t.actionLabel ?? "View"} →
                </button>
              )}
            </div>
            <button type="button" onClick={() => dismissToast(t.id)}
              className="text-mist-500 hover:text-mist-100" aria-label="dismiss">✕</button>
          </div>
        </div>
      ))}
    </div>
  );
}
