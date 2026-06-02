// Phase 6.1 — the Skills surface. The ledger records spend BY ROLE, which is the
// honest proxy the fabric exposes for "which capabilities ran, how much"; we
// surface role activity (calls / tokens / cost) rather than fabricate per-skill
// invocation counts that aren't recorded yet.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Dot, fmtInt, fmtUsd, SkeletonRows, EmptyMoment } from "./ui";
import type { CostReport, CostBucket } from "../types";

export function SkillsPage() {
  const [rep, setRep] = useState<CostReport | null>(null);
  const [win, setWin] = useState("all");
  useEffect(() => { api.cost(win).then(setRep).catch(() => setRep(null)); }, [win]);

  const roles = Object.entries(rep?.by_role ?? {}).sort((a, b) => b[1].tok - a[1].tok);

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Skills</h1>
          <p className="mt-1 text-sm text-mist-400">Role-level activity from the ledger — who did the work, at what cost.</p>
        </div>
        <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
          {["today", "week", "all"].map((w) => (
            <button key={w} type="button" onClick={() => setWin(w)}
              className={`rounded px-2.5 py-1 capitalize transition-colors ${win === w ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>{w}</button>
          ))}
        </div>
      </header>

      {!rep ? <SkeletonRows rows={5} />
        : roles.length === 0
          ? <EmptyMoment icon="◇" title="No activity yet" hint="Role activity appears here once the fabric records calls." />
          : (
            <div className="space-y-2">
              {roles.map(([role, b]: [string, CostBucket]) => (
                <div key={role} className="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-900 px-3 py-2.5 text-xs">
                  <Dot tone={b.usd > 0 ? "warn" : "good"} />
                  <span className="font-mono text-mist-100">{role}</span>
                  <span className="ml-auto font-mono tabular-nums text-mist-400">{fmtInt(b.calls)} calls</span>
                  <span className="w-24 text-right font-mono tabular-nums text-mist-300">{fmtInt(b.tok)} tok</span>
                  <span className={`w-24 text-right font-mono tabular-nums ${b.usd > 0 ? "text-mist-200" : "text-good"}`}>{b.usd > 0 ? fmtUsd(b.usd) : "free"}</span>
                </div>
              ))}
            </div>
          )}
    </div>
  );
}
