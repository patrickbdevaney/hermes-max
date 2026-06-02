// Phase 7.1 + 7.2 — the memory surface no other agent tool ships.
//
// 7.2 Memory-anchor overlay: the execution contract is re-injected before every
// LLM call (pre_llm_call). We render that re-injection as a persistent ANCHOR
// with a thin line connecting every turn back to it — visually explaining WHY
// the agent doesn't drift across a long run. An invisible reliability mechanism
// made a visible feature.
//
// 7.1 Compaction "what survived": when context is compacted, the anchor is what
// survives. We surface each compaction event and highlight the re-injected
// contract that carried through.
import type { FlowState } from "../../lib/feed";
import { EmptyMoment } from "../ui";

const MAX_DOTS = 40;

export function MemoryView({ flow, turns }: { flow: FlowState; turns: number }) {
  const { anchors, compactions, lastContract } = flow.memory;

  if (anchors === 0 && compactions === 0) {
    return (
      <EmptyMoment
        icon="⚓"
        title="No anchor re-injections observed yet"
        hint="The execution contract is re-injected before each LLM call; once the run makes calls, the anchor and its survival across compaction appear here."
      />
    );
  }

  const dots = Math.min(Math.max(turns, anchors), MAX_DOTS);
  const H = 24 + dots * 16 + 16;
  const ANCHOR_X = 40, ANCHOR_Y = 24, COL_X = 220;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto">
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="anchor re-injections" value={anchors.toLocaleString()} tone="text-conductor" />
        <Stat label="turns covered" value={turns.toLocaleString()} />
        <Stat label="compactions survived" value={compactions.toLocaleString()} tone={compactions > 0 ? "text-good" : "text-mist-300"} />
      </div>

      <div className="rounded-lg border border-conductor/30 bg-conductor/5 p-3">
        <div className="flex items-center gap-2">
          <span className="text-conductor" aria-hidden>⚓</span>
          <span className="text-sm font-medium text-mist-100">What the agent is holding onto</span>
        </div>
        <p className="mt-1 text-xs text-mist-400">
          The agent re-reads this every turn — so even after a long run it keeps the plan in mind and doesn't drift.
        </p>
        {lastContract && (
          <pre className="mt-2 max-h-40 overflow-auto rounded-md border border-ink-800 bg-ink-input p-2 font-mono text-[11px] leading-relaxed text-mist-300 whitespace-pre-wrap">
            {lastContract}
          </pre>
        )}
      </div>

      {/* the overlay: anchor ─── connected to every turn */}
      <div className="rounded-lg border border-ink-800 bg-ink-950/40 p-2">
        <svg width="100%" height={H} viewBox={`0 0 320 ${H}`} preserveAspectRatio="xMinYMin meet" className="block">
          {/* connecting lines from the anchor to each turn marker */}
          {Array.from({ length: dots }).map((_, i) => {
            const y = ANCHOR_Y + 16 + i * 16;
            return (
              <path key={i} d={`M${ANCHOR_X + 6},${ANCHOR_Y + 8} C${COL_X - 60},${ANCHOR_Y + 8} ${COL_X - 40},${y} ${COL_X},${y}`}
                fill="none" stroke="var(--conductor)" strokeWidth={0.75} opacity={0.25} />
            );
          })}
          {/* the persistent anchor node */}
          <circle cx={ANCHOR_X} cy={ANCHOR_Y + 8} r={9} fill="oklch(var(--conductor-c) / 0.2)" stroke="var(--conductor)" />
          <text x={ANCHOR_X} y={ANCHOR_Y + 12} textAnchor="middle" fontSize={11} fill="var(--conductor)">⚓</text>
          {/* turn markers */}
          {Array.from({ length: dots }).map((_, i) => {
            const y = ANCHOR_Y + 16 + i * 16;
            return (
              <g key={`t${i}`}>
                <circle cx={COL_X} cy={y} r={3} fill="var(--executor)" />
                <text x={COL_X + 10} y={y + 3} fontSize={9} className="fill-mist-500">turn {i + 1}</text>
              </g>
            );
          })}
        </svg>
        {turns > MAX_DOTS && (
          <p className="px-1 pt-1 text-[10px] text-mist-500">showing first {MAX_DOTS} of {turns} turns — each re-anchored to the same contract</p>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-mist-500">{label}</div>
      <div className={`mt-0.5 font-mono text-lg tabular-nums ${tone ?? "text-mist-100"}`}>{value}</div>
    </div>
  );
}
