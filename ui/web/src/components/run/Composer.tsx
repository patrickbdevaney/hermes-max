// The prompt field that ACTUATES the agent (PART IV.6) — the same role typing into
// `hermes` plays. Enter sends; Shift+Enter inserts a newline. It is disabled while
// the current turn is still working (one turn at a time), and re-focuses on handback
// so the conversational loop feels immediate.
import { useEffect, useRef, useState } from "react";

export function Composer({ onSend, working, autoFocus, placeholder }:
  {
    onSend: (text: string) => void;
    working: boolean;
    autoFocus?: boolean;
    placeholder?: string;
  }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Re-focus when the agent hands back (working → false).
  useEffect(() => {
    if (!working && autoFocus) ref.current?.focus();
  }, [working, autoFocus]);

  function send() {
    const t = text.trim();
    if (!t || working) return;
    onSend(t);
    setText("");
  }

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900 p-2 focus-within:border-accent">
      <textarea
        ref={ref}
        value={text}
        rows={2}
        disabled={working}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
        }}
        placeholder={working ? "the agent is working…" : (placeholder ?? "Describe the next step…")}
        className="w-full resize-none bg-transparent px-2 py-1 text-sm text-mist-100 outline-none placeholder:text-mist-400 disabled:opacity-50"
      />
      <div className="flex items-center justify-between px-1 pt-1">
        <span className="text-[11px] text-mist-400">
          {working ? "one turn at a time — it'll hand back when done" : "Enter to send · Shift+Enter for a newline"}
        </span>
        <button
          type="button"
          onClick={send}
          disabled={working || !text.trim()}
          className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-ink-950 transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send ▸
        </button>
      </div>
    </div>
  );
}
