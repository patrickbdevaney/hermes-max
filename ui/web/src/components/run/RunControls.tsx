// Phase 5.2 — Steer / Interrupt / Pending as THREE distinct controls (most tools
// conflate them; separating them is the craft). The agent is a one-shot process
// per turn, so each maps to a real, distinct behaviour:
//   • INTERRUPT — abort the running turn now (SIGTERM the process group).
//   • PAUSE / RESUME — suspend / continue the turn (SIGSTOP / SIGCONT).
//   • STEER — a non-destructive nudge delivered at the NEXT safe point (enqueued
//     to the FRONT; sent the instant the turn hands back).
//   • PENDING — queue a message for later (enqueued to the BACK).
// Steer/Pending share one input; Interrupt/Pause are immediate signal buttons.
import { useState } from "react";

export function RunControls({ working, paused, pending, busy, onInterrupt, onPause, onResume, onSteer, onPending, onRemovePending }:
  {
    working: boolean;
    paused: boolean;
    pending: string[];
    busy?: boolean;
    onInterrupt: () => void;
    onPause: () => void;
    onResume: () => void;
    onSteer: (text: string) => void;
    onPending: (text: string) => void;
    onRemovePending: (i: number) => void;
  }) {
  const [msg, setMsg] = useState("");
  const enq = (fn: (t: string) => void) => { const t = msg.trim(); if (!t) return; fn(t); setMsg(""); };

  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-mist-500">control</span>

        {paused ? (
          <Btn label="▶ Resume" tone="accent" disabled={busy} onClick={onResume} title="SIGCONT — continue the turn" />
        ) : (
          <Btn label="⏸ Pause" disabled={busy || !working} onClick={onPause} title="SIGSTOP — suspend the turn" />
        )}
        <Btn label="⏹ Interrupt" tone="bad" disabled={busy || !working} onClick={onInterrupt} title="SIGTERM — abort the current turn" />

        <div className="ml-auto flex items-center gap-1.5">
          <input
            value={msg}
            onChange={(e) => setMsg(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") enq(onSteer); }}
            placeholder="nudge the agent…"
            className="w-48 rounded-md border border-ink-700 bg-ink-input px-2 py-1 text-xs text-mist-100 outline-none focus:border-accent"
          />
          <Btn label="Steer →" tone="accent" disabled={!msg.trim()} onClick={() => enq(onSteer)}
            title="deliver at the next safe point (non-destructive)" />
          <Btn label="Queue" disabled={!msg.trim()} onClick={() => enq(onPending)}
            title="queue for later — sent on the next handback" />
        </div>
      </div>

      {pending.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-ink-800 pt-2">
          <span className="text-[10px] uppercase tracking-wide text-mist-500">pending</span>
          {pending.map((p, i) => (
            <span key={i} className="flex items-center gap-1 rounded-full border border-ink-700 bg-ink-850 px-2 py-0.5 text-[11px] text-mist-300">
              <span className="max-w-[180px] truncate">{p}</span>
              <button type="button" onClick={() => onRemovePending(i)} className="text-mist-500 hover:text-mist-100" aria-label="remove">✕</button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Btn({ label, onClick, disabled, tone, title }:
  { label: string; onClick: () => void; disabled?: boolean; tone?: "accent" | "bad"; title?: string }) {
  const cls = tone === "accent" ? "border-accent/40 text-accent hover:bg-accent-soft/15"
    : tone === "bad" ? "border-bad/40 text-bad hover:bg-bad/10"
    : "border-ink-700 text-mist-200 hover:bg-ink-850";
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title}
      className={`rounded-md border px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${cls}`}>
      {label}
    </button>
  );
}
