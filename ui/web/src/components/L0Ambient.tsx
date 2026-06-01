// L0 — the glanceable layer for ONE turn. One plain-language line, a determinate
// progress bar against the PLAN.md contract (or honest activity when there's no
// plan), and the big live cost. No jargon, no tool names, no spans.
import { Bar, fmtUsd } from "./ui";
import { planProgress, activePlanIndex } from "../state";
import type { RunView, Turn } from "../state";

export function L0Ambient({ turn, view }: { turn: Turn; view: RunView }) {
  const pp = planProgress(turn);
  const activeIdx = activePlanIndex(turn);
  const line = narrationLine(turn, pp, activeIdx);
  const done = turn.status === "done";

  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-base font-medium leading-snug text-mist-100" aria-live="polite">
          {line}
        </h2>
        <div className="shrink-0 text-right">
          <div className="font-mono text-xl font-semibold tabular-nums text-mist-100">
            {fmtUsd(view.cost.total_usd)}
          </div>
          <div className="text-[11px] text-mist-400">{view.cost.paid_tok === 0 ? "all free so far" : "spent today"}</div>
        </div>
      </div>

      <div className="mt-4">
        {pp ? (
          <Bar done={done ? pp.total : pp.done} total={pp.total} />
        ) : (
          <Bar done={turn.startedSteps} total={0} indeterminate={!done} />
        )}
        <div className="mt-2 flex items-center justify-between text-xs text-mist-400">
          <span>
            {pp
              ? `step ${Math.min(pp.done + (done ? 0 : 1), pp.total)} of ${pp.total}`
              : done ? `${turn.completedSteps} steps complete`
                     : `step ${turn.startedSteps || 0}`}
          </span>
          <span>{done ? "finished" : phaseLabel(turn.phase)}</span>
        </div>
      </div>

      {turn.narrationLog.length > 1 && (
        <ul className="mt-4 space-y-1 border-t border-ink-800 pt-3">
          {turn.narrationLog.slice(-4).map((n, i, arr) => (
            <li
              key={`${n.ts}-${i}`}
              className={`flex items-center gap-2 text-xs ${
                n.level === "warn" ? "text-warn" : "text-mist-400"
              } ${i === arr.length - 1 ? "text-mist-200" : ""}`}
            >
              <span aria-hidden>{n.level === "warn" ? "⚠" : "·"}</span>
              <span className="truncate">{n.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function phaseLabel(phase: string): string {
  const map: Record<string, string> = {
    idle: "waiting", connected: "starting", plan: "planning", execute: "working",
    verify: "checking", research: "researching", done: "finished",
  };
  return map[phase] ?? phase;
}

function narrationLine(
  turn: Turn,
  pp: { done: number; total: number } | null,
  activeIdx: number,
): string {
  if (turn.status === "done") return turn.handback || "All done — your turn.";
  if (turn.plan && activeIdx >= 0 && pp) {
    return `${turn.plan[activeIdx].text} — step ${activeIdx + 1} of ${pp.total}`;
  }
  if (turn.narration) return turn.narration.text;
  if (turn.startedSteps === 0) return "Getting started…";
  return "Working…";
}
