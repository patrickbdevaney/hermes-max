// Phase 4.3 — shareable permalinks + replay/scrubbing. A past run (#/replay/:id)
// re-plays its translated events through the SAME pure reducer the live stream
// uses (lib/feed), so every Phase 1–3 view (chrome, feed, conductor swimlane,
// flow, shadow) renders identically — just driven by a scrubber instead of SSE.
// The append-only, offset-addressable JSONL makes this exact and cheap.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { navigate } from "../lib/router";
import { reduceFeed, initialFeed } from "../lib/feed";
import { RunChrome } from "./run/RunChrome";
import { VirtualFeed } from "./run/VirtualFeed";
import { ConductorSwimlane } from "./run/ConductorSwimlane";
import { FlowGraph } from "./run/FlowGraph";
import { RunReceipt } from "./run/RunReceipt";
import { SkeletonRows, ErrorState, EmptyMoment } from "./ui";
import type { HistoryDetail } from "../types";

type Tab = "feed" | "conductor" | "flow";
const SPEEDS = [1, 2, 4, 8];

export function ReplayPage({ runId }: { runId: string | null }) {
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pos, setPos] = useState(0);          // events applied [0..pos]
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [tab, setTab] = useState<Tab>("conductor");
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!runId) return;
    setDetail(null); setErr(null); setPos(0); setPlaying(false);
    api.historyRun(runId)
      .then((d) => { setDetail(d); setPos(d.events.length); })  // start fully settled
      .catch((e) => setErr((e as Error).message));
  }, [runId]);

  const total = detail?.events.length ?? 0;

  // playback: advance one event per tick (cadence ∝ 1/speed). Reduced-motion users
  // can still scrub manually; auto-play is an explicit opt-in.
  useEffect(() => {
    if (!playing || total === 0) return;
    timer.current = window.setInterval(() => {
      setPos((p) => { if (p >= total) { setPlaying(false); return p; } return p + 1; });
    }, Math.max(60, 360 / speed));
    return () => window.clearInterval(timer.current);
  }, [playing, speed, total]);

  const feed = useMemo(() => {
    if (!detail) return initialFeed;
    const evs = detail.events.slice(0, pos).map((e) => ({
      evt: e.event as any, data: e.data, now: e.ts ? e.ts * 1000 : e.seq,
    }));
    let s = reduceFeed(initialFeed, { type: "reset", userText: detail.summary.prompt ?? null });
    return reduceFeed(s, { type: "batch", events: evs });
  }, [detail, pos]);

  if (!runId) return <EmptyMoment icon="◇" title="No run selected" hint="Open a run from the Runs index to replay it." />;
  if (err) return <ErrorState title="Couldn't load this run" detail={err} onRetry={() => navigate("runs")} />;
  if (!detail) return <div className="pt-6"><SkeletonRows rows={8} /></div>;

  const cur = detail.events[Math.max(0, pos - 1)];
  const permalink = `${location.origin}${location.pathname}#/replay/${encodeURIComponent(runId)}`;

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between border-b border-ink-800 px-1 pb-3">
        <div className="flex min-w-0 items-center gap-2 text-xs text-mist-400">
          <span>replay</span>
          <span className="font-mono text-mist-200">{runId}</span>
          <span>·</span>
          <span className="truncate text-mist-300">{detail.summary.prompt || "(no prompt)"}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button type="button" onClick={() => navigator.clipboard?.writeText(permalink)}
            className="rounded-md border border-ink-700 px-2.5 py-1 text-xs text-mist-200 hover:bg-ink-850" title={permalink}>
            ⎘ copy link
          </button>
          <button type="button" onClick={() => navigate("runs")}
            className="rounded-md border border-ink-700 px-2.5 py-1 text-xs text-mist-200 hover:bg-ink-850">← runs</button>
        </div>
      </div>

      <RunChrome chrome={feed.chrome} live={false} conn="lost" />
      {pos >= total && <RunReceipt chrome={feed.chrome} conductorFires={feed.flow.conductors.length} runId={runId} />}

      <div className="flex items-center gap-2">
        <div className="flex rounded-md border border-ink-700 p-0.5 text-xs">
          {(["feed", "conductor", "flow"] as Tab[]).map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)}
              className={`rounded px-2.5 py-1 capitalize transition-colors ${tab === t ? "bg-accent-soft/30 text-accent" : "text-mist-400 hover:text-mist-200"}`}>
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1">
        {tab === "feed" && <VirtualFeed items={feed.items} live={false} flow={feed.flow} activeStep={feed.flow.current} />}
        {tab === "conductor" && <ConductorSwimlane flow={feed.flow} live={false} />}
        {tab === "flow" && <FlowGraph flow={feed.flow} live={false} />}
      </div>

      {/* the scrubber — scrub a past run like a video */}
      <div className="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-900 px-3 py-2">
        <button type="button" onClick={() => setPlaying((p) => !p)}
          className="w-8 shrink-0 rounded-md border border-ink-700 py-1 text-mist-100 hover:bg-ink-850" aria-label={playing ? "pause" : "play"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <input type="range" min={0} max={total} value={pos}
          onChange={(e) => { setPlaying(false); setPos(Number(e.target.value)); }}
          className="h-1 flex-1 accent-current text-accent" aria-label="scrub run" />
        <span className="w-24 shrink-0 text-right font-mono text-[11px] tabular-nums text-mist-400">
          {pos}/{total}{cur?.hms ? ` · ${cur.hms}` : ""}
        </span>
        <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}
          className="shrink-0 rounded-md border border-ink-700 bg-ink-input px-1.5 py-1 text-xs text-mist-300">
          {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
        </select>
      </div>
    </div>
  );
}
