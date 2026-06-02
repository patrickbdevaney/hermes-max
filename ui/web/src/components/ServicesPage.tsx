// Phase 6.1 — the MCP/Services dashboard. Health (real loopback TCP probe), port,
// latency for each server in the MCP port range. Auto-refreshes; colour + glyph +
// label, never colour alone.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Dot, SkeletonRows, ErrorState } from "./ui";
import type { ServicesPayload } from "../types";

export function ServicesPage() {
  const [data, setData] = useState<ServicesPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    const tick = () => api.services()
      .then((d) => { if (!stop) { setData(d); setErr(null); } })
      .catch((e) => { if (!stop) setErr((e as Error).message); });
    tick();
    const id = setInterval(tick, 5000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Services</h1>
          <p className="mt-1 text-sm text-mist-400">MCP servers — live loopback health.</p>
        </div>
        {data && (
          <span className="font-mono text-sm tabular-nums text-mist-300">
            <span className="text-good">{data.up}</span> / {data.total} up
          </span>
        )}
      </header>

      {err ? <ErrorState detail={err} />
        : !data ? <SkeletonRows rows={6} />
        : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {data.services.map((s) => (
              <div key={s.port} className="flex items-center justify-between rounded-lg border border-ink-800 bg-ink-900 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <Dot tone={s.open ? "good" : "muted"} pulse={s.open} />
                  <span className="font-mono text-sm text-mist-200">:{s.port}</span>
                  <span className={`text-xs ${s.open ? "text-good" : "text-mist-500"}`}>{s.open ? "up" : "down"}</span>
                </div>
                <span className="font-mono text-xs tabular-nums text-mist-400">
                  {s.latency_ms != null ? `${s.latency_ms}ms` : "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      <p className="text-[11px] text-mist-500">A TCP connect on 127.0.0.1 — "up" means the port is accepting connections.</p>
    </div>
  );
}
