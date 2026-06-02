// Fix B — the graphical Flow view (n8n / ComfyUI lineage). The run's topology drawn
// as a node graph instead of a log: a vertical chain of PLAN STEP nodes (pending →
// active → complete/failed), a sticky PLAN node at the head, and CONDUCTOR nodes that
// branch to the right of whichever step triggered them, joined by an orange dashed
// edge. The edge entering the active step "marches" (animated dash) so the eye lands
// on where work is happening now. Pure SVG + foreignObject — no graph library, node
// count is hard-capped (MAX_GRAPH_NODES) upstream, and the canvas scrolls as the run
// grows. State is always colour + glyph + label, so reduced-motion loses nothing.
import type { FlowState, FlowStep, StepStatus } from "../../lib/feed";
import { conductorsForStep } from "../../lib/feed";

const NODE_W = 188;
const NODE_H = 46;
const V_GAP = 34;
const TOP = 70;            // room for the sticky plan node
const STEP_X = 28;         // left of the step column
const COND_X = STEP_X + NODE_W + 90;   // conductor column
const COND_W = 184;

// `cvar` is the OKLCH channel var so both the glyph colour and the translucent
// active-step glow derive from one token (no hardcoded hex).
const STEP_TONE: Record<StepStatus, { border: string; cvar: string; glyph: string; label: string }> = {
  pending:  { border: "border-ink-700",  cvar: "--ink-700-c",        glyph: "○", label: "text-mist-400" },
  active:   { border: "border-accent",   cvar: "--accent-c",         glyph: "◐", label: "text-mist-100" },
  complete: { border: "border-good",     cvar: "--status-success-c", glyph: "✓", label: "text-mist-200" },
  failed:   { border: "border-bad",      cvar: "--status-error-c",   glyph: "✗", label: "text-mist-100" },
};

function stepY(i: number): number { return TOP + i * (NODE_H + V_GAP); }

export function FlowGraph({ flow, live }: { flow: FlowState; live: boolean }) {
  const steps = flow.steps.length
    ? flow.steps
    : [{ n: 1, status: "active" as StepStatus, turns: 0 }];
  const height = stepY(steps.length) + 20;
  // include the conductor column only if any conductor fired (keeps the canvas tight)
  const width = flow.conductors.length ? COND_X + COND_W + 24 : STEP_X + NODE_W + 24;

  return (
    <div className="h-full overflow-auto rounded-lg border border-ink-800 bg-ink-950/40">
      <svg width={width} height={Math.max(height, 240)} className="block">
        {/* PLAN head node + edge into step 1 */}
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
        {steps.map((st, i) =>
          conductorsForStep(flow, st.n).map((c, k) => (
            <Edge
              key={`ce${c.id}`}
              x1={STEP_X + NODE_W} y1={stepY(i) + NODE_H / 2}
              x2={COND_X} y2={stepY(i) + NODE_H / 2 + k * (NODE_H + 10)}
              conductor
              active={!c.resolved && live}
            />
          )),
        )}

        {/* step nodes */}
        {steps.map((st, i) => <StepNode key={st.n} step={st} y={stepY(i)} live={live} />)}

        {/* conductor nodes */}
        {steps.map((st, i) =>
          conductorsForStep(flow, st.n).map((c, k) => (
            <foreignObject
              key={c.id}
              x={COND_X} y={stepY(i) + NODE_H / 2 + k * (NODE_H + 10) - NODE_H / 2}
              width={COND_W} height={NODE_H}
            >
              <div className={`flex h-full flex-col justify-center rounded-md border px-2.5 ${
                c.resolved ? "border-good/50 bg-good-soft/10" : "border-warn/60 bg-warn-soft/10"}`}>
                <div className="flex items-center gap-1.5">
                  <span className={c.resolved ? "text-good" : "text-warn"}>{c.resolved ? "✦" : "⚡"}</span>
                  <span className="truncate text-[11px] font-medium text-mist-100">
                    conductor{c.tier ? ` · ${c.tier}` : ""}
                  </span>
                </div>
                <span className="truncate text-[10px] text-mist-400">
                  {c.resolved ? (c.model || "guidance ready") : c.reason}
                </span>
              </div>
            </foreignObject>
          )),
        )}
      </svg>
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

function StepNode({ step, y, live }: { step: FlowStep; y: number; live: boolean }) {
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
          <div className="truncate text-[10px] text-mist-400">
            {step.status}{step.turns ? ` · ${step.turns} turn${step.turns === 1 ? "" : "s"}` : ""}
          </div>
        </div>
      </div>
    </foreignObject>
  );
}

function Edge({ x1, y1, x2, y2, active, conductor }:
  { x1: number; y1: number; x2: number; y2: number; active?: boolean; conductor?: boolean }) {
  // orthogonal-ish bezier so branches read like a flow diagram, not a star
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
