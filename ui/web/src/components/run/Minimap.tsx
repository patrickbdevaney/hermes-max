// Phase 1.4 — the run minimap. A thin overview strip of the WHOLE run: each
// plan step is a block (coloured by status, width ∝ turns spent), each
// conductor fire is a pin above the step that triggered it. Click a block to
// jump the feed to that step. This is what makes a 300-turn run as scrubbable
// as a 5-turn one. Pure flexbox + tokens; state is colour + glyph (the pin) +
// title, never colour alone.
import type { FlowState, StepStatus } from "../../lib/feed";

const STATUS_BG: Record<StepStatus, string> = {
  pending: "bg-ink-700",
  active: "bg-accent",
  complete: "bg-good",
  failed: "bg-bad",
};

export function Minimap({ flow, onJump, activeStep }:
  { flow: FlowState; onJump: (step: number) => void; activeStep?: number }) {
  if (!flow.steps.length) return null;
  const fires = new Set(flow.conductors.map((c) => c.step));
  return (
    <div className="flex items-stretch gap-0.5 px-0.5 pt-1" aria-label="run minimap" role="navigation">
      {flow.steps.map((s) => {
        const grow = 1 + Math.min(s.turns, 8); // width ∝ work, capped
        const isActive = s.n === activeStep;
        return (
          <button
            key={s.n}
            type="button"
            onClick={() => onJump(s.n)}
            title={`Step ${s.n} · ${s.status}${s.turns ? ` · ${s.turns} turn${s.turns === 1 ? "" : "s"}` : ""}${fires.has(s.n) ? " · conductor fired" : ""}`}
            className="group relative flex flex-col justify-end"
            style={{ flexGrow: grow, flexBasis: 0 }}
          >
            {/* conductor pin */}
            {fires.has(s.n) && (
              <span className="absolute -top-1.5 left-1/2 -translate-x-1/2 text-[9px] leading-none text-conductor" aria-hidden>▾</span>
            )}
            <span
              className={`h-2 rounded-sm transition-all ${STATUS_BG[s.status]} ${isActive ? "ring-1 ring-accent ring-offset-0" : "opacity-80 group-hover:opacity-100"}`}
            />
          </button>
        );
      })}
    </div>
  );
}
