// Diff-style + code artifact rendering (1.3). file_write payloads render as
// red/green unified diffs (Devin/Replit idiom); fenced code renders
// syntax-tinted — but ONLY when the code block is COMPLETE (even count of ```
// fences). Highlighting a half-streamed block flickers as the tokenizer's view
// of the source flips every frame; the `isCompleteCodeBlock` guard is the known
// fix. Hand-rolled (no highlight.js / shiki) to hold the bundle budget — a
// light, safe per-line tint for strings / comments / numbers, never
// dangerouslySetInnerHTML.
import React from "react";

const MAX_LINES = 400; // bound the DOM; artifacts can be huge

// Even number of ``` fences ⇒ every opened block was closed ⇒ safe to tint.
export function isCompleteCodeBlock(s: string): boolean {
  return (s.match(/```/g)?.length ?? 0) % 2 === 0;
}

// Does this look like a unified diff (hunks or +/- gutters)?
function looksLikeDiff(s: string): boolean {
  return /^@@ |^\+\+\+ |^--- |^[+-](?![+-])/m.test(s);
}

// Strip one surrounding ```lang fence if present; report the language.
function unfence(s: string): { code: string; lang?: string } {
  const m = s.match(/^```([a-zA-Z0-9_+-]*)\n([\s\S]*?)\n?```\s*$/);
  if (m) return { code: m[2], lang: m[1] || undefined };
  return { code: s };
}

function cap(lines: string[]): { lines: string[]; dropped: number } {
  if (lines.length <= MAX_LINES) return { lines, dropped: 0 };
  return { lines: lines.slice(0, MAX_LINES), dropped: lines.length - MAX_LINES };
}

// A minimal, safe line tinter: comments, strings, numbers. Order matters — a
// line that is a comment is tinted whole; otherwise strings then numbers.
function tintLine(line: string, key: number): React.ReactNode {
  const trimmed = line.trimStart();
  if (trimmed.startsWith("//") || trimmed.startsWith("#") || trimmed.startsWith("*") || trimmed.startsWith("/*")) {
    return <span key={key} className="text-mist-500">{line}</span>;
  }
  const parts: React.ReactNode[] = [];
  // split on string literals and numbers, keeping delimiters
  const re = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`|\b\d[\d_.]*\b)/g;
  let last = 0; let m: RegExpExecArray | null; let i = 0;
  while ((m = re.exec(line))) {
    if (m.index > last) parts.push(<span key={`${key}-t${i++}`}>{line.slice(last, m.index)}</span>);
    const tok = m[0];
    const isStr = /^["'`]/.test(tok);
    parts.push(
      <span key={`${key}-h${i++}`} className={isStr ? "text-good" : "text-status-info"}>{tok}</span>,
    );
    last = m.index + tok.length;
  }
  if (last < line.length) parts.push(<span key={`${key}-e`}>{line.slice(last)}</span>);
  return <React.Fragment key={key}>{parts}</React.Fragment>;
}

export function DiffBlock({ text }: { text: string }) {
  const { lines, dropped } = cap(text.replace(/\n$/, "").split("\n"));
  let adds = 0, dels = 0;
  for (const l of lines) { if (/^\+(?!\+)/.test(l)) adds++; else if (/^-(?!-)/.test(l)) dels++; }
  return (
    <div className="overflow-x-auto rounded-md border border-ink-800 bg-ink-input font-mono text-[11px] leading-relaxed">
      <div className="flex items-center gap-3 border-b border-ink-800 px-3 py-1 text-[10px] text-mist-500">
        <span className="text-good">+{adds}</span>
        <span className="text-bad">−{dels}</span>
        <span>unified diff</span>
      </div>
      <pre className="m-0 p-0">
        {lines.map((l, i) => {
          const add = /^\+(?!\+\+)/.test(l);
          const del = /^-(?!--)/.test(l);
          const hunk = /^@@/.test(l) || /^(\+\+\+|---)/.test(l);
          const cls = add ? "bg-good/10 text-good"
            : del ? "bg-bad/10 text-bad"
            : hunk ? "text-status-info"
            : "text-mist-300";
          return (
            <div key={i} className={`px-3 ${cls}`}>{l || " "}</div>
          );
        })}
      </pre>
      {dropped > 0 && (
        <div className="border-t border-ink-800 px-3 py-1 text-[10px] text-mist-500">
          … {dropped.toLocaleString()} more lines (truncated for display)
        </div>
      )}
    </div>
  );
}

export function CodeBlock({ text, lang }: { text: string; lang?: string }) {
  const complete = isCompleteCodeBlock(text);
  const { lines, dropped } = cap(text.replace(/\n$/, "").split("\n"));
  return (
    <div className="overflow-x-auto rounded-md border border-ink-800 bg-ink-input font-mono text-[11px] leading-relaxed">
      {lang && (
        <div className="border-b border-ink-800 px-3 py-1 text-[10px] text-mist-500">
          {lang}{!complete && " · streaming…"}
        </div>
      )}
      <pre className="m-0 px-3 py-1.5 text-mist-200">
        {lines.map((l, i) => (
          <div key={i}>{complete ? tintLine(l, i) : (l || " ")}</div>
        ))}
      </pre>
      {dropped > 0 && (
        <div className="border-t border-ink-800 px-3 py-1 text-[10px] text-mist-500">
          … {dropped.toLocaleString()} more lines (truncated for display)
        </div>
      )}
    </div>
  );
}

// Decide how to render an arbitrary artifact body.
export function Artifact({ text }: { text: string }) {
  if (!text?.trim()) return null;
  if (looksLikeDiff(text)) return <DiffBlock text={text} />;
  const { code, lang } = unfence(text);
  if (lang || code.includes("\n")) return <CodeBlock text={code} lang={lang} />;
  return <div className="whitespace-pre-wrap break-words font-mono text-[11px] text-mist-300">{text}</div>;
}
