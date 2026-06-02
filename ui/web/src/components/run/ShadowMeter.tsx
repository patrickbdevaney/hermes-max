// Phase 2.1 — the live Cost Shadow Meter. The single most shareable artifact in
// the UI: as the local executor runs essentially free, a running comparison
// climbs — "Actual $0.04 · On Claude Opus $3.20 · Saved $3.16 (99%)". The
// shadow is computed from the run's REAL token volume re-priced at a
// configurable, explicitly-labelled frontier rate (lib/shadow.ts) — never a
// borrowed percentage. The counters tick up (reduced-motion-safe) so the
// asymmetry reads viscerally in motion.
import type { ChromeMetrics } from "../../lib/feed";
import { computeShadow, rateLabel, fmtMoney, fmtMultiple, FRONTIER } from "../../lib/shadow";
import { useCountUp } from "../../lib/useCountUp";

// Authoritative token volume: the ledger's free+paid totals (every call), with
// a fallback to planner tokens if cost events haven't arrived yet.
function totalTokens(c: ChromeMetrics): number {
  const ledger = c.execFreeTok + c.execPaidTok;
  return ledger > 0 ? ledger : c.plannerTokens;
}

export function ShadowMeter({ chrome, compact }: { chrome: ChromeMetrics; compact?: boolean }) {
  const tokens = totalTokens(chrome);
  const r = computeShadow(chrome.cost_usd, tokens);
  const actual = useCountUp(r.actualUsd);
  const shadow = useCountUp(r.shadowUsd);
  const saved = useCountUp(r.savedUsd);

  if (tokens <= 0) return null; // nothing to compare yet — stay honest, show nothing

  return (
    <div className={`rounded-lg border border-conductor/30 bg-conductor/5 ${compact ? "px-3 py-2" : "px-4 py-3"}`}>
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        <Metric label="actual" value={fmtMoney(actual)} tone="text-good" />
        <span className="text-mist-600">·</span>
        <Metric label={`on ${FRONTIER.model}`} value={fmtMoney(shadow)} tone="text-mist-200" />
        <span className="text-mist-600">·</span>
        <Metric
          label="saved"
          value={`${fmtMoney(saved)} (${r.savedPct.toFixed(0)}%)`}
          tone="text-conductor"
          big={!compact}
        />
        <span className="ml-auto font-mono text-sm tabular-nums text-conductor" title="cost multiple vs frontier">
          {fmtMultiple(r.multiple)} cheaper
        </span>
      </div>
      {!compact && (
        <p className="mt-1.5 text-[10px] text-mist-500">
          {tokens.toLocaleString()} tokens re-priced against {rateLabel()}
        </p>
      )}
    </div>
  );
}

function Metric({ label, value, tone, big }: { label: string; value: string; tone: string; big?: boolean }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[10px] uppercase tracking-wide text-mist-500">{label}</span>
      <span className={`font-mono tabular-nums ${tone} ${big ? "text-base font-semibold" : "text-sm"}`}>{value}</span>
    </span>
  );
}
