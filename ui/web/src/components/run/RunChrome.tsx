// Fix C / Phase 1.1 — the persistent run chrome. A single always-visible HUD
// strip that answers "where is this run, right now?" at a glance, and seeds the
// cost thesis: step, turns, cumulative cost, live tok/s with a TREND sparkline,
// a cost-rate sparkline, the planner↔executor cost SPLIT, and a connection dot
// (green=live · amber=reconnecting · red=lost). Driven by ChromeMetrics (folded
// in lib/feed.ts) on the batched cadence — no extra polling.
import type { ChromeMetrics } from "../../lib/feed";
import type { ConnState } from "../../lib/events";
import { Dot } from "../ui";
import { Sparkline } from "./Sparkline";

function fmtCost(usd: number): string {
  if (usd <= 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

function Stat({ label, value, tone, spark, sparkStroke, sparkFill }:
  { label: string; value: string; tone?: string; spark?: number[]; sparkStroke?: string; sparkFill?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-mist-500">{label}</span>
      <div className="flex items-end gap-1.5">
        <span className={`font-mono text-sm tabular-nums ${tone ?? "text-mist-100"}`}>{value}</span>
        {spark && spark.length > 1 && (
          <Sparkline data={spark} stroke={sparkStroke ?? "var(--accent)"} fill={sparkFill} />
        )}
      </div>
    </div>
  );
}

const CONN: Record<ConnState, { tone: "good" | "warn" | "bad" | "accent"; label: string; pulse: boolean }> = {
  live:         { tone: "good",  label: "live",         pulse: false },
  connecting:   { tone: "warn",  label: "connecting",   pulse: true },
  reconnecting: { tone: "warn",  label: "reconnecting", pulse: true },
  lost:         { tone: "bad",   label: "disconnected", pulse: false },
};

export function RunChrome({ chrome, live, conn }:
  { chrome: ChromeMetrics; live: boolean; conn: ConnState }) {
  const stepLabel = chrome.total > 0 ? `${chrome.step}/${chrome.total}` : `${chrome.step}`;
  const pct = chrome.total > 0 ? Math.min(100, Math.round((chrome.step / chrome.total) * 100)) : 0;
  const running = chrome.running && live;
  const c = CONN[conn];

  // Planner = the rare cloud guidance calls; executor = the local worker.
  const plannerLabel = chrome.model
    ? `${chrome.model} · ${chrome.plannerTokens.toLocaleString()} tok · ${fmtCost(chrome.plannerCost)}`
    : "—";
  const execLabel = chrome.execPaidTok > 0
    ? `${chrome.execProvider ?? "local"} · ${chrome.execPaidTok.toLocaleString()} tok`
    : `${chrome.execProvider ?? "local"} · free`;

  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <div className="flex items-center gap-2" title={`stream ${c.label}`}>
          <Dot tone={running ? "accent" : c.tone} pulse={running || c.pulse} />
          <span className="text-xs font-medium text-mist-200">
            {running ? "executing" : !live ? "done" : chrome.running ? "idle" : "done"}
          </span>
          <span className="text-[10px] text-mist-500">· {c.label}</span>
        </div>
        <div className="h-7 w-px bg-ink-800" />
        <Stat label="step" value={stepLabel} tone="text-accent" />
        <Stat label="turns" value={String(chrome.turns)} />
        <Stat
          label="tok/s"
          value={chrome.tokps != null ? chrome.tokps.toFixed(0) : "—"}
          spark={chrome.tokpsHist}
          sparkStroke="var(--accent)"
          sparkFill="oklch(var(--accent-c) / 0.12)"
        />
        <Stat
          label="cost"
          value={fmtCost(chrome.cost_usd)}
          tone={chrome.cost_usd > 0 ? "text-warn" : "text-good"}
          spark={chrome.costHist}
          sparkStroke="var(--status-warning)"
        />

        {/* planner ↔ executor cost split — the seed of the cost thesis */}
        <div className="h-7 w-px bg-ink-800" />
        <div className="flex flex-col">
          <span className="text-[10px] uppercase tracking-wide text-conductor">planner</span>
          <span className="font-mono text-xs tabular-nums text-mist-200">{plannerLabel}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] uppercase tracking-wide" style={{ color: "var(--executor)" }}>executor</span>
          <span className="font-mono text-xs tabular-nums text-mist-300">{execLabel}</span>
        </div>

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
