// EventSource SSE consumer with auto-reconnect (EventSource reconnects natively;
// the backend sends `retry: 3000`). Surfaces a typed onEvent + connection state.
import { api } from "./api";
import type { EventType } from "../types";

const EVENT_TYPES: EventType[] = [
  "token", "phase", "plan", "plan_item", "tool_call", "file_op", "shell",
  "gate", "checkpoint", "escalation", "cost", "narration", "heartbeat", "span",
  "conductor", "gen.token", "gen.reasoning", "gen.thinking",
];

// Memory/robustness limit (Fix D): a backend that is down should not let the browser
// spin forever reopening a socket. After this many consecutive failed reconnects we
// give up and surface a terminal "reconnecting" so the UI can stop showing a live dot.
const MAX_SSE_RECONNECTS = 10;

export type ConnState = "connecting" | "live" | "reconnecting" | "lost";

export interface EventStream {
  close: () => void;
}

export function openStream(
  runId: string,
  onEvent: (type: EventType, data: any) => void,
  onConn: (state: ConnState) => void,
): EventStream {
  let closed = false;
  let reconnects = 0;
  onConn("connecting");
  const es = new EventSource(api.eventsUrl(runId));

  es.onopen = () => { if (!closed) { reconnects = 0; onConn("live"); } };
  es.onerror = () => {
    if (closed) return;
    reconnects += 1;
    // EventSource auto-retries; only intervene once we've exhausted the budget.
    if (reconnects >= MAX_SSE_RECONNECTS) {
      closed = true;
      es.close();
      onConn("lost");
    } else {
      onConn("reconnecting");
    }
  };

  for (const t of EVENT_TYPES) {
    es.addEventListener(t, (ev: MessageEvent) => {
      if (closed) return;
      try {
        onEvent(t, JSON.parse(ev.data));
      } catch {
        /* ignore malformed frame */
      }
    });
  }

  return {
    close: () => {
      closed = true;
      es.close();
    },
  };
}
