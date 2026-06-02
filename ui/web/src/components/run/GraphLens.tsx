// PART IV.4 — the optional GRAPH lens. The timeline is the readable default; this
// renders a turn as a staged left→right DAG (plan → research → build → verify →
// checkpoint) for when the SHAPE of a run matters more than its sequence. Nodes are
// typed by step kind; escalations render as a fallback branch. Bounded and calm by
// construction (stages, not a sprawling force graph) — exactly the "lens" the spec
// asks for, with zero graph-lib dependency.
import { Dot } from "../ui";
import { researchFanOut } from "../../state";
import type { Turn } from "../../state";
import type { TimelineEntry } from "../../types";

type Tone = "good" | "warn" | "bad" | "info" | "accent" | "muted";

interface Node { label: string; tone: Tone; sub?: string }
interface Stage { key: string; title: string; nodes: Node[] }

function toneFor(e: TimelineEntry): Tone {
  switch (e.status) {
    case "ok": case "pass": return "good";
    case "fail": return "bad";
    case "slow": return "warn";
    case "running": return "accent";
    default: return "info";
  }
}

function buildStages(turn: Turn): Stage[] {
  const fan = researchFanOut(turn);
  const researchKeys = new Set(fan ? fan.sources.map((s) => s.key) : []);

  const plan: Node[] = turn.plan
    ? [{ label: "PLAN.md", tone: "info", sub: `${turn.plan.length} items` }]
    : turn.phase === "plan" ? [{ label: "planning", tone: "accent" }] : [];

  const research: Node[] = fan
    ? fan.sources.slice(0, 6).map((s) => ({ label: s.title, tone: toneFor(s), sub: s.server || undefined }))
    : [];

  const build: Node[] = [];
  const verify: Node[] = [];
  const checkpoint: Node[] = [];
  const fallback: Node[] = [];

  for (const e of turn.entries) {
    if (researchKeys.has(e.key)) continue;
    if (e.kind === "gate") verify.push({ label: e.title.replace(/^gate:\s*/, ""), tone: toneFor(e) });
    else if (e.kind === "checkpoint") checkpoint.push({ label: e.title, tone: "good", sub: e.subtitle });
    else if (e.kind === "escalation") fallback.push({ label: e.title, tone: "warn", sub: e.subtitle });
    else if (e.kind === "fileop") build.push({ label: e.title, tone: "info" });
    else if (e.kind === "shell") build.push({ label: e.title, tone: toneFor(e), sub: "shell" });
    else if (e.kind === "tool" || e.kind === "stream") build.push({ label: e.title, tone: toneFor(e), sub: e.server || undefined });
  }

  const stages: Stage[] = [
    { key: "plan", title: "Plan", nodes: plan },
    { key: "research", title: "Research", nodes: research },
    { key: "build", title: "Build", nodes: build.slice(-8) },
    { key: "verify", title: "Verify", nodes: verify },
    { key: "checkpoint", title: "Checkpoint", nodes: checkpoint },
  ];
  // keep the fallback branch as an addendum so escalations stay visible
  if (fallback.length) stages.push({ key: "fallback", title: "Fallback ↘", nodes: fallback });
  return stages.filter((s) => s.nodes.length > 0);
}

export function GraphLens({ turn }: { turn: Turn }) {
  const stages = buildStages(turn);
  if (stages.length === 0) {
    return <p className="px-2 py-6 text-center text-xs text-mist-400">No shape to graph yet.</p>;
  }
  return (
    <div className="overflow-x-auto px-1 py-3">
      <div className="flex items-stretch gap-1">
        {stages.map((stage, si) => (
          <div key={stage.key} className="flex items-stretch gap-1">
            <div className="min-w-[140px] rounded-lg border border-ink-800 bg-ink-850 p-2">
              <div className="mb-1.5 text-[11px] uppercase tracking-wide text-mist-400">{stage.title}</div>
              <div className="space-y-1">
                {stage.nodes.map((n, i) => (
                  <div
                    key={`${stage.key}-${i}`}
                    className="flex items-center gap-1.5 rounded-md border border-ink-800 bg-ink-900 px-2 py-1"
                  >
                    <Dot tone={n.tone} pulse={n.tone === "accent"} />
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-[11px] text-mist-100" style={{ maxWidth: 110 }}>
                        {n.label}
                      </span>
                      {n.sub && <span className="block truncate text-[10px] text-mist-400" style={{ maxWidth: 110 }}>{n.sub}</span>}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            {si < stages.length - 1 && stages[si].key !== "fallback" && (
              <div className="flex items-center text-mist-400" aria-hidden>→</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
