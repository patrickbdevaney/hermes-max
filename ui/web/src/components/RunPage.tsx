// PART IV — the conversational, full-actuation Run view. A vertical conversation of
// TURNS: the user's message → the agent's working flow (L0 glance + L1 timeline /
// optional graph lens + research fan-out + L2 on demand) → an explicit handback
// ("your turn"). The composer at the bottom ACTUATES the agent (launch, then
// continue the same conversation). When no run is active it is the launcher.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Badge, Dot } from "./ui";
import { Composer } from "./run/Composer";
import { GraphLens } from "./run/GraphLens";
import { L0Ambient } from "./L0Ambient";
import { Timeline } from "./Timeline";
import { ResearchFanOut, FullTrace } from "./L2Panels";
import { VirtualFeed } from "./run/VirtualFeed";
import { FlowGraph } from "./run/FlowGraph";
import { RunChrome } from "./run/RunChrome";
import type { RunView, Turn } from "../state";
import type { FeedState } from "../lib/feed";
import type { ConnState } from "../lib/events";
import type { StatusPayload, RecentProject } from "../types";

type Tab = "feed" | "flow" | "turns";
type TurnLens = "timeline" | "graph";
const TABS: { id: Tab; label: string }[] = [
  { id: "feed", label: "Feed" }, { id: "flow", label: "Flow" }, { id: "turns", label: "Turns" },
];

export function RunPage({ runId, view, feed, conn, status, onLaunch, onContinue, onNewRun }:
  {
    runId: string | null;
    view: RunView;
    feed: FeedState;
    conn: ConnState;
    status: StatusPayload | null;
    onLaunch: (cwd: string, prompt: string) => void;
    onContinue: (prompt: string) => void;
    onNewRun: () => void;
  }) {
  const [tab, setTab] = useState<Tab>("feed");
  const [lens, setLens] = useState<TurnLens>("timeline");

  if (!runId) {
    return <EmptyState status={status} onLaunch={onLaunch} />;
  }

  const lastTurn = view.turns[view.turns.length - 1];
  const working = !!lastTurn && lastTurn.status === "working";
  const live = conn === "live";
  // Deep-linked to a run the server no longer has: the stream can't open and nothing
  // streams. Tell the truth rather than spin forever.
  const replayLost = conn === "reconnecting" && view.lastEventTs === 0 && view.turns.every((t) => t.entries.length === 0);

  return (
    <div className="flex h-full flex-col">
      {/* run header: identity + tabs + (turns-only) lens toggle + new-run */}
      <div className="flex items-center justify-between border-b border-ink-800 px-1 pb-3">
        <div className="flex items-center gap-2 text-xs text-mist-400">
          <span>run</span>
          <span className="font-mono text-mist-200">{runId}</span>
          <span>·</span>
          <span>{view.turns.length} turn{view.turns.length === 1 ? "" : "s"}</span>
        </div>
        <div className="flex items-center gap-2">
          {tab === "turns" && (
            <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
              {(["timeline", "graph"] as TurnLens[]).map((m) => (
                <button key={m} type="button" onClick={() => setLens(m)}
                  className={`rounded px-2 py-1 capitalize transition-colors ${
                    lens === m ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>
                  {m}
                </button>
              ))}
            </div>
          )}
          <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
            {TABS.map((t) => (
              <button key={t.id} type="button" onClick={() => setTab(t.id)}
                className={`rounded px-2.5 py-1 transition-colors ${
                  tab === t.id ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>
                {t.label}
              </button>
            ))}
          </div>
          <button type="button" onClick={onNewRun}
            className="rounded-md border border-ink-700 px-2.5 py-1 text-xs text-mist-200 transition-colors hover:bg-ink-850">
            + new run
          </button>
        </div>
      </div>

      {replayLost && (
        <div className="mt-3 rounded-lg border border-warn/40 bg-warn-soft/20 px-3 py-2 text-xs text-warn">
          This run isn't available to replay — the server may have restarted since it ran.
          Start a new run below, or open one from Activity.
        </div>
      )}

      {/* persistent run chrome: step / turns / cost / tok-s — visible in every tab */}
      <div className="pt-3">
        <RunChrome chrome={feed.chrome} live={live} />
      </div>

      {/* the active view */}
      <div className="min-h-0 flex-1 py-3">
        {tab === "feed" && <VirtualFeed items={feed.items} live={live} />}
        {tab === "flow" && <FlowGraph flow={feed.flow} live={live} />}
        {tab === "turns" && (
          <div className="h-full space-y-5 overflow-y-auto pr-1">
            {view.turns.map((turn) => (
              <TurnBlock key={turn.id} turn={turn} view={view} mode={lens} />
            ))}
            <FullTrace view={view} />
          </div>
        )}
      </div>

      {/* the composer — actuates the agent */}
      <div className="border-t border-ink-800 pt-3">
        <Composer
          onSend={onContinue}
          working={working}
          autoFocus
          placeholder={working ? "the agent is working…" : "Describe the next step…"}
        />
      </div>
    </div>
  );
}

function TurnBlock({ turn, view, mode }: { turn: Turn; view: RunView; mode: TurnLens }) {
  const working = turn.status === "working";
  return (
    <div className="space-y-2">
      {turn.userText && (
        <div className="flex justify-end">
          <div className="max-w-[80%] rounded-lg rounded-br-sm border border-accent/30 bg-accent-soft/15 px-3 py-2 text-sm text-mist-100">
            {turn.userText}
          </div>
        </div>
      )}

      <div className="rounded-lg border border-ink-800 bg-ink-900">
        <div className="flex items-center gap-2 border-b border-ink-800 px-4 py-2">
          <Dot tone={working ? "accent" : "good"} pulse={working} />
          <span className="text-xs font-medium text-mist-200">hermes-max</span>
          <Badge tone={working ? "accent" : "good"}>{working ? "working" : "your turn"}</Badge>
        </div>

        <div className="space-y-3 p-4">
          <L0Ambient turn={turn} view={view} />
          <ResearchFanOut turn={turn} />
          {mode === "timeline"
            ? <Timeline turn={turn} view={view} />
            : <GraphLens turn={turn} />}

          {turn.handback && (
            <div className="flex items-center gap-2 rounded-lg border border-good/30 bg-good-soft/10 px-3 py-2 text-sm text-good">
              <Dot tone="good" /> {turn.handback}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// The launcher (no active run): pick a working directory, describe the task, go.
function EmptyState({ status, onLaunch }:
  { status: StatusPayload | null; onLaunch: (cwd: string, prompt: string) => void }) {
  const [recent, setRecent] = useState<RecentProject[]>([]);
  const [cwd, setCwd] = useState("");
  const [browseHint, setBrowseHint] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);

  useEffect(() => {
    api.recent().then((r) => {
      setRecent(r.projects);
      if (r.projects[0]) setCwd(r.projects[0].path);
    }).catch(() => void 0);
  }, []);

  async function browse() {
    setBrowseHint(null); setBrowsing(true);
    try {
      const r = await api.browseDir(cwd || undefined);
      if (r.path) setCwd(r.path);
      else if (r.error) setBrowseHint(r.hint || r.error);
    } catch (e) {
      setBrowseHint((e as Error).message);
    } finally {
      setBrowsing(false);
    }
  }

  const driver = status?.driver;
  const costy = status?.mode && !["free", "full-local", "local"].includes(status.mode);

  return (
    <div className="mx-auto flex h-full max-w-2xl flex-col justify-center">
      <div className="mb-6 text-center">
        <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">Describe a task to begin</h1>
        <p className="mt-2 text-sm text-mist-400">
          The agent plans, works step by step, and hands back to you — every action shown live.
        </p>
      </div>

      <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <label className="block text-xs text-mist-400">Working directory</label>
        <div className="mt-1 flex gap-2">
          <input
            value={cwd}
            onChange={(e) => setCwd(e.target.value)}
            placeholder="/path/to/project"
            className="flex-1 rounded-md border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-sm text-mist-100 outline-none focus:border-accent"
          />
          <button
            type="button"
            onClick={browse}
            disabled={browsing}
            title="Open the OS folder chooser"
            className="rounded-md border border-ink-700 px-3 py-2 text-xs text-mist-200 transition-colors hover:bg-ink-800 disabled:opacity-50"
          >
            {browsing ? "opening…" : "Browse…"}
          </button>
          {recent.length > 1 && (
            <select
              value={cwd}
              onChange={(e) => setCwd(e.target.value)}
              className="max-w-[32%] rounded-md border border-ink-700 bg-ink-950 px-2 py-2 text-xs text-mist-300 outline-none focus:border-accent"
            >
              {recent.map((p) => <option key={p.path} value={p.path}>{p.path}</option>)}
            </select>
          )}
        </div>
        {browseHint && <p className="mt-1 text-[11px] text-warn">{browseHint}</p>}

        <div className="mt-3">
          <Composer
            onSend={(prompt) => onLaunch(cwd, prompt)}
            working={false}
            autoFocus
            placeholder="e.g. Build a tested Python rate limiter with token-bucket and sliding-window…"
          />
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-mist-400">
          {driver && <Badge tone={driver.state === "none" ? "bad" : "good"}><Dot tone={driver.state === "none" ? "bad" : "good"} />{driver.label}</Badge>}
          <span>runs in <span className="font-mono text-mist-200">{status?.mode ?? "—"}</span> mode</span>
          <span>· {costy ? "cloud rungs may cost — the live total is always shown" : "typically $0 (free/local rungs)"}</span>
        </div>
      </div>
    </div>
  );
}
