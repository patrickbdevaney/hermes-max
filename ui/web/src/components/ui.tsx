// Small shared primitives. Every status is signalled by COLOR + ICON + LABEL
// together — never colour alone (WCAG / colour-blind safe).
import React from "react";
import { cn } from "../lib/cn";

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

// ── the three states every surface needs (Phase 0) ──────────────────────────
// Never a blank div, never a bare spinner, never a dead-end error.

// Skeleton — the loading state. Shape-first (not a spinner): the silhouette of
// the content that's coming. Shimmer is decorative; reduced-motion freezes it
// and the shape still reads as "loading".
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("hmx-skeleton rounded", className)} aria-hidden />;
}

export function SkeletonRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} role="status" aria-label="loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-5" />
      ))}
    </div>
  );
}

// EmptyMoment — a designed empty state: a glyph, a line, an optional CTA.
export function EmptyMoment({ icon = "◇", title, hint, action }:
  { icon?: string; title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-[160px] flex-col items-center justify-center gap-3 p-6 text-center">
      <span className="text-3xl text-mist-500" aria-hidden>{icon}</span>
      <div className="space-y-1">
        <p className="text-sm font-medium text-mist-200">{title}</p>
        {hint && <p className="max-w-sm text-xs text-mist-400">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

// ErrorState — explains + offers recovery (icon + label + colour, retry CTA).
export function ErrorState({ title = "Something went wrong", detail, onRetry }:
  { title?: string; detail?: string; onRetry?: () => void }) {
  return (
    <div className="flex h-full min-h-[160px] flex-col items-center justify-center gap-3 p-6 text-center">
      <span className="text-2xl text-bad" aria-hidden>✗</span>
      <div className="space-y-1">
        <p className="text-sm font-medium text-bad">{title}</p>
        {detail && <p className="max-w-md break-words text-xs text-mist-400">{detail}</p>}
      </div>
      {onRetry && (
        <button type="button" onClick={onRetry}
          className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-mist-200 transition-colors hover:bg-ink-800">
          ↻ Try again
        </button>
      )}
    </div>
  );
}
