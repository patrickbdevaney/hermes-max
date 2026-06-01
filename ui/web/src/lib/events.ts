// EventSource SSE consumer with auto-reconnect (EventSource reconnects natively;
// the backend sends `retry: 3000`). Surfaces a typed onEvent + connection state.
import { api } from "./api";
import type { EventType } from "../types";

const EVENT_TYPES: EventType[] = [
  "token", "phase", "plan", "plan_item", "tool_call", "file_op", "shell",
  "gate", "checkpoint", "escalation", "cost", "narration", "heartbeat", "span",
];

export type ConnState = "connecting" | "live" | "reconnecting";

export interface EventStream {
  close: () => void;
}

export function openStream(
  runId: string,
  onEvent: (type: EventType, data: any) => void,
  onConn: (state: ConnState) => void,
): EventStream {
  let closed = false;
  onConn("connecting");
  const es = new EventSource(api.eventsUrl(runId));

  es.onopen = () => { if (!closed) onConn("live"); };
  es.onerror = () => {
    // EventSource will auto-retry; reflect that without tearing down.
    if (!closed) onConn("reconnecting");
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
