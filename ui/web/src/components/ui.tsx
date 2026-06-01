// Small shared primitives. Every status is signalled by COLOR + ICON + LABEL
// together — never colour alone (WCAG / colour-blind safe).
import React from "react";

export function fmtUsd(x: number): string {
  // Always six decimals — the fabric's costs live in the 4th–6th place.
  return `$${(x ?? 0).toFixed(6)}`;
}

export function fmtInt(n: number | undefined | null): string {
  return (n ?? 0).toLocaleString();
}

type Tone = "good" | "warn" | "bad" | "info" | "muted" | "accent";

const TONE: Record<Tone, string> = {
  good: "text-good", warn: "text-warn", bad: "text-bad",
  info: "text-mist-200", muted: "text-mist-400", accent: "text-accent",
};
const DOT: Record<Tone, string> = {
  good: "bg-good", warn: "bg-warn", bad: "bg-bad",
  info: "bg-mist-300", muted: "bg-mist-400", accent: "bg-accent",
};

export function Dot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${DOT[tone]} ${pulse ? "animate-pulse2" : ""}`}
      aria-hidden
    />
  );
}

export function Glyph({ name }: { name: string }) {
  // Monochrome glyphs (no icon-font dependency) — paired with a text label.
  const g: Record<string, string> = {
    running: "◐", ok: "✓", fail: "✗", slow: "◷", pass: "✓",
    escalation: "↳", gate: "▣", checkpoint: "⚑", fileop: "✎", shell: "▸",
    tool: "•", phase: "◆", info: "·",
  };
  return <span aria-hidden className="font-mono">{g[name] ?? "•"}</span>;
}

export function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  const ring: Record<Tone, string> = {
    good: "border-good/40", warn: "border-warn/40", bad: "border-bad/40",
    info: "border-ink-600", muted: "border-ink-700", accent: "border-accent/40",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border ${ring[tone]} ${TONE[tone]} px-2.5 py-0.5 text-xs font-medium`}>
      {children}
    </span>
  );
}

// A determinate progress bar — NOT a spinner (Nielsen's >10s rule). When total is
// unknown it renders an honest "activity" fill that grows with started steps,
// capped below 100% so it never falsely claims completion.
export function Bar({ done, total, indeterminate }:
  { done: number; total: number; indeterminate?: boolean }) {
  const pct = indeterminate
    ? Math.min(92, 12 + done * 6)
    : total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-ink-800"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={indeterminate ? `working, step ${done}` : `${done} of ${total} steps`}
    >
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
