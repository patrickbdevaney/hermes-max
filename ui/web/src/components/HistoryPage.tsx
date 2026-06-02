// Phase 4.2 — the Runs index. A dense, searchable, virtualized table of every
// indexed run (SQLite + FTS5 over the livelog). This is what a web UI does that a
// terminal cannot: persistent, searchable, shareable, replayable history. Rows
// deep-link to the replay view (4.3); two selected runs compare side-by-side.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { navigate } from "../lib/router";
import { Dot, fmtInt, EmptyMoment, SkeletonRows, ErrorState } from "./ui";
import { computeShadow, fmtMoney, fmtMultiple } from "../lib/shadow";
import type { HistoryRun } from "../types";

const ROW_PX = 44;
const OVERSCAN = 6;
type Sort = "recent" | "cost" | "savings" | "fires";

function tokensOf(r: HistoryRun): number { return (r.free_tok ?? 0) + (r.paid_tok ?? 0); }
function savingsOf(r: HistoryRun) { return computeShadow(r.cost_usd ?? 0, tokensOf(r)); }
function durOf(r: HistoryRun): string {
  if (!r.start_ts || !r.end_ts) return "—";
  const s = Math.max(0, r.end_ts - r.start_ts);
  return s < 60 ? `${s.toFixed(0)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function HistoryPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<Sort>("recent");
  const [rows, setRows] = useState<HistoryRun[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string[]>([]);

  // debounce the FTS query
  useEffect(() => {
    let stop = false;
    const t = setTimeout(() => {
      setRows(null); setErr(null);
      api.history(q, status)
        .then((r) => { if (!stop) setRows(r.runs); })
        .catch((e) => { if (!stop) setErr((e as Error).message); });
    }, 250);
    return () => { stop = true; clearTimeout(t); };
  }, [q, status]);

  const sorted = useMemo(() => {
    const rs = [...(rows ?? [])];
    if (sort === "cost") rs.sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0));
    else if (sort === "fires") rs.sort((a, b) => (b.conductor_fires ?? 0) - (a.conductor_fires ?? 0));
    else if (sort === "savings") rs.sort((a, b) => savingsOf(b).savedUsd - savingsOf(a).savedUsd);
    else rs.sort((a, b) => (b.start_ts ?? 0) - (a.start_ts ?? 0));
    return rs;
  }, [rows, sort]);

  const toggleSel = (id: string) =>
    setSel((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2));
  const compareRuns = sel.length === 2 ? sorted.filter((r) => sel.includes(r.run_id)) : [];

  return (
    <div className="flex h-full flex-col gap-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Runs</h1>
          <p className="mt-1 text-sm text-mist-400">Every run, searchable and replayable — what a terminal can't keep.</p>
        </div>
        <div className="flex items-center gap-2">
          <SortToggle sort={sort} setSort={setSort} />
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search prompts, tools, reasons… (full-text)"
          className="min-w-[260px] flex-1 rounded-md border border-ink-700 bg-ink-input px-3 py-2 text-sm text-mist-100 outline-none focus:border-accent"
        />
        <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
          {[["", "all"], ["running", "running"], ["exited", "done"]].map(([v, label]) => (
            <button key={v} type="button" onClick={() => setStatus(v)}
              className={`rounded px-2.5 py-1 capitalize transition-colors ${status === v ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {compareRuns.length === 2 && <CompareStrip a={compareRuns[0]} b={compareRuns[1]} onClear={() => setSel([])} />}

      <div className="min-h-0 flex-1">
        {err ? <ErrorState detail={err} onRetry={() => setQ((x) => x + "")} />
          : rows === null ? <SkeletonRows rows={8} />
          : sorted.length === 0
            ? <EmptyMoment icon="◇" title={q ? "No runs match" : "No runs indexed yet"}
                hint={q ? "Try a different search." : "Completed runs are indexed automatically and appear here for search and replay."} />
            : <RunTable rows={sorted} sel={sel} onToggle={toggleSel} onOpen={(id) => navigate("replay", id)} />}
      </div>
    </div>
  );
}

function SortToggle({ sort, setSort }: { sort: Sort; setSort: (s: Sort) => void }) {
  const opts: [Sort, string][] = [["recent", "recent"], ["cost", "cost"], ["savings", "saved"], ["fires", "fires"]];
  return (
    <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
      {opts.map(([v, label]) => (
        <button key={v} type="button" onClick={() => setSort(v)}
          className={`rounded px-2.5 py-1 transition-colors ${sort === v ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

// Windowed table — O(visible) DOM rows (virtualize > ~50, perf budget).
function RunTable({ rows, sel, onToggle, onOpen }:
  { rows: HistoryRun[]; sel: string[]; onToggle: (id: string) => void; onOpen: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(500);

  useEffect(() => {
    const el = ref.current; if (!el) return;
    const m = () => setHeight(el.clientHeight); m();
    const ro = new ResizeObserver(m); ro.observe(el); return () => ro.disconnect();
  }, []);

  const first = Math.max(0, Math.floor(scrollTop / ROW_PX) - OVERSCAN);
  const last = Math.min(rows.length, first + Math.ceil(height / ROW_PX) + OVERSCAN * 2);
  const slice = rows.slice(first, last);

  return (
    <div ref={ref} onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      className="h-full overflow-y-auto rounded-lg border border-ink-800 bg-ink-900">
      <div style={{ height: rows.length * ROW_PX, position: "relative" }}>
        {slice.map((r, i) => {
          const sv = savingsOf(r);
          const running = r.status === "running";
          return (
            <div key={r.run_id}
              style={{ position: "absolute", top: (first + i) * ROW_PX, height: ROW_PX, left: 0, right: 0 }}
              className={`flex items-center gap-3 border-b border-ink-800 px-3 text-xs hover:bg-ink-850 ${sel.includes(r.run_id) ? "bg-ink-850" : ""}`}>
              <input type="checkbox" checked={sel.includes(r.run_id)} onChange={() => onToggle(r.run_id)}
                aria-label="select for compare" className="accent-current text-accent" onClick={(e) => e.stopPropagation()} />
              <button type="button" onClick={() => onOpen(r.run_id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <span className="flex w-20 shrink-0 items-center gap-1.5">
                  <Dot tone={running ? "accent" : r.verify_fail ? "warn" : "good"} pulse={running} />
                  <span className="text-mist-400">{running ? "live" : "done"}</span>
                </span>
                <span className="min-w-0 flex-1 truncate text-mist-200">{r.prompt || <span className="text-mist-500">(no prompt)</span>}</span>
                <span className="w-16 shrink-0 text-right font-mono tabular-nums text-mist-400">{r.step_count ?? 0} st</span>
                <span className="w-14 shrink-0 text-right font-mono tabular-nums text-conductor" title="conductor fires">{fmtInt(r.conductor_fires)}⚡</span>
                <span className="w-20 shrink-0 text-right font-mono tabular-nums text-mist-300">{durOf(r)}</span>
                <span className={`w-20 shrink-0 text-right font-mono tabular-nums ${(r.cost_usd ?? 0) > 0 ? "text-warn" : "text-good"}`}>{fmtMoney(r.cost_usd ?? 0)}</span>
                <span className="w-16 shrink-0 text-right font-mono tabular-nums text-conductor" title="savings vs frontier">{fmtMultiple(sv.multiple)}</span>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CompareStrip({ a, b, onClear }: { a: HistoryRun; b: HistoryRun; onClear: () => void }) {
  const cols = [a, b];
  const rowsDef: [string, (r: HistoryRun) => string][] = [
    ["prompt", (r) => r.prompt || "—"],
    ["steps", (r) => String(r.step_count ?? 0)],
    ["turns", (r) => String(r.turn_count ?? 0)],
    ["conductor fires", (r) => String(r.conductor_fires ?? 0)],
    ["verify pass/fail", (r) => `${r.verify_pass ?? 0} / ${r.verify_fail ?? 0}`],
    ["cost", (r) => fmtMoney(r.cost_usd ?? 0)],
    ["saved", (r) => `${fmtMoney(savingsOf(r).savedUsd)} (${fmtMultiple(savingsOf(r).multiple)})`],
  ];
  return (
    <section className="rounded-lg border border-accent/30 bg-ink-900 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium text-mist-200">Compare</h2>
        <button type="button" onClick={onClear} className="text-xs text-mist-400 hover:text-mist-100">clear</button>
      </div>
      <div className="grid grid-cols-[140px_1fr_1fr] gap-x-3 gap-y-1 text-xs">
        {rowsDef.map(([label, get]) => (
          <div key={label} className="contents">
            <div className="text-[10px] uppercase tracking-wide text-mist-500">{label}</div>
            {cols.map((r, i) => <div key={i} className="truncate font-mono text-mist-200">{get(r)}</div>)}
          </div>
        ))}
      </div>
    </section>
  );
}
