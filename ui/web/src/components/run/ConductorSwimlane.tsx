// Phase 3 — the Conductor Swimlane (THE hero, uniquely ours). The visual story
// in one glance: a cheap local EXECUTOR runs many steps in a long calm lane;
// the expensive cloud CONDUCTOR reaches down rarely but decisively, dropping a
// bright gold INTERVENTION PIN into the exact step it corrected. The contrast —
// rare bright pins against a long neutral stream — IS the architectural thesis.
//
//   • conductor lane (top, --conductor gold): planner invocations (model /
//     tokens / cost from the resolved guidance)
//   • executor lane (middle, --executor slate): the step stream, width ∝ turns
//   • a pin drops from each conductor node to the step it fired on; SOLID when
//     guidance was applied (resolved), DASHED+marching while pending (3.1)
//   • verify-gate SPINE (bottom): pytest pass/fail/ done-rejected per step —
//     ground truth as the run's backbone (3.3)
//   • clicking a pin opens its "what happened & why" card below (3.2)
import { useState } from "react";
import type { FlowState, FlowStep } from "../../lib/feed";
import { conductorsForStep } from "../../lib/feed";
import { EmptyMoment } from "../ui";
import { fmtMoney } from "../../lib/shadow";

const PAD_X = 16;
const COND_Y = 14, COND_H = 50;
const EXEC_Y = 94, EXEC_H = 56;
const SPINE_Y = 170;
const HEIGHT = 198;

function colW(s: FlowStep): number { return 116 + Math.min(s.turns, 6) * 10; }

const VERIFY: Record<string, { glyph: string; cls: string; label: string }> = {
  pass:     { glyph: "✓", cls: "var(--status-success)", label: "verify pass" },
  fail:     { glyph: "✗", cls: "var(--status-error)",   label: "verify fail" },
  rejected: { glyph: "⊘", cls: "var(--status-warning)", label: "done rejected" },
};

export function ConductorSwimlane({ flow, live }: { flow: FlowState; live: boolean }) {
  const [sel, setSel] = useState<string | null>(null);
  const steps = flow.steps.length ? flow.steps : [{ n: 1, status: "active" as const, turns: 0 }];

  // x layout
  let x = PAD_X;
  const lay = steps.map((s) => { const w = colW(s); const o = { s, x, w, cx: x + w / 2 }; x += w; return o; });
  const width = Math.max(x + PAD_X, 320);

  const interventions = [...flow.conductors].sort((a, b) => a.ts - b.ts);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {/* lane legend */}
      <div className="flex items-center gap-4 px-1 text-[11px]">
        <span className="flex items-center gap-1.5 text-conductor"><span aria-hidden>⚡</span>conductor (cloud planner)</span>
        <span className="flex items-center gap-1.5" style={{ color: "var(--executor)" }}><span aria-hidden>▦</span>executor (local worker)</span>
        <span className="ml-auto text-mist-500">{interventions.length} intervention{interventions.length === 1 ? "" : "s"} · {steps.length} steps</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-ink-800 bg-ink-950/40">
        <svg width={width} height={HEIGHT} className="block">
          {/* lane backdrops */}
          <rect x={0} y={COND_Y - 6} width={width} height={COND_H + 12} fill="oklch(var(--conductor-c) / 0.04)" />
          <rect x={0} y={EXEC_Y - 6} width={width} height={EXEC_H + 12} fill="oklch(var(--executor-c) / 0.05)" />
          {/* verify spine baseline */}
          <line x1={PAD_X} y1={SPINE_Y} x2={width - PAD_X} y2={SPINE_Y} stroke="var(--edge)" strokeWidth={1.5} />

          {lay.map(({ s, x: sx, w, cx }) => {
            const fires = conductorsForStep(flow, s.n);
            const v = s.lastVerify ? VERIFY[s.lastVerify] : null;
            const stepBorder = s.status === "failed" ? "border-bad/60"
              : s.status === "complete" ? "border-good/40"
              : s.status === "active" ? "border-accent/60" : "border-ink-700";
            return (
              <g key={s.n}>
                {/* executor step block */}
                <foreignObject x={sx + 6} y={EXEC_Y} width={w - 12} height={EXEC_H}>
                  <div className={`flex h-full flex-col justify-center rounded-md border bg-ink-900 px-2.5 ${stepBorder}`}>
                    <div className="text-xs font-medium text-mist-100">Step {s.n}</div>
                    <div className="truncate text-[10px] text-mist-400">
                      {s.turns ? `${s.turns} turn${s.turns === 1 ? "" : "s"}` : s.status}
                    </div>
                  </div>
                </foreignObject>

                {/* verify spine marker */}
                <circle cx={cx} cy={SPINE_Y} r={3} fill={v ? v.cls : "var(--edge)"} />
                {v && (
                  <>
                    <text x={cx} y={SPINE_Y + 18} textAnchor="middle" fontSize={11} fill={v.cls}>{v.glyph}</text>
                    <title>{v.label}</title>
                  </>
                )}

                {/* conductor pins + nodes for this step */}
                {fires.map((c, k) => {
                  const px = cx + (k - (fires.length - 1) / 2) * 22;
                  const pinActive = !c.resolved && live;
                  return (
                    <g key={c.id} className="cursor-pointer" onClick={() => setSel((p) => (p === c.id ? null : c.id))}>
                      {/* the intervention pin: conductor lane → executor step */}
                      <line
                        x1={px} y1={COND_Y + COND_H} x2={px} y2={EXEC_Y}
                        stroke="var(--conductor)" strokeWidth={c.resolved ? 2 : 1.5}
                        strokeDasharray={c.resolved ? undefined : "4 4"}
                        className={pinActive ? "animate-dash" : undefined}
                      />
                      {/* arrowhead into the executor lane (guidance applied) */}
                      <path d={`M${px - 4},${EXEC_Y - 6} L${px + 4},${EXEC_Y - 6} L${px},${EXEC_Y} Z`} fill="var(--conductor)" />
                      {/* conductor node */}
                      <foreignObject x={px - 56} y={COND_Y} width={112} height={COND_H}>
                        <div className={`flex h-full flex-col justify-center rounded-md border px-2 ${sel === c.id ? "border-conductor bg-conductor/15" : "border-conductor/50 bg-conductor/8"}`}>
                          <div className="flex items-center gap-1 text-conductor">
                            <span aria-hidden>{c.resolved ? "✦" : "⚡"}</span>
                            <span className="truncate text-[10px] font-medium">{c.tier ?? "conductor"}</span>
                          </div>
                          <div className="truncate text-[10px] text-mist-300">{c.model ?? c.reason}</div>
                          {c.resolved && (c.tokens || c.cost != null) && (
                            <div className="truncate font-mono text-[9px] text-mist-500">
                              {c.tokens ? `${c.tokens} tok` : ""}{c.cost != null ? ` · ${fmtMoney(c.cost)}` : ""}
                            </div>
                          )}
                        </div>
                      </foreignObject>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>

      {/* "what just happened and why" cards (3.2) */}
      <div className="max-h-[42%] shrink-0 space-y-2 overflow-auto">
        {interventions.length === 0 ? (
          <EmptyMoment
            icon="▦"
            title="No conductor interventions"
            hint="The local executor is running unsupervised — the cloud planner hasn't needed to reach down and correct it."
          />
        ) : (
          interventions.map((c) => (
            <InterventionCard key={c.id} node={c} open={sel === c.id} onToggle={() => setSel((p) => (p === c.id ? null : c.id))} />
          ))
        )}
      </div>
    </div>
  );
}

function InterventionCard({ node, open, onToggle }:
  { node: FlowState["conductors"][number]; open: boolean; onToggle: () => void }) {
  return (
    <div className="rounded-lg border border-conductor/30 bg-ink-900">
      <button type="button" onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <span className="text-conductor" aria-hidden>{node.resolved ? "✦" : "⚡"}</span>
        <span className="text-xs font-medium text-mist-100">Step {node.step}</span>
        <span className="truncate text-xs text-mist-300">{node.reason}</span>
        <span className="ml-auto text-[10px] text-mist-500">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-ink-800 px-3 py-2.5 text-xs">
          <Row k="why it fired" v={node.reason + (node.failures ? ` (after ${node.failures} verify failure${node.failures === 1 ? "" : "s"})` : "")} />
          <Row k="planner" v={node.resolved ? (node.model ?? "guidance applied") : "awaiting guidance…"} tone="text-conductor" />
          {node.resolved && (
            <Row k="what it cost" v={`${node.tokens ?? 0} tokens · ${node.cost != null ? fmtMoney(node.cost) : "free"}`} />
          )}
          <Row k="what changed" v={node.resolved
            ? "Guidance was injected into the executor's next turn on this step."
            : "The executor is paused on this step until guidance returns."} />
        </div>
      )}
    </div>
  );
}

function Row({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="flex gap-3">
      <span className="w-24 shrink-0 text-[10px] uppercase tracking-wide text-mist-500">{k}</span>
      <span className={`flex-1 ${tone ?? "text-mist-200"}`}>{v}</span>
    </div>
  );
}
