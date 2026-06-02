// The persistent left navigation (PART III.1) — always visible, current item
// highlighted, the five product surfaces reachable in one click. Links are real
// hash hrefs so Back/forward/bookmark all work and middle-click opens nothing
// surprising. The active mode is echoed at the foot as a calm, always-present cue.
import { Dot } from "./ui";
import { hrefFor } from "../lib/router";
import { modeInfo } from "../lib/modes";
import type { RouteName } from "../lib/router";
import type { StatusPayload } from "../types";

const SECTIONS: { title: string; items: { name: RouteName; label: string; hint: string }[] }[] = [
  { title: "Work", items: [
    { name: "run", label: "Run", hint: "talk to the agent · watch it work" },
    { name: "runs", label: "Runs", hint: "searchable history · replay" },
  ] },
  { title: "Insight", items: [
    { name: "cost", label: "Cost", hint: "the ledger · shadow meter" },
    { name: "fabric", label: "Fabric", hint: "models · providers · routing" },
    { name: "skills", label: "Skills", hint: "role activity" },
    { name: "services", label: "Services", hint: "MCP health" },
  ] },
  { title: "System", items: [
    { name: "providers", label: "Providers", hint: "rungs · keys" },
    { name: "config", label: "Config", hint: "mode · endpoints" },
    { name: "settings", label: "Settings", hint: "theme · sound · motion" },
    { name: "setup", label: "Setup", hint: "profile · mode · keys" },
  ] },
];

export function SideNav({ active, status, liveRuns = 0 }:
  { active: RouteName; status: StatusPayload | null; liveRuns?: number }) {
  const mode = modeInfo(status?.mode);
  return (
    <nav className="flex w-52 shrink-0 flex-col border-r border-ink-800 bg-ink-950 px-3 py-4">
      <a href={hrefFor("run")} className="mb-5 flex items-center gap-2 px-2">
        <Dot tone="accent" />
        <span className="text-sm font-semibold tracking-tight2 text-mist-100">hermes-max</span>
      </a>

      <div className="space-y-3 overflow-y-auto">
        {SECTIONS.map((sec) => (
          <ul key={sec.title} className="space-y-0.5">
            <li className="px-2.5 pb-0.5 text-[10px] uppercase tracking-wide text-mist-600">{sec.title}</li>
            {sec.items.map((it) => {
              const on = it.name === active || (it.name === "runs" && active === "replay");
              return (
                <li key={it.name}>
                  <a
                    href={hrefFor(it.name)}
                    aria-current={on ? "page" : undefined}
                    className={`block rounded-md px-2.5 py-2 text-sm transition-colors ${
                      on ? "bg-accent-soft/20 text-accent" : "text-mist-300 hover:bg-ink-850 hover:text-mist-100"}`}
                  >
                    <span className="flex items-center gap-2">
                      {on && <span className="h-3.5 w-0.5 rounded bg-accent" aria-hidden />}
                      <span className={on ? "" : "pl-2.5"}>{it.label}</span>
                      {it.name === "run" && liveRuns > 0 && (
                        <span className="ml-auto flex items-center gap-1 text-[10px] text-good">
                          <Dot tone="good" pulse />{liveRuns}
                        </span>
                      )}
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        ))}
      </div>

      <div className="mt-auto px-2 pt-4 text-[11px] text-mist-400">
        <button type="button"
          onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
          className="mb-2 flex w-full items-center gap-1.5 rounded-md border border-ink-800 px-2 py-1 text-left text-mist-400 hover:bg-ink-850">
          <span>Search &amp; commands</span>
          <span className="ml-auto font-mono text-[10px] text-mist-500">⌘K</span>
        </button>
        <div className="flex items-center gap-1.5">
          <span>mode</span>
          <span className="font-mono text-mist-300">{status?.mode ?? "—"}</span>
        </div>
        {mode && <div className="mt-0.5 leading-snug">{mode.blurb}</div>}
      </div>
    </nav>
  );
}
