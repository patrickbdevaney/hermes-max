// Phase 6.2 — the Cmd-K command palette, hand-rolled (cmdk is the Linear/Raycast
// pedigree pick, but a focused ~150-line palette holds the bundle budget and the
// house style). Registers navigation, run actions, and run-search. Linear-style
// single-key chords (g r → runs, g c → cost, …) and ? to open. Every action is
// keyboard-reachable; the overlay traps focus and Esc closes.
import { useEffect, useMemo, useRef, useState } from "react";
import { navigate, type RouteName } from "../lib/router";
import { api } from "../lib/api";

interface Cmd { id: string; label: string; section: string; hint?: string; run: () => void; }

const NAV: { key: string; name: RouteName; label: string }[] = [
  { key: "r", name: "run", label: "Run" },
  { key: "u", name: "runs", label: "Runs (history)" },
  { key: "c", name: "cost", label: "Cost" },
  { key: "p", name: "providers", label: "Providers" },
  { key: "f", name: "fabric", label: "Fabric (models/providers)" },
  { key: "s", name: "services", label: "Services (MCP)" },
  { key: "k", name: "skills", label: "Skills" },
  { key: "g", name: "settings", label: "Settings" },
];

function fuzzy(q: string, s: string): boolean {
  q = q.toLowerCase(); s = s.toLowerCase();
  if (!q) return true;
  let i = 0;
  for (const ch of s) if (ch === q[i]) i++;
  return i >= q.length;
}

const isTyping = () => {
  const el = document.activeElement;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || (el as HTMLElement).isContentEditable);
};

export function CommandPalette({ activeRunId, working, onNewRun, onInterrupt }:
  { activeRunId: string | null; working: boolean; onNewRun: () => void; onInterrupt: () => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [hits, setHits] = useState<{ id: string; label: string }[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  // global keys: Cmd/Ctrl+K toggles; ? opens; `g <key>` chord navigates.
  useEffect(() => {
    let gPending = false; let gTimer = 0;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault(); setOpen((o) => !o); return;
      }
      if (open || isTyping()) return;
      if (e.key === "?") { e.preventDefault(); setOpen(true); return; }
      if (e.key === "g") {
        gPending = true; window.clearTimeout(gTimer);
        gTimer = window.setTimeout(() => { gPending = false; }, 800);
        return;
      }
      if (gPending) {
        const nav = NAV.find((n) => n.key === e.key.toLowerCase());
        gPending = false;
        if (nav) { e.preventDefault(); navigate(nav.name); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); window.clearTimeout(gTimer); };
  }, [open]);

  useEffect(() => { if (open) { setQ(""); setSel(0); setTimeout(() => inputRef.current?.focus(), 0); } }, [open]);

  // debounced run-search (FTS) when there's a query
  useEffect(() => {
    if (!open || !q.trim()) { setHits([]); return; }
    let stop = false;
    const t = setTimeout(() => {
      api.history(q).then((r) => {
        if (!stop) setHits(r.runs.slice(0, 6).map((x) => ({ id: x.run_id, label: x.prompt || x.run_id })));
      }).catch(() => void 0);
    }, 200);
    return () => { stop = true; clearTimeout(t); };
  }, [q, open]);

  const cmds = useMemo<Cmd[]>(() => {
    const list: Cmd[] = NAV.map((n) => ({
      id: `nav-${n.name}`, label: n.label, section: "Go to", hint: `g ${n.key}`,
      run: () => navigate(n.name),
    }));
    list.push({ id: "new-run", label: "New run", section: "Actions", run: onNewRun });
    if (activeRunId && working) {
      list.push({ id: "interrupt", label: "Interrupt current turn", section: "Actions", run: onInterrupt });
    }
    return list;
  }, [activeRunId, working, onNewRun, onInterrupt]);

  const filtered = cmds.filter((c) => fuzzy(q, c.label));
  const searchCmds: Cmd[] = hits.map((h) => ({
    id: `run-${h.id}`, label: h.label, section: "Open run", run: () => navigate("replay", h.id),
  }));
  const all = [...filtered, ...searchCmds];

  function exec(i: number) {
    const c = all[i];
    if (c) { c.run(); setOpen(false); }
  }

  if (!open) return null;
  const sections = Array.from(new Set(all.map((c) => c.section)));

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/40 pt-[12vh]"
      onClick={() => setOpen(false)}>
      <div className="w-[560px] max-w-[92vw] overflow-hidden rounded-xl border border-ink-700 bg-ink-overlay shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
            else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, all.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
            else if (e.key === "Enter") { e.preventDefault(); exec(sel); }
          }}
          placeholder="Type a command or search runs…"
          className="w-full border-b border-ink-800 bg-transparent px-4 py-3 text-sm text-mist-100 outline-none placeholder:text-mist-500"
        />
        <div className="max-h-[50vh] overflow-y-auto py-1">
          {all.length === 0 && <div className="px-4 py-6 text-center text-sm text-mist-500">No matches</div>}
          {sections.map((sec) => (
            <div key={sec}>
              <div className="px-4 pb-1 pt-2 text-[10px] uppercase tracking-wide text-mist-500">{sec}</div>
              {all.map((c, i) => c.section === sec && (
                <button key={c.id} type="button"
                  onMouseEnter={() => setSel(i)} onClick={() => exec(i)}
                  className={`flex w-full items-center gap-2 px-4 py-2 text-left text-sm ${i === sel ? "bg-accent-soft/20 text-mist-100" : "text-mist-300"}`}>
                  <span className="truncate">{c.label}</span>
                  {c.hint && <span className="ml-auto font-mono text-[10px] text-mist-500">{c.hint}</span>}
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="border-t border-ink-800 px-4 py-1.5 text-[10px] text-mist-500">
          ↑↓ navigate · ↵ select · esc close · ⌘K toggle · g+key jumps
        </div>
      </div>
    </div>
  );
}
