// Phase 6.1 — the Models/Providers (fabric) dashboard: which executor driver is
// up, provider reachability, and the routing Sankey (where tokens flow). Built
// from /api/status (driver + providers) and /api/cost (the cascade).
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Dot, SkeletonRows, fmtInt } from "./ui";
import { CascadeSankey } from "./CascadeSankey";
import type { StatusPayload, CostReport } from "../types";

export function FabricPage({ status }: { status: StatusPayload | null }) {
  const [cost, setCost] = useState<CostReport | null>(null);
  useEffect(() => { api.cost("today").then(setCost).catch(() => void 0); }, []);
  const driver = status?.driver;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Fabric</h1>
        <p className="mt-1 text-sm text-mist-400">Models &amp; providers — the cascade, who's up, where tokens route.</p>
      </header>

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <h2 className="mb-3 text-sm font-medium text-mist-200">Executor driver</h2>
        {driver ? (
          <div className="flex items-center gap-3">
            <Dot tone={driver.state === "none" ? "bad" : "good"} pulse={driver.state !== "none"} />
            <span className="text-sm text-mist-100">{driver.label}</span>
            {driver.model && <span className="font-mono text-xs text-mist-400">{driver.model}</span>}
            {driver.latency_ms != null && <span className="font-mono text-xs text-mist-500">{driver.latency_ms}ms</span>}
          </div>
        ) : <SkeletonRows rows={1} />}
      </section>

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <h2 className="mb-3 text-sm font-medium text-mist-200">Providers</h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {(status?.providers ?? []).map((p) => (
            <div key={p.name} className="flex items-center gap-2 rounded-md border border-ink-800 bg-ink-850 px-3 py-1.5">
              <Dot tone={p.reachable === false ? "bad" : p.present ? "good" : "muted"} />
              <span className="text-xs text-mist-200">{p.name}</span>
              <span className="ml-auto text-[10px] text-mist-500">{p.present ? "key" : "no key"}</span>
            </div>
          ))}
          {!status && <SkeletonRows rows={3} />}
        </div>
      </section>

      {cost && Object.keys(cost.by_provider ?? {}).length > 0 && (
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
          <h2 className="mb-3 text-sm font-medium text-mist-200">Routing (today)</h2>
          <CascadeSankey report={cost} />
          <p className="mt-2 text-[11px] text-mist-500">
            {fmtInt((cost.free_tok ?? 0) + (cost.paid_tok ?? 0))} tokens routed · {Object.keys(cost.by_provider).length} providers
          </p>
        </section>
      )}
    </div>
  );
}
