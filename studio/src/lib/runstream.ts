// The v2 run stream — Rust is the sole SSE consumer; it pushes coalesced frames
// over a Tauri Channel. This replaces lib/workshop.ts (the old emit-based tailer).
// The shell renders the studio bar from `chrome`; Phase 3 will also feed `events`
// through the shared feed.ts reducer for the native run view.
import { invoke, makeChannel } from "./tauri";

export interface Chrome {
  step: number;
  total: number;
  turns: number;
  cost_usd: number;
  tokens: number;
  running: boolean;
  done: boolean;
  phrase: string;
  model?: string | null;
  tier?: string | null;
}

export interface StreamMsg {
  tokens: string;            // concatenated token deltas since the last frame
  reasoning: string;         // concatenated reasoning deltas
  events: { event: string; data: any }[]; // structured events (verbatim, for Phase 3)
  chrome: Chrome;
  done: boolean;
}

/** Begin streaming a run over the Channel. `onMsg` fires ~50Hz while tokens flow. */
export function startRunStream(runId: string, onMsg: (m: StreamMsg) => void): Promise<unknown> {
  return invoke("start_run_stream", { runId, onEvent: makeChannel<StreamMsg>(onMsg) });
}

export const stopRunStream = () => invoke("stop_run_stream");
