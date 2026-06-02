// Fix A — the memory-safe, virtualized event feed. The feed buffer is capped at
// MAX_FEED_ITEMS upstream; here we render ONLY the rows in (and just around) the
// viewport, so the DOM node count is constant (~visible+overscan) no matter how many
// events stream in. Rows are FIXED HEIGHT (ROW_PX) — the one invariant that lets a
// scroll offset map directly to a slice without measuring. Auto-scroll sticks to the
// tail unless the user scrolls up (then a "jump to latest" affordance appears). No
// react-window dependency: ~one screen of self-contained windowing, consistent with
// the rest of this UI (GraphLens is hand-rolled too).
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FeedItem, Tone } from "../../lib/feed";

const ROW_PX = 30;        // fixed row height (must match the row's rendered height)
const OVERSCAN = 8;       // rows rendered above/below the viewport for smooth scroll

const TONE: Record<Tone, string> = {
  info: "text-mist-200",
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  accent: "text-accent",
  muted: "text-mist-400",
};

export function VirtualFeed({ items, live }: { items: FeedItem[]; live: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(400);
  const [stick, setStick] = useState(true);   // glued to the tail?

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

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    // within ~1.5 rows of the bottom ⇒ re-stick; otherwise the user took control
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < ROW_PX * 1.5;
    setStick(atBottom);
  };

  const total = items.length;
  const first = Math.max(0, Math.floor(scrollTop / ROW_PX) - OVERSCAN);
  const visibleCount = Math.ceil(height / ROW_PX) + OVERSCAN * 2;
  const last = Math.min(total, first + visibleCount);
  const slice = items.slice(first, last);

  return (
    <div className="relative h-full min-h-0">
      <div
        ref={ref}
        onScroll={onScroll}
        className="h-full overflow-y-auto rounded-lg border border-ink-800 bg-ink-950/40 font-mono text-xs"
      >
        {total === 0 ? (
          <div className="flex h-full items-center justify-center text-mist-500">
            {live ? "waiting for the first event…" : "no events"}
          </div>
        ) : (
          // a single tall spacer establishes the scrollbar; the slice is absolutely
          // positioned at its true offset — classic windowing, O(visible) DOM nodes.
          <div style={{ height: total * ROW_PX, position: "relative" }}>
            {slice.map((it, i) => (
              <Row key={it.id} item={it} top={(first + i) * ROW_PX} />
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
          ↓ jump to latest
        </button>
      )}
    </div>
  );
}

function Row({ item, top }: { item: FeedItem; top: number }) {
  return (
    <div
      style={{ position: "absolute", top, height: ROW_PX, left: 0, right: 0 }}
      className="flex items-center gap-2 px-3"
      title={item.detail || item.title}
    >
      <span className={`w-3 shrink-0 text-center ${TONE[item.tone]}`}>{item.icon}</span>
      {item.hms && <span className="shrink-0 text-mist-600">{item.hms}</span>}
      <span className={`shrink-0 truncate ${TONE[item.tone]} max-w-[40%]`}>{item.title}</span>
      {item.detail && <span className="truncate text-mist-500">{item.detail}</span>}
      {item.repeat > 1 && (
        <span className="shrink-0 rounded bg-ink-800 px-1 text-[10px] text-mist-400">×{item.repeat}</span>
      )}
      <span className="ml-auto shrink-0 text-mist-600">{item.meta}</span>
    </div>
  );
}
