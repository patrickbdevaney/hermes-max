// The prompt field that ACTUATES the agent (PART IV.6 / Phase 5.1). Enter sends;
// Shift+Enter inserts a newline; it's disabled while a turn is working (one turn
// at a time) and re-focuses on handback. Phase 5.1 additions: DRAFT persistence
// (survives reload), prompt HISTORY (↑/↓ recall when the field is empty), and a
// PLAN-FIRST toggle (Replit Plan-mode / Devin interactive planning — when on, the
// agent is asked to present a plan for review before executing). All client-side
// and namespaced by `historyKey` so a per-run draft never bleeds across runs.
import { useEffect, useRef, useState } from "react";

const MAX_HISTORY = 50;
const PLAN_FIRST_PREFIX =
  "Plan first: outline the steps you intend to take and present them for my review BEFORE executing. Task: ";

function load(key: string): string[] {
  try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch { return []; }
}

export function Composer({ onSend, working, autoFocus, placeholder, historyKey = "global", allowPlanFirst }:
  {
    onSend: (text: string) => void;
    working: boolean;
    autoFocus?: boolean;
    placeholder?: string;
    historyKey?: string;
    allowPlanFirst?: boolean;
  }) {
  const draftKey = `hmx.draft.${historyKey}`;
  const histKey = "hmx.prompt.history";
  const [text, setText] = useState(() => localStorage.getItem(draftKey) || "");
  const [planFirst, setPlanFirst] = useState(() => localStorage.getItem("hmx.planFirst") === "1");
  const [histIdx, setHistIdx] = useState(-1); // -1 = not browsing history
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (!working && autoFocus) ref.current?.focus(); }, [working, autoFocus]);
  // persist the draft as it's typed (debounce-free: localStorage is cheap here)
  useEffect(() => { try { localStorage.setItem(draftKey, text); } catch { /* quota */ } }, [text, draftKey]);
  useEffect(() => { try { localStorage.setItem("hmx.planFirst", planFirst ? "1" : "0"); } catch { /**/ } }, [planFirst]);

  function send() {
    const t = text.trim();
    if (!t || working) return;
    // record history (most-recent-first, de-duped)
    const hist = [t, ...load(histKey).filter((h) => h !== t)].slice(0, MAX_HISTORY);
    try { localStorage.setItem(histKey, JSON.stringify(hist)); } catch { /**/ }
    onSend(planFirst ? PLAN_FIRST_PREFIX + t : t);
    setText(""); setHistIdx(-1);
    try { localStorage.removeItem(draftKey); } catch { /**/ }
  }

  // ↑/↓ recall history only when the caret-line content is empty-ish, so it never
  // hijacks normal multi-line editing.
  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); return send(); }
    const hist = load(histKey);
    if (!hist.length) return;
    if (e.key === "ArrowUp" && (text === "" || histIdx >= 0)) {
      e.preventDefault();
      const next = Math.min(histIdx + 1, hist.length - 1);
      setHistIdx(next); setText(hist[next]);
    } else if (e.key === "ArrowDown" && histIdx >= 0) {
      e.preventDefault();
      const next = histIdx - 1;
      setHistIdx(next); setText(next < 0 ? "" : hist[next]);
    }
  }

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900 p-2 focus-within:border-accent">
      <textarea
        ref={ref}
        value={text}
        rows={2}
        disabled={working}
        onChange={(e) => { setText(e.target.value); setHistIdx(-1); }}
        onKeyDown={onKeyDown}
        placeholder={working ? "the agent is working…" : (placeholder ?? "Describe the next step…")}
        className="w-full resize-none bg-transparent px-2 py-1 text-sm text-mist-100 outline-none placeholder:text-mist-400 disabled:opacity-50"
      />
      <div className="flex items-center justify-between gap-2 px-1 pt-1">
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-mist-400">
            {working ? "one turn at a time — it'll hand back when done" : "Enter to send · ↑ history"}
          </span>
          {allowPlanFirst && (
            <label className="flex cursor-pointer items-center gap-1 text-[11px] text-mist-300">
              <input type="checkbox" checked={planFirst} onChange={(e) => setPlanFirst(e.target.checked)}
                className="accent-current text-accent" />
              plan first
            </label>
          )}
        </div>
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
