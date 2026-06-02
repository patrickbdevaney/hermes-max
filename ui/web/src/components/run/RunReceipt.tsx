// Phase 2.2 — the per-run cost receipt. A summary card shown when the run
// settles into review: the headline savings multiple, tokens split planner↔
// executor, dollars, conductor intervention count. Built from RUN-SCOPED chrome
// metrics (the ledger report API isn't run-scoped, so we show what is honestly
// attributable to this run). Designed to be screenshotted / shared.
import type { ChromeMetrics } from "../../lib/feed";
import { computeShadow, rateLabel, fmtMoney, fmtMultiple, FRONTIER } from "../../lib/shadow";
import { fmtInt } from "../ui";

export function RunReceipt({ chrome, conductorFires, runId }:
  { chrome: ChromeMetrics; conductorFires: number; runId?: string }) {
  const tokens = (chrome.execFreeTok + chrome.execPaidTok) || chrome.plannerTokens;
  const r = computeShadow(chrome.cost_usd, tokens);

  return (
    <div className="rounded-lg border border-conductor/30 bg-ink-900 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-conductor" aria-hidden>◆</span>
          <h3 className="text-sm font-semibold text-mist-100">Run receipt</h3>
        </div>
        {runId && <span className="font-mono text-[10px] text-mist-500">{runId}</span>}
      </div>

      {/* headline: the savings multiple */}
      <div className="mt-3 flex items-end gap-3">
        <span className="font-mono text-3xl font-semibold tabular-nums text-conductor">{fmtMultiple(r.multiple)}</span>
        <div className="pb-1">
          <div className="text-sm text-mist-200">cheaper than {FRONTIER.model}</div>
          <div className="text-xs text-mist-400">
            saved <span className="font-mono text-conductor">{fmtMoney(r.savedUsd)}</span> ({r.savedPct.toFixed(0)}%)
          </div>
        </div>
      </div>

      {/* the split */}
      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <Cell label="actual cost" value={fmtMoney(r.actualUsd)} tone="text-good" />
        <Cell label={`on ${FRONTIER.model}`} value={fmtMoney(r.shadowUsd)} tone="text-mist-200" />
        <Cell label="turns" value={fmtInt(chrome.turns)} />
        <Cell label="conductor fires" value={fmtInt(conductorFires)} tone="text-conductor" />
      </dl>

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Lane
          label="planner"
          tone="text-conductor"
          model={chrome.model ?? "—"}
          tok={chrome.plannerTokens}
          cost={fmtMoney(chrome.plannerCost)}
        />
        <Lane
          label="executor"
          tone="text-mist-300"
          model={chrome.execProvider ?? "local"}
          tok={chrome.execFreeTok + chrome.execPaidTok}
          cost={chrome.execPaidTok > 0 ? `${fmtInt(chrome.execPaidTok)} paid tok` : "free"}
        />
      </div>

      <p className="mt-3 text-[10px] text-mist-500">
        {fmtInt(tokens)} tokens re-priced against {rateLabel()}
      </p>
    </div>
  );
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md border border-ink-800 bg-ink-850 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-mist-500">{label}</div>
      <div className={`mt-0.5 font-mono text-sm tabular-nums ${tone ?? "text-mist-100"}`}>{value}</div>
    </div>
  );
}

function Lane({ label, tone, model, tok, cost }:
  { label: string; tone: string; model: string; tok: number; cost: string }) {
  return (
    <div className="rounded-md border border-ink-800 bg-ink-850 px-3 py-2">
      <div className={`text-[10px] uppercase tracking-wide ${tone}`}>{label}</div>
      <div className="mt-0.5 truncate font-mono text-xs text-mist-200">{model}</div>
      <div className="mt-0.5 font-mono text-[11px] tabular-nums text-mist-400">
        {fmtInt(tok)} tok · {cost}
      </div>
    </div>
  );
}
