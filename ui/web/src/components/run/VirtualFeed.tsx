// Fix A / Phase 1 — the memory-safe, virtualized event feed. The buffer is
// capped at MAX_FEED_ITEMS upstream; here we render ONLY the rows in (and just
// around) the viewport, so DOM node count is constant no matter how many events
// stream in. Rows are FIXED HEIGHT (ROW_PX) — the invariant that maps a scroll
// offset directly to a slice without measuring. No react-window dependency.
//
// Phase 1 additions, all preserving the windowing invariant:
//   • a MINIMAP strip (click a step to jump the feed there) — 1.4
//   • reasoning rows render de-emphasized with a subtle left border — 1.2
//   • rows carrying a body (file diffs, code) are clickable → an expandable
//     DETAIL panel below the list renders the diff/code (1.3) without breaking
//     fixed-height windowing
//   • LIVE settles into REVIEW the instant the run completes: auto-follow stops
//     and an affordance appears (1.5)
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FeedItem, Tone, FlowState } from "../../lib/feed";
import { Artifact } from "./CodeBlock";
import { Minimap } from "./Minimap";

const ROW_PX = 30;        // fixed row height (must match the row's rendered height)
const OVERSCAN = 8;       // rows rendered above/below the viewport

const TONE: Record<Tone, string> = {
  info: "text-mist-200",
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  accent: "text-accent",
  muted: "text-mist-400",
};

export function VirtualFeed({ items, live, flow, activeStep }:
  { items: FeedItem[]; live: boolean; flow?: FlowState; activeStep?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(400);
  const [stick, setStick] = useState(true);   // glued to the tail?
  const [selected, setSelected] = useState<FeedItem | null>(null);
  const wasLive = useRef(live);

  // measure the viewport (and re-measure on resize)
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setHeight(el.clientHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // auto-scroll to the tail when stuck and new items arrive
  useEffect(() => {
    const el = ref.current;
    if (el && stick) el.scrollTop = items.length * ROW_PX;
  }, [items.length, stick]);

  // LIVE → REVIEW: when the stream goes quiet/complete, stop following so the
  // view settles calmly where the run ended rather than yanking to the tail.
  useEffect(() => {
    if (wasLive.current && !live) setStick(false);
    wasLive.current = live;
  }, [live]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < ROW_PX * 1.5;
    setStick(atBottom);
  };

  // jump the feed to the first row belonging to a step (minimap click)
  const jumpToStep = (step: number) => {
    const idx = items.findIndex((it) => it.step === step);
    const el = ref.current;
    if (idx >= 0 && el) { el.scrollTop = idx * ROW_PX; setStick(false); }
  };

  const total = items.length;
  const first = Math.max(0, Math.floor(scrollTop / ROW_PX) - OVERSCAN);
  const visibleCount = Math.ceil(height / ROW_PX) + OVERSCAN * 2;
  const last = Math.min(total, first + visibleCount);
  const slice = items.slice(first, last);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {flow && flow.steps.length > 0 && (
        <Minimap flow={flow} onJump={jumpToStep} activeStep={activeStep} />
      )}

      <div className="relative min-h-0 flex-1">
        <div
          ref={ref}
          onScroll={onScroll}
          role="log"
          aria-live="polite"
          aria-label="run event feed"
          className="h-full overflow-y-auto rounded-lg border border-ink-800 bg-ink-950/40 font-mono text-xs"
        >
          {total === 0 ? (
            <div className="flex h-full items-center justify-center text-mist-500">
              {live ? "waiting for the first event…" : "no events"}
            </div>
          ) : (
            <div style={{ height: total * ROW_PX, position: "relative" }}>
              {slice.map((it, i) => (
                <Row
                  key={it.id}
                  item={it}
                  top={(first + i) * ROW_PX}
                  selected={selected?.id === it.id}
                  onSelect={it.body ? () => setSelected((p) => (p?.id === it.id ? null : it)) : undefined}
                />
              ))}
            </div>
          )}
        </div>

        {!stick && total > 0 && (
          <button
            type="button"
            onClick={() => { const el = ref.current; if (el) { el.scrollTop = total * ROW_PX; setStick(true); } }}
            className="absolute bottom-3 right-3 rounded-full border border-ink-700 bg-ink-850 px-3 py-1 text-[11px] text-mist-200 shadow-lg transition-colors hover:bg-ink-800"
          >
            {live ? "↓ follow live" : "↓ jump to end"}
          </button>
        )}
      </div>

      {/* expandable detail — diff / code for the selected artifact (1.3) */}
      {selected?.body && (
        <div className="max-h-[40%] shrink-0 overflow-auto rounded-lg border border-ink-800 bg-ink-900">
          <div className="flex items-center justify-between border-b border-ink-800 px-3 py-1.5">
            <span className="truncate font-mono text-[11px] text-mist-200">{selected.title}</span>
            <button type="button" onClick={() => setSelected(null)}
              className="rounded px-1.5 text-mist-400 transition-colors hover:text-mist-100" aria-label="close detail">✕</button>
          </div>
          <div className="p-2">
            <Artifact text={selected.body} />
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ item, top, selected, onSelect }:
  { item: FeedItem; top: number; selected?: boolean; onSelect?: () => void }) {
  const reasoning = item.kind === "reasoning";
  return (
    <div
      style={{ position: "absolute", top, height: ROW_PX, left: 0, right: 0 }}
      className={`flex items-center gap-2 px-3 ${reasoning ? "border-l-2 border-ink-700 pl-2 italic opacity-80" : ""} ${onSelect ? "cursor-pointer hover:bg-ink-900" : ""} ${selected ? "bg-ink-900" : ""}`}
      title={item.body ? "click to view diff/code" : item.detail || item.title}
      onClick={onSelect}
    >
      <span className={`w-3 shrink-0 text-center ${TONE[item.tone]}`}>{item.icon}</span>
      {item.hms && <span className="shrink-0 text-mist-600">{item.hms}</span>}
      <span className={`shrink-0 truncate ${TONE[item.tone]} max-w-[40%]`}>{item.title}</span>
      {item.detail && <span className="truncate text-mist-500">{item.detail}</span>}
      {item.body && <span className="shrink-0 text-[10px] text-accent">⌄ diff</span>}
      {item.repeat > 1 && (
        <span className="shrink-0 rounded bg-ink-800 px-1 text-[10px] text-mist-400">×{item.repeat}</span>
      )}
      <span className="ml-auto shrink-0 text-mist-600">{item.meta}</span>
    </div>
  );
}
