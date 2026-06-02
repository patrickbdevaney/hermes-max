// Phase 2 — the cost-shadow model. Re-price the run's REAL token volume at a
// frontier model's CURRENT published per-token rate, so the conductor↔executor
// asymmetry becomes a dollar figure. This is computed HONESTLY from actual
// tokens — never a borrowed headline percentage — and the comparison model and
// rate are labelled explicitly wherever the shadow is shown.
//
// The rate is a CONFIG VALUE (edit here, or override at call sites) so it stays
// current. Token counts in the live stream / ledger are aggregate (no in/out
// split), so the shadow uses a blended rate with a STATED output fraction; the
// assumption is surfaced in the UI label, not hidden.

export interface FrontierRate {
  model: string;            // the comparison model, shown to the user
  inputPerMtok: number;     // USD per million input tokens
  outputPerMtok: number;    // USD per million output tokens
  assumedOutputFraction: number;  // used when an in/out split isn't available
  asOf: string;             // the date the rate was current (keep fresh)
  source: string;
}

// Default comparison: Claude Opus list price. Edit to re-price; the UI reflects
// model + rate + asOf automatically.
export const FRONTIER: FrontierRate = {
  model: "Claude Opus 4.x",
  inputPerMtok: 15,
  outputPerMtok: 75,
  assumedOutputFraction: 0.35,
  asOf: "2026-06",
  source: "Anthropic published list price",
};

export function blendedPerMtok(r: FrontierRate = FRONTIER): number {
  return r.inputPerMtok * (1 - r.assumedOutputFraction) + r.outputPerMtok * r.assumedOutputFraction;
}

// A human label for the comparison, e.g.
// "Claude Opus 4.x @ $15/$75 per Mtok (in/out), ~35% output · 2026-06".
export function rateLabel(r: FrontierRate = FRONTIER): string {
  const pct = Math.round(r.assumedOutputFraction * 100);
  return `${r.model} @ $${r.inputPerMtok}/$${r.outputPerMtok} per Mtok (in/out), ~${pct}% output · ${r.asOf}`;
}

export interface ShadowResult {
  actualUsd: number;
  shadowUsd: number;        // what the same token volume would cost on frontier
  savedUsd: number;
  savedPct: number;         // 0..100
  multiple: number;         // shadow / actual (Infinity when actual is 0)
  tokens: number;
}

export function computeShadow(actualUsd: number, tokens: number, r: FrontierRate = FRONTIER): ShadowResult {
  const shadowUsd = (Math.max(0, tokens) / 1e6) * blendedPerMtok(r);
  const savedUsd = Math.max(0, shadowUsd - actualUsd);
  const savedPct = shadowUsd > 0 ? (savedUsd / shadowUsd) * 100 : 0;
  const multiple = actualUsd > 0 ? shadowUsd / actualUsd : Infinity;
  return { actualUsd, shadowUsd, savedUsd, savedPct, multiple, tokens };
}

export function fmtMoney(usd: number): string {
  if (usd <= 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

export function fmtMultiple(m: number): string {
  if (!isFinite(m)) return "∞×";
  if (m >= 100) return `${Math.round(m)}×`;
  if (m >= 10) return `${m.toFixed(0)}×`;
  return `${m.toFixed(1)}×`;
}
