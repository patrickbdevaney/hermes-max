// Phase 3.2 — the run view, rendered NATIVELY in the shell (same origin as the
// chrome, no iframe) from the Phase 1 Channel stream through the SHARED feed.ts
// reducer. These are the very same components the standalone web UI uses
// (imported via @webui, not forked), so the two surfaces cannot diverge — and
// Cmd-K / shortcuts now work everywhere because there's no iframe focus trap.
import { useState } from "react";
import type { FeedState } from "@webui/lib/feed";
import { VirtualFeed } from "@webui/components/run/VirtualFeed";
import { ConductorSwimlane } from "@webui/components/run/ConductorSwimlane";
import { FlowGraph } from "@webui/components/run/FlowGraph";
import { MemoryView } from "@webui/components/run/MemoryView";

type Tab = "feed" | "conductor" | "flow" | "memory";
const TABS: Tab[] = ["feed", "conductor", "flow", "memory"];

export function RunView({ feed, live }: { feed: FeedState; live: boolean }) {
  const [tab, setTab] = useState<Tab>("feed");
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 px-3 pt-2">
        <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
          {TABS.map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)}
              className={`rounded px-2.5 py-1 capitalize transition-colors ${
                tab === t ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>
              {t}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 px-3 pb-3 pt-2">
        {tab === "feed" && <VirtualFeed items={feed.items} live={live} flow={feed.flow} activeStep={feed.flow.current} />}
        {tab === "conductor" && <ConductorSwimlane flow={feed.flow} live={live} />}
        {tab === "flow" && <FlowGraph flow={feed.flow} live={live} />}
        {tab === "memory" && <MemoryView flow={feed.flow} turns={feed.chrome.turns} />}
      </div>
    </div>
  );
}
