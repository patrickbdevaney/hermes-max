// PART III.4 — the full Providers page: the driver, the provider rungs (status +
// live free-RPD headroom + role assignments), and key management. The compact
// version of this lives behind the status dot in the chrome.
import { Badge, Dot, fmtInt } from "./ui";
import { ProviderKeyList } from "./providers/KeyManager";
import type { StatusPayload, DriverStatus } from "../types";

type Tone = "good" | "warn" | "bad" | "info" | "muted" | "accent";

function driverTone(d: DriverStatus): Tone {
  switch (d.state) {
    case "local": case "remote": return "good";
    case "cloud": return "info";
    default: return "bad";
  }
}

export function ProvidersPage({ status, refreshStatus }:
  { status: StatusPayload | null; refreshStatus: () => void }) {
  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Providers</h1>
        <p className="mt-1 text-sm text-mist-400">The driver, the rungs, and your keys.</p>
      </header>

      {status?.driver && <DriverCard d={status.driver} />}

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <h2 className="text-sm font-medium text-mist-200">Rungs</h2>
        <ul className="mt-3 space-y-1.5">
          {status?.providers.map((p) => {
            const rpd = status.free_rpd_remaining?.[p.name];
            return (
              <li key={p.name} className="flex items-center gap-3 rounded-md border border-ink-800 bg-ink-850 px-3 py-2">
                <Dot tone={p.present ? "good" : "muted"} />
                <span className="font-mono text-sm text-mist-100">{p.name}</span>
                <span className="ml-auto text-xs text-mist-400">
                  {p.present
                    ? (rpd != null ? <>free · <span className="tabular-nums text-mist-300">{fmtInt(rpd)}</span> rpd left</> : "ready")
                    : "not configured"}
                </span>
                <Badge tone={p.present ? "good" : "muted"}>{p.present ? "ready" : "standby"}</Badge>
              </li>
            );
          }) ?? <li className="text-sm text-mist-400">loading…</li>}
        </ul>
      </section>

      {status?.roster?.length ? (
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
          <h2 className="text-sm font-medium text-mist-200">Role roster</h2>
          <ul className="mt-3 grid gap-1 sm:grid-cols-2">
            {status.roster.map((r) => (
              <li key={r.role} className="flex items-center justify-between gap-3 rounded-md border border-ink-800 bg-ink-850 px-3 py-1.5 text-xs">
                <span className="text-mist-400">{r.role}</span>
                <span className="font-mono text-mist-200">{r.rung}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <h2 className="mb-3 text-sm font-medium text-mist-200">Keys</h2>
        <ProviderKeyList onChange={refreshStatus} />
      </section>
    </div>
  );
}

function DriverCard({ d }: { d: DriverStatus }) {
  const tone = driverTone(d);
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <div className="text-xs uppercase tracking-wide text-mist-400">Your driver</div>
      <div className="mt-2 flex items-center gap-2">
        <Dot tone={tone} />
        <span className="text-base font-medium text-mist-100">{d.label}</span>
        {d.model && <span className="font-mono text-xs text-mist-400">· {d.model}</span>}
        {d.latency_ms != null && <span className="font-mono text-xs text-mist-400">· {d.latency_ms}ms</span>}
      </div>
      <p className="mt-1 text-sm text-mist-400">
        {d.state === "remote" && <>Your vLLM endpoint is live on another machine{d.host ? <> ({d.host})</> : null}. Local execution runs there.</>}
        {d.state === "local" && <>A local model executes the agent loop on this machine.</>}
        {d.state === "cloud" && <>A cloud model ({d.provider}) drives the agent loop.</>}
        {d.state === "none" && (d.detail || "No reachable driver — configure one in Setup.")}
      </p>
      {d.base_url && <div className="mt-1 font-mono text-[11px] text-mist-400">{d.base_url}</div>}
    </section>
  );
}
