// L2 — the raw OTLP span tree (the Phoenix/LangSmith-style view, embedded). Any
// L1 row expands into the correlated subtree; a "full trace" panel shows all roots.
// Renders span timing, status, attributes (with diff/code/text formatting), and
// span events — the exact tool I/O the operator drills into.
import { useState } from "react";
import { Dot, Glyph } from "./ui";
import { childrenOf } from "../state";
import type { RunView } from "../state";
import type { Span } from "../types";

type Tone = "good" | "warn" | "bad" | "info" | "muted" | "accent";

function statusTone(code: string): Tone {
  return code === "error" ? "bad" : code === "ok" ? "good" : "info";
}

// Unified-diff-ish renderer: +added / -removed / @@hunk, coloured (+ icon by char).
export function Diff({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <pre className="overflow-x-auto rounded bg-ink-950 p-2 text-xs leading-relaxed">
      {lines.map((ln, i) => {
        const c = ln[0];
        const cls = c === "+" ? "text-good" : c === "-" ? "text-bad"
          : ln.startsWith("@@") ? "text-accent" : "text-mist-400";
        return <div key={i} className={`font-mono ${cls}`}>{ln || " "}</div>;
      })}
    </pre>
  );
}

const DIFF_KEYS = /diff|patch/i;
const CODE_KEYS = /code|content|snippet|body|source/i;

function AttrValue({ k, v }: { k: string; v: any }) {
  const str = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  if (typeof v === "string" && DIFF_KEYS.test(k) && /(^|\n)[+-]/.test(v)) {
    return <Diff text={v} />;
  }
  if (typeof v === "string" && (CODE_KEYS.test(k) || v.includes("\n") || v.length > 80)) {
    return (
      <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-ink-950 p-2 font-mono text-xs text-mist-300">
        {str}
      </pre>
    );
  }
  return <span className="break-words font-mono text-xs text-mist-200">{str}</span>;
}

function AttrTable({ attrs }: { attrs: Record<string, any> }) {
  const keys = Object.keys(attrs);
  if (keys.length === 0) return null;
  return (
    <dl className="mt-1 space-y-1">
      {keys.map((k) => (
        <div key={k} className="grid grid-cols-[8rem_1fr] gap-2">
          <dt className="truncate font-mono text-[11px] text-mist-400">{k}</dt>
          <dd className="min-w-0"><AttrValue k={k} v={attrs[k]} /></dd>
        </div>
      ))}
    </dl>
  );
}

function SpanNode({ view, sp, depth }: { view: RunView; sp: Span; depth: number }) {
  const kids = childrenOf(view, sp.span_id);
  const [open, setOpen] = useState(depth < 1);
  const tone = statusTone(sp.status.code);
  const hasBody = Object.keys(sp.attributes).length > 0 || sp.events.length > 0 || kids.length > 0;

  return (
    <li>
      <div
        className="flex items-center gap-2 rounded px-1.5 py-1 hover:bg-ink-850"
        style={{ paddingLeft: `${depth * 14 + 6}px` }}
      >
        <button
          type="button"
          onClick={() => hasBody && setOpen((o) => !o)}
          className={`flex min-w-0 flex-1 items-center gap-2 text-left ${hasBody ? "cursor-pointer" : "cursor-default"}`}
          aria-expanded={hasBody ? open : undefined}
        >
          <span className="w-3 shrink-0 font-mono text-[11px] text-mist-400">
            {hasBody ? (open ? "▾" : "▸") : ""}
          </span>
          <Dot tone={tone} />
          <span className="truncate font-mono text-xs text-mist-100">{sp.name}</span>
          {sp.scope && <span className="truncate text-[11px] text-mist-400">{sp.scope}</span>}
          <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-mist-400">
            {sp.duration_ms != null ? `${sp.duration_ms.toFixed(1)}ms` : ""}
          </span>
          {sp.status.code === "error" && (
            <span className="shrink-0 text-[11px] text-bad">error</span>
          )}
        </button>
      </div>
      {open && hasBody && (
        <div style={{ paddingLeft: `${depth * 14 + 24}px` }} className="pb-1">
          {sp.status.message && <div className="text-[11px] text-bad">{sp.status.message}</div>}
          <AttrTable attrs={sp.attributes} />
          {sp.events.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {sp.events.map((e, i) => (
                <li key={i} className="text-[11px] text-mist-400">
                  <Glyph name="info" /> {e.name}
                  {Object.keys(e.attributes).length > 0 &&
                    ` — ${Object.entries(e.attributes).map(([k, v]) => `${k}=${v}`).join(", ")}`}
                </li>
              ))}
            </ul>
          )}
          {kids.length > 0 && <SpanTree view={view} spans={kids} depth={depth + 1} />}
        </div>
      )}
    </li>
  );
}

export function SpanTree({ view, spans, depth = 0 }:
  { view: RunView; spans: Span[]; depth?: number }) {
  if (spans.length === 0) {
    return <p className="px-2 py-1 text-[11px] text-mist-400">no spans yet (the OTLP bridge feeds these)</p>;
  }
  return (
    <ul className="space-y-0.5">
      {spans.map((sp) => <SpanNode key={sp.span_id} view={view} sp={sp} depth={depth} />)}
    </ul>
  );
}
