// Fix C — the persistent run chrome. A single always-visible HUD strip above the
// Feed/Flow tabs that answers "where is this run, right now?" at a glance: which step
// of how many, how many model turns, cumulative cost, and live tokens/sec. Driven by
// ChromeMetrics (folded in lib/feed.ts), so it updates with the same batched cadence
// as the feed — no extra polling, no per-event re-render storm.
import type { ChromeMetrics } from "../../lib/feed";
import { Dot } from "../ui";

function fmtCost(usd: number): string {
  if (usd <= 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-mist-500">{label}</span>
      <span className={`font-mono text-sm tabular-nums ${tone ?? "text-mist-100"}`}>{value}</span>
    </div>
  );
}

export function RunChrome({ chrome, live }: { chrome: ChromeMetrics; live: boolean }) {
  const stepLabel = chrome.total > 0 ? `${chrome.step}/${chrome.total}` : `${chrome.step}`;
  const pct = chrome.total > 0 ? Math.min(100, Math.round((chrome.step / chrome.total) * 100)) : 0;
  const running = chrome.running && live;

  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 px-4 py-2.5">
      <div className="flex items-center gap-5">
        <div className="flex items-center gap-2">
          <Dot tone={running ? "accent" : "good"} pulse={running} />
          <span className="text-xs font-medium text-mist-200">{running ? "executing" : chrome.running ? "idle" : "done"}</span>
        </div>
        <div className="h-7 w-px bg-ink-800" />
        <Stat label="step" value={stepLabel} tone="text-accent" />
        <Stat label="turns" value={String(chrome.turns)} />
        <Stat label="cost" value={fmtCost(chrome.cost_usd)} tone={chrome.cost_usd > 0 ? "text-warn" : "text-good"} />
        <Stat label="tok/s" value={chrome.tokps != null ? chrome.tokps.toFixed(0) : "—"} />
        {chrome.model && <Stat label="planner" value={chrome.model} tone="text-mist-300" />}

        {chrome.total > 0 && (
          <div className="ml-auto flex w-40 items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
            </div>
            <span className="font-mono text-[11px] text-mist-400">{pct}%</span>
          </div>
        )}
      </div>
    </div>
  );
}
