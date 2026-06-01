// The persistent chrome (every view): identity, the active mode (a switcher
// popover), a status dot that RESOLVES to a named state (never a stuck spinner)
// with the provider rungs behind it, the detected DRIVER chip, and the always-
// present, calm live cost. Cost is never hidden; the driver is whatever config
// detects (local/remote/cloud) — never a hardcoded backend.
import { useState } from "react";
import { Dot, fmtUsd, fmtInt } from "./ui";
import { Popover } from "./Popover";
import { api } from "../lib/api";
import { MODES, modeInfo } from "../lib/modes";
import type { RunView } from "../state";
import type { StatusPayload, DriverStatus } from "../types";
import type { ConnState } from "../lib/events";

type Tone = "good" | "warn" | "bad" | "info" | "accent" | "muted";

function driverTone(d: DriverStatus | undefined): Tone {
  if (!d) return "muted";
  switch (d.state) {
    case "local": case "remote": return "good";
    case "cloud": return "info";
    default: return "bad";
  }
}

// The status dot resolves to a NAMED state from (driver + connection + whether a
// run is streaming). It never sits on a bare "connecting" with no resolution.
function resolveStatus(d: DriverStatus | undefined, conn: ConnState, hasRun: boolean):
  { tone: Tone; label: string } {
  if (!d || d.state === "none") return { tone: "bad", label: "no driver" };
  const driverWord = d.state === "cloud" ? `cloud · ${d.provider ?? "driver"}`
    : d.state === "remote" ? "remote driver" : "local driver";
  if (!hasRun) return { tone: driverTone(d), label: `ready · ${driverWord}` };
  if (conn === "live") return { tone: "good", label: `connected · ${driverWord}` };
  if (conn === "connecting") return { tone: "warn", label: "connecting…" };
  return { tone: "warn", label: "reconnecting…" };
}

export function TopChrome({ view, status, conn, alive, hasRun, refreshStatus }:
  {
    view: RunView; status: StatusPayload | null; conn: ConnState;
    alive: boolean; hasRun: boolean; refreshStatus: () => void;
  }) {
  const driver = status?.driver;
  const st = resolveStatus(driver, conn, hasRun);
  // Live cost when a run is streaming; otherwise today's ledger total (calm, always shown).
  const totalUsd = hasRun ? view.cost.total_usd : (status?.today_spend_usd ?? 0);
  const freeTok = view.cost.free_tok;
  const paidTok = view.cost.paid_tok;

  return (
    <header className="sticky top-0 z-20 border-b border-ink-800 bg-ink-950/90 backdrop-blur">
      <div className="flex items-center gap-3 px-5 py-2.5">
        <div className="flex items-center gap-2">
          <Dot tone="accent" pulse={alive} />
          <span className="text-sm font-semibold tracking-tight2 text-mist-100">hermes-max</span>
        </div>

        {/* mode switcher */}
        <ModeSwitcher mode={status?.mode} refreshStatus={refreshStatus} />

        {/* status dot → provider rungs popover */}
        <Popover
          width={340}
          trigger={() => (
            <span className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-mist-300 hover:bg-ink-850">
              <Dot tone={st.tone} pulse={hasRun && conn !== "live"} />
              <span className="hidden sm:inline">{st.label}</span>
            </span>
          )}
        >
          {() => <RungsPanel status={status} />}
        </Popover>

        <div className="ml-auto flex items-center gap-4">
          {/* the detected driver — never "no GPU"; a reachable endpoint IS a driver */}
          {driver && (
            <span className="hidden items-center gap-1.5 text-xs text-mist-400 md:flex">
              <Dot tone={driverTone(driver)} />
              <span>
                {driver.label}
                {driver.host && <span className="text-mist-300"> · {driver.host}</span>}
              </span>
            </span>
          )}

          {/* calm cost — tertiary weight, monospace, never an alarm */}
          <div className="text-right">
            <div className="font-mono text-sm font-medium tabular-nums text-mist-200">
              {fmtUsd(totalUsd)}
            </div>
            <div className="text-[11px] text-mist-400">
              {fmtInt(freeTok)} tok
              {paidTok > 0 ? <> · {fmtInt(paidTok)} paid</> : <span className="text-good"> · free</span>}
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}

function ModeSwitcher({ mode, refreshStatus }:
  { mode: string | undefined; refreshStatus: () => void }) {
  const [applying, setApplying] = useState<string | null>(null);
  const info = modeInfo(mode);

  async function pick(key: string, close: () => void) {
    if (key === mode) return close();
    setApplying(key);
    try { await api.setMode(key); refreshStatus(); }
    finally { setApplying(null); close(); }
  }

  return (
    <Popover
      width={300}
      align="left"
      trigger={() => (
        <span className="inline-flex items-center gap-1.5 rounded-md border border-accent/40 px-2.5 py-1 text-xs font-medium text-accent hover:bg-accent-soft/20">
          mode · {info?.title ?? mode ?? "—"} <span className="text-mist-400">▾</span>
        </span>
      )}
    >
      {(close) => (
        <div className="space-y-1">
          <div className="px-1.5 pb-1 text-[11px] uppercase tracking-wide text-mist-400">Posture</div>
          {MODES.map((m) => {
            const active = m.key === mode;
            return (
              <button
                key={m.key}
                type="button"
                disabled={applying != null}
                onClick={() => pick(m.key, close)}
                className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-ink-800 disabled:opacity-50 ${
                  active ? "bg-accent-soft/20" : ""}`}
              >
                <span className="mt-0.5">
                  <Dot tone={active ? "accent" : m.cost === "paid" ? "warn" : "muted"} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className={`text-sm ${active ? "text-accent" : "text-mist-100"}`}>{m.title}</span>
                    {active && <span className="text-[10px] text-accent">active</span>}
                    {applying === m.key && <span className="text-[10px] text-mist-400">applying…</span>}
                  </span>
                  <span className="block text-[11px] text-mist-400">{m.blurb}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </Popover>
  );
}

// Compact provider rungs behind the status dot. The full view lives on /providers.
function RungsPanel({ status }: { status: StatusPayload | null }) {
  if (!status) return <p className="px-2 py-1 text-xs text-mist-400">loading…</p>;
  const d = status.driver;
  return (
    <div className="space-y-2">
      {d && (
        <div className="rounded-md border border-ink-800 bg-ink-900 p-2">
          <div className="text-[11px] uppercase tracking-wide text-mist-400">Driver</div>
          <div className="mt-1 flex items-center gap-2 text-xs text-mist-200">
            <Dot tone={driverTone(d)} />
            <span>{d.label}{d.model ? <span className="text-mist-400"> · {d.model}</span> : null}</span>
          </div>
          {d.detail && <div className="mt-0.5 text-[11px] text-warn">{d.detail}</div>}
        </div>
      )}
      <div>
        <div className="px-1 text-[11px] uppercase tracking-wide text-mist-400">Providers</div>
        <ul className="mt-1 space-y-0.5">
          {status.providers.map((p) => {
            const rpd = status.free_rpd_remaining?.[p.name];
            return (
              <li key={p.name} className="flex items-center gap-2 px-1 py-0.5 text-xs">
                <Dot tone={p.present ? "good" : "muted"} />
                <span className="font-mono text-mist-200">{p.name}</span>
                <span className="ml-auto text-mist-400">
                  {p.present ? (rpd != null ? `${fmtInt(rpd)} rpd` : "ready") : "not set"}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
      <a href="#/providers" className="block rounded-md px-1 py-1 text-center text-xs text-accent hover:bg-ink-800">
        manage providers →
      </a>
    </div>
  );
}
