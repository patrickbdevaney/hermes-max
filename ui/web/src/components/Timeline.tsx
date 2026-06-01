// L1 — the structured timeline for ONE turn. A vertical stream of step cards: tool
// calls (request → latency → result), live token streams, escalations (the cheapest-
// first ladder, animated), gates (red/green), checkpoints, file ops. Any tool card
// expands to its correlated raw span subtree (L2, run-global spans).
import { useEffect, useRef, useState } from "react";
import { Badge, Dot, Glyph } from "./ui";
import { SpanTree, Diff } from "./SpanTree";
import { spansForEntry } from "../state";
import type { RunView, Turn } from "../state";
import type { TimelineEntry } from "../types";

type Tone = "good" | "warn" | "bad" | "info" | "accent" | "muted";

const TEXT: Record<Tone, string> = {
  good: "text-good", warn: "text-warn", bad: "text-bad",
  info: "text-mist-200", accent: "text-accent", muted: "text-mist-400",
};

function statusTone(status: TimelineEntry["status"]): Tone {
  switch (status) {
    case "running": return "accent";
    case "ok": case "pass": return "good";
    case "fail": return "bad";
    case "slow": return "warn";
    default: return "info";
  }
}
function statusLabel(e: TimelineEntry): string {
  if (e.kind === "tool") return e.status;
  if (e.status === "pass") return "pass";
  if (e.status === "fail") return "fail";
  return e.kind;
}

const RUNGS = ["LSP", "repair", "steer", "re-plan", "frontier"];
function EscalationLadder({ to }: { to: string }) {
  const hit = RUNGS.findIndex((r) => to.toLowerCase().includes(r.toLowerCase().replace("-", "")));
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      {RUNGS.map((r, i) => (
        <span key={r} className="flex items-center gap-1">
          <span className={`rounded px-1.5 py-0.5 text-[10px] ${
            i === hit ? "bg-warn-soft/40 text-warn animate-flash" : "bg-ink-800 text-mist-400"}`}>
            {r}
          </span>
          {i < RUNGS.length - 1 && <span className="text-[10px] text-mist-400">→</span>}
        </span>
      ))}
    </div>
  );
}

export function Timeline({ turn, view, follow = true }:
  { turn: Turn; view: RunView; follow?: boolean }) {
  const entries = turn.entries;
  const [open, setOpen] = useState<Set<string>>(new Set());
  const tailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (follow && turn.status === "working") {
      tailRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [entries.length, follow, turn.status]);

  const toggle = (k: string) =>
    setOpen((prev) => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });

  if (entries.length === 0) {
    return (
      <p className="px-2 py-6 text-center text-xs text-mist-400">
        {turn.status === "working" ? "Working…" : "No steps recorded for this turn."}
      </p>
    );
  }

  return (
    <div className="px-1 py-1">
      <ol className="space-y-1.5">
        {entries.map((e) => {
          const tone = statusTone(e.status);
          const isOpen = open.has(e.key);

          if (e.kind === "stream") {
            return (
              <li key={e.key} className="animate-risein">
                <div className="rounded-lg border border-accent/30 bg-ink-850 px-3 py-2">
                  <div className="mb-1 flex items-center gap-2 text-xs text-accent">
                    <Dot tone="accent" pulse /> generating
                  </div>
                  <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words font-mono text-xs text-mist-200">
                    {e.detail}<span className="animate-pulse2">▌</span>
                  </pre>
                </div>
              </li>
            );
          }

          const spans = isOpen && e.kind === "tool" ? spansForEntry(view, e) : [];
          const hasDetail = Boolean(e.detail) || e.kind === "escalation" || e.kind === "tool";
          return (
            <li key={e.key} className="animate-risein">
              <div className={`rounded-lg border border-ink-800 bg-ink-850 px-3 py-2 ${
                e.kind === "escalation" || e.kind === "checkpoint" ? "animate-flash" : ""}`}>
                <button
                  type="button"
                  onClick={() => hasDetail && toggle(e.key)}
                  className={`flex w-full items-center gap-3 text-left ${hasDetail ? "cursor-pointer" : "cursor-default"}`}
                  aria-expanded={hasDetail ? isOpen : undefined}
                >
                  <span className={`shrink-0 ${TEXT[tone]}`}>
                    <Glyph name={e.kind === "tool" ? e.status : e.kind} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate font-mono text-sm text-mist-100">{e.title}</span>
                      {e.server && <span className="truncate text-xs text-mist-400">{e.server}</span>}
                    </span>
                    {e.subtitle && <span className="mt-0.5 block truncate text-xs text-mist-400">{e.subtitle}</span>}
                  </span>
                  {e.progress && e.progress.total > 0 && (
                    <span className="shrink-0 text-xs tabular-nums text-mist-400">
                      {e.progress.done}/{e.progress.total}
                      {e.progress.eta_s != null && <> · ~{Math.round(e.progress.eta_s)}s</>}
                    </span>
                  )}
                  {e.latency_ms != null && (
                    <span className="shrink-0 font-mono text-xs tabular-nums text-mist-400">
                      {(e.latency_ms / 1000).toFixed(1)}s
                    </span>
                  )}
                  <Badge tone={tone}>
                    <Dot tone={tone} pulse={e.status === "running"} />
                    {statusLabel(e)}
                  </Badge>
                </button>

                {e.kind === "escalation" && <EscalationLadder to={e.title} />}

                {isOpen && (
                  <div className="mt-2 space-y-2">
                    {e.detail && (e.kind === "fileop" && /(^|\n)[+-]/.test(e.detail) ? (
                      <Diff text={e.detail} />
                    ) : (
                      <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-ink-950 px-3 py-2 font-mono text-xs text-mist-300">
                        {e.detail}
                      </pre>
                    ))}
                    {e.kind === "tool" && (
                      <div className="rounded border border-ink-800 bg-ink-900 p-2">
                        <div className="mb-1 text-[11px] uppercase tracking-wide text-mist-400">
                          raw span tree (L2)
                        </div>
                        <SpanTree view={view} spans={spans} />
                      </div>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      <div ref={tailRef} />
    </div>
  );
}
