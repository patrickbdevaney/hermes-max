// PART III.1 Cost surface — the ledger breakdown (window: today / week / month /
// all), by provider, model, and role, plus free-budget headroom. Cost reads as
// calm information (monospace, tertiary), never an alarm; the free portion is a
// gentle green.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Dot, fmtUsd, fmtInt } from "./ui";
import type { CostReport, CostBucket } from "../types";

const WINDOWS = ["today", "week", "month", "all"] as const;
type Window = typeof WINDOWS[number];

export function CostPage() {
  const [win, setWin] = useState<Window>("today");
  const [rep, setRep] = useState<CostReport | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    api.cost(win).then(setRep).catch(() => setRep(null)).finally(() => setLoading(false));
  }, [win]);

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Cost</h1>
          <p className="mt-1 text-sm text-mist-400">What the fabric spent — and how much stayed free.</p>
        </div>
        <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
          {WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => setWin(w)}
              className={`rounded px-2.5 py-1 capitalize transition-colors ${
                win === w ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}
            >
              {w}
            </button>
          ))}
        </div>
      </header>

      {/* headline numbers */}
      <section className="grid gap-3 sm:grid-cols-4">
        <Stat label="Total" value={fmtUsd(rep?.total_usd ?? 0)} mono tone={rep && rep.paid_tok > 0 ? "info" : "good"} />
        <Stat label="Calls" value={fmtInt(rep?.calls ?? 0)} mono />
        <Stat label="Free tokens" value={fmtInt(rep?.free_tok ?? 0)} mono tone="good" />
        <Stat label="Paid tokens" value={fmtInt(rep?.paid_tok ?? 0)} mono tone={rep && rep.paid_tok > 0 ? "warn" : "muted"} />
      </section>

      {loading && <p className="text-sm text-mist-400">loading…</p>}

      <BucketTable title="By provider" buckets={rep?.by_provider} />
      <BucketTable title="By model" buckets={rep?.by_model} />
      <BucketTable title="By role" buckets={rep?.by_role} />

      {rep && Object.keys(rep.free_budget_remaining ?? {}).length > 0 && (
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
          <h2 className="mb-3 text-sm font-medium text-mist-200">Free budget remaining</h2>
          <ul className="grid gap-1 sm:grid-cols-2">
            {Object.entries(rep.free_budget_remaining).map(([k, v]) => (
              <li key={k} className="flex items-center justify-between gap-3 rounded-md border border-ink-800 bg-ink-850 px-3 py-1.5 text-xs">
                <span className="font-mono text-mist-300">{k}</span>
                <span className="font-mono tabular-nums text-good">{v == null ? "∞" : fmtInt(v)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value, mono, tone = "info" }:
  { label: string; value: string; mono?: boolean; tone?: "good" | "warn" | "muted" | "info" }) {
  const color = tone === "good" ? "text-good" : tone === "warn" ? "text-warn"
    : tone === "muted" ? "text-mist-400" : "text-mist-100";
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <div className="text-xs uppercase tracking-wide text-mist-400">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${color} ${mono ? "font-mono" : ""}`}>{value}</div>
    </div>
  );
}

function BucketTable({ title, buckets }: { title: string; buckets?: Record<string, CostBucket> }) {
  const rows = Object.entries(buckets ?? {}).sort((a, b) => b[1].tok - a[1].tok);
  if (rows.length === 0) return null;
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <h2 className="mb-3 text-sm font-medium text-mist-200">{title}</h2>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-mist-400">
            <th className="pb-2 font-medium">name</th>
            <th className="pb-2 text-right font-medium">calls</th>
            <th className="pb-2 text-right font-medium">tokens</th>
            <th className="pb-2 text-right font-medium">cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, b]) => (
            <tr key={name} className="border-t border-ink-800">
              <td className="py-1.5 pr-2">
                <span className="flex items-center gap-2">
                  <Dot tone={b.usd > 0 ? "warn" : "good"} />
                  <span className="truncate font-mono text-mist-200">{name}</span>
                </span>
              </td>
              <td className="py-1.5 text-right font-mono tabular-nums text-mist-300">{fmtInt(b.calls)}</td>
              <td className="py-1.5 text-right font-mono tabular-nums text-mist-300">{fmtInt(b.tok)}</td>
              <td className={`py-1.5 text-right font-mono tabular-nums ${b.usd > 0 ? "text-mist-200" : "text-good"}`}>
                {b.usd > 0 ? fmtUsd(b.usd) : "free"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
