// Two L2 panels shown during a run:
//  • Research fan-out — when the agent fans out across providers, show the breadth
//    (parallel source cards) converging into a synthesis node.
//  • Full trace — the entire OTLP span tree (the embedded Phoenix-style view),
//    collapsed by default so it never crowds the timeline.
import { useState } from "react";
import { Badge, Dot } from "./ui";
import { SpanTree } from "./SpanTree";
import { researchFanOut, rootSpans, spanCount } from "../state";
import type { RunView, Turn } from "../state";

export function ResearchFanOut({ turn }: { turn: Turn }) {
  const fan = researchFanOut(turn);
  if (!fan) return null;
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <h3 className="text-sm font-medium text-mist-200">Research fan-out</h3>
        <Badge tone="accent">{fan.sources.length} sources</Badge>
        {fan.synthesis && <Badge tone="good">converging</Badge>}
      </div>
      {/* breadth: parallel source cards */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {fan.sources.map((s) => {
          const tone = s.status === "ok" ? "good" : s.status === "fail" ? "bad"
            : s.status === "running" ? "accent" : "info";
          return (
            <div key={s.key} className="rounded-lg border border-ink-800 bg-ink-850 px-3 py-2">
              <div className="flex items-center gap-1.5">
                <Dot tone={tone as any} pulse={s.status === "running"} />
                <span className="truncate font-mono text-xs text-mist-100">{s.title}</span>
              </div>
              {s.server && <div className="mt-0.5 truncate text-[11px] text-mist-400">{s.server}</div>}
            </div>
          );
        })}
      </div>
      {/* convergence node */}
      <div className="mt-3 flex items-center justify-center">
        <div className="text-mist-400">↓</div>
      </div>
      <div className={`rounded-lg border px-3 py-2 text-center ${
        fan.synthesis ? "border-good/40 text-good" : "border-ink-800 text-mist-400"}`}>
        {fan.synthesis ? `synthesis · ${fan.synthesis.title}` : "awaiting synthesis…"}
      </div>
    </div>
  );
}

export function FullTrace({ view }: { view: RunView }) {
  const [open, setOpen] = useState(false);
  const n = spanCount(view);
  if (n === 0) return null;
  return (
    <section className="rounded-2xl border border-ink-800 bg-ink-900">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-5 py-3 text-left"
        aria-expanded={open}
      >
        <h3 className="text-sm font-medium text-mist-200">
          Full trace <span className="text-mist-400">({n} spans)</span>
        </h3>
        <span className="text-xs text-mist-400">{open ? "hide ▾" : "show ▸"}</span>
      </button>
      {open && (
        <div className="max-h-[50vh] overflow-y-auto border-t border-ink-800 px-3 py-3">
          <SpanTree view={view} spans={rootSpans(view)} />
        </div>
      )}
    </section>
  );
}
