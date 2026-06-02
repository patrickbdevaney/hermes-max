// Fix B / Phase 1.4 — the graphical Flow view (n8n / ComfyUI lineage) with
// SEMANTIC ZOOM. The run's topology is a vertical chain of PLAN STEP nodes
// (pending → active → complete/failed), a sticky PLAN head, and CONDUCTOR nodes
// branching right of whichever step triggered them, joined by a gold dashed
// edge. The edge entering the active step "marches" so the eye lands on where
// work is happening now.
//
// Semantic zoom: one canvas, three levels of detail. Zoomed out shows STEPS
// only (a 300-turn run fits on screen); default adds turn counts; zoomed in
// adds conductor detail. Pure SVG + foreignObject — node count hard-capped
// (MAX_GRAPH_NODES) upstream, vector scales crisply. State is colour + glyph +
// label, so reduced-motion loses nothing.
import { useState } from "react";
import type { FlowState, FlowStep, StepStatus } from "../../lib/feed";
import { conductorsForStep } from "../../lib/feed";

const NODE_W = 188;
const NODE_H = 46;
const V_GAP = 34;
const TOP = 70;            // room for the sticky plan node
const STEP_X = 28;         // left of the step column
const COND_X = STEP_X + NODE_W + 90;   // conductor column
const COND_W = 184;

type Detail = "steps" | "turns" | "full";

const STEP_TONE: Record<StepStatus, { border: string; cvar: string; glyph: string; label: string }> = {
  pending:  { border: "border-ink-700",  cvar: "--ink-700-c",        glyph: "○", label: "text-mist-400" },
  active:   { border: "border-accent",   cvar: "--accent-c",         glyph: "◐", label: "text-mist-100" },
  complete: { border: "border-good",     cvar: "--status-success-c", glyph: "✓", label: "text-mist-200" },
  failed:   { border: "border-bad",      cvar: "--status-error-c",   glyph: "✗", label: "text-mist-100" },
};

function stepY(i: number): number { return TOP + i * (NODE_H + V_GAP); }

export function FlowGraph({ flow, live }: { flow: FlowState; live: boolean }) {
  const [zoom, setZoom] = useState(1);
  const detail: Detail = zoom <= 0.72 ? "steps" : zoom >= 1.3 ? "full" : "turns";

  const steps = flow.steps.length
    ? flow.steps
    : [{ n: 1, status: "active" as StepStatus, turns: 0 }];
  const height = stepY(steps.length) + 20;
  const showCond = detail !== "steps" && flow.conductors.length > 0;
  const width = showCond ? COND_X + COND_W + 24 : STEP_X + NODE_W + 24;
  const h = Math.max(height, 240);

  return (
    <div className="relative h-full overflow-hidden rounded-lg border border-ink-800 bg-ink-950/40">
      {/* zoom toolbar — semantic level shown by label, not by guesswork */}
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-md border border-ink-700 bg-ink-850/90 px-1.5 py-1 text-xs backdrop-blur">
        <button type="button" aria-label="zoom out" className="px-1 text-mist-300 hover:text-mist-100"
          onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.2).toFixed(2)))}>−</button>
        <span className="w-12 text-center text-[10px] uppercase tracking-wide text-mist-500">{detail}</span>
        <button type="button" aria-label="zoom in" className="px-1 text-mist-300 hover:text-mist-100"
          onClick={() => setZoom((z) => Math.min(1.8, +(z + 0.2).toFixed(2)))}>+</button>
      </div>

      <div className="h-full overflow-auto">
        <svg viewBox={`0 0 ${width} ${h}`} width={width * zoom} height={h * zoom} className="block">
          <PlanNode done={flow.done} />
          <Edge x1={STEP_X + NODE_W / 2} y1={48} x2={STEP_X + NODE_W / 2} y2={stepY(0)} />

          {/* step chain edges */}
          {steps.slice(0, -1).map((st, i) => {
            const nextActive = steps[i + 1].status === "active";
            return (
              <Edge
                key={`e${st.n}`}
                x1={STEP_X + NODE_W / 2} y1={stepY(i) + NODE_H}
                x2={STEP_X + NODE_W / 2} y2={stepY(i + 1)}
                active={nextActive && live}
              />
            );
          })}

          {/* conductor branch edges */}
          {showCond && steps.map((st, i) =>
            conductorsForStep(flow, st.n).map((cnd, k) => (
              <Edge
                key={`ce${cnd.id}`}
                x1={STEP_X + NODE_W} y1={stepY(i) + NODE_H / 2}
                x2={COND_X} y2={stepY(i) + NODE_H / 2 + k * (NODE_H + 10)}
                conductor
                active={!cnd.resolved && live}
              />
            )),
          )}

          {/* step nodes */}
          {steps.map((st, i) => <StepNode key={st.n} step={st} y={stepY(i)} live={live} detail={detail} />)}

          {/* conductor nodes */}
          {showCond && steps.map((st, i) =>
            conductorsForStep(flow, st.n).map((cnd, k) => (
              <foreignObject
                key={cnd.id}
                x={COND_X} y={stepY(i) + NODE_H / 2 + k * (NODE_H + 10) - NODE_H / 2}
                width={COND_W} height={NODE_H}
              >
                <div className={`flex h-full flex-col justify-center rounded-md border px-2.5 ${
                  cnd.resolved ? "border-conductor/50 bg-conductor/10" : "border-conductor/60 bg-conductor/5"}`}>
                  <div className="flex items-center gap-1.5">
                    <span className="text-conductor">{cnd.resolved ? "✦" : "⚡"}</span>
                    <span className="truncate text-[11px] font-medium text-mist-100">
                      conductor{cnd.tier ? ` · ${cnd.tier}` : ""}
                    </span>
                  </div>
                  {detail === "full" && (
                    <span className="truncate text-[10px] text-mist-400">
                      {cnd.resolved ? (cnd.model || "guidance ready") : cnd.reason}
                    </span>
                  )}
                </div>
              </foreignObject>
            )),
          )}
        </svg>
      </div>
    </div>
  );
}

function PlanNode({ done }: { done: boolean }) {
  return (
    <foreignObject x={STEP_X} y={10} width={NODE_W} height={38}>
      <div className="flex h-full items-center gap-2 rounded-md border border-accent/40 bg-accent-soft/15 px-3">
        <span className="text-accent">◆</span>
        <span className="text-xs font-semibold text-mist-100">PLAN</span>
        {done && <span className="ml-auto text-[10px] text-good">complete</span>}
      </div>
    </foreignObject>
  );
}

function StepNode({ step, y, live, detail }: { step: FlowStep; y: number; live: boolean; detail: Detail }) {
  const t = STEP_TONE[step.status];
  const pulsing = step.status === "active" && live;
  return (
    <foreignObject x={STEP_X} y={y} width={NODE_W} height={NODE_H}>
      <div
        className={`flex h-full items-center gap-2 rounded-md border bg-ink-900 px-3 ${t.border}`}
        style={pulsing ? { boxShadow: `0 0 0 3px oklch(var(${t.cvar}) / 0.14)` } : undefined}
      >
        <span className={`text-sm ${t.label}`} style={{ color: `oklch(var(${t.cvar}))` }}>{t.glyph}</span>
        <div className="min-w-0">
          <div className={`text-xs font-medium ${t.label}`}>Step {step.n}</div>
          {detail !== "steps" && (
            <div className="truncate text-[10px] text-mist-400">
              {step.status}{step.turns ? ` · ${step.turns} turn${step.turns === 1 ? "" : "s"}` : ""}
            </div>
          )}
        </div>
      </div>
    </foreignObject>
  );
}

function Edge({ x1, y1, x2, y2, active, conductor }:
  { x1: number; y1: number; x2: number; y2: number; active?: boolean; conductor?: boolean }) {
  const midY = conductor ? y1 : (y1 + y2) / 2;
  const d = conductor
    ? `M${x1},${y1} C${x1 + 45},${y1} ${x2 - 45},${y2} ${x2},${y2}`
    : `M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`;
  const color = conductor ? "var(--conductor)" : active ? "var(--accent)" : "var(--edge)";
  return (
    <path
      d={d} fill="none" stroke={color} strokeWidth={conductor ? 1.5 : 2}
      strokeDasharray={conductor || active ? "4 4" : undefined}
      className={active ? "animate-dash" : undefined}
    />
  );
}
