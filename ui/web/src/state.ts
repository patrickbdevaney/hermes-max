// Fold the SSE event stream into reduced view state. The conversation is a list of
// TURNS (PART IV): a user message → the agent's working flow → an explicit handback.
// Each turn owns its own timeline/plan/narration; cost and the raw OTLP span tree
// are run-global (they accumulate across the whole conversation). Pure + framework-
// agnostic so it's trivially testable and reusable in Tauri.
import type {
  EventType, TimelineEntry, PlanItem, ToolCallEvt, CostEvt, NarrationEvt,
  EscalationEvt, GateEvt, CheckpointEvt, HeartbeatEvt, PhaseEvt, PlanEvt,
  PlanItemEvt, FileOpEvt, ShellEvt, Span, SpanEvt, TokenEvt,
} from "./types";

// One conversational turn: the user's message, then everything the agent did until
// it handed back. `userText` is null for an attached/first turn with no prompt.
export interface Turn {
  id: number;
  userText: string | null;
  status: "working" | "done";
  phase: string;
  plan: PlanItem[] | null;
  narration: { text: string; level: "info" | "warn" } | null;
  narrationLog: { text: string; level: "info" | "warn"; ts: number }[];
  entries: TimelineEntry[];
  toolIndex: Record<string, number>;
  streamIndex: Record<string, number>;
  startedSteps: number;
  completedSteps: number;
  handback: string | null;   // the "your turn" line, set on phase:done
}

export interface RunView {
  turns: Turn[];
  // run-global
  cost: { total_usd: number; free: boolean; free_tok: number; paid_tok: number };
  spans: Record<string, Span>;
  spanOrder: string[];
  childIndex: Record<string, string[]>;   // parent_span_id → [span_id]
  lastEventTs: number;     // Date.now() of last event — drives the calm "alive" pulse
}

function newTurn(id: number, userText: string | null): Turn {
  return {
    id, userText, status: "working", phase: "connected", plan: null,
    narration: null, narrationLog: [], entries: [], toolIndex: {},
    streamIndex: {}, startedSteps: 0, completedSteps: 0, handback: null,
  };
}

export const initialView: RunView = {
  turns: [],
  cost: { total_usd: 0, free: true, free_tok: 0, paid_tok: 0 },
  spans: {},
  spanOrder: [],
  childIndex: {},
  lastEventTs: 0,
};

const MAX_ENTRIES = 600;

function clamp(entries: TimelineEntry[]): TimelineEntry[] {
  return entries.length > MAX_ENTRIES ? entries.slice(entries.length - MAX_ENTRIES) : entries;
}

export type Action =
  | { type: "event"; evt: EventType; data: any }
  | { type: "user_message"; text: string }
  | { type: "reset"; userText?: string | null };

export function reduce(state: RunView, action: Action): RunView {
  if (action.type === "reset") {
    return { ...initialView, turns: [newTurn(0, action.userText ?? null)] };
  }
  if (action.type === "user_message") {
    // A fresh turn: the prior turn is implicitly closed (it reached handback or the
    // user moved on). New turns start with their own empty flow; spans/cost persist.
    const id = state.turns.length;
    return { ...state, turns: [...state.turns, newTurn(id, action.text)], lastEventTs: Date.now() };
  }

  const { evt, data } = action;
  const now = Date.now();
  const v: RunView = { ...state, lastEventTs: now };

  // run-global events
  if (evt === "cost") {
    const d = data as CostEvt;
    v.cost = {
      total_usd: d.total_usd, free: d.free,
      free_tok: d.free_tok ?? state.cost.free_tok, paid_tok: d.paid_tok ?? state.cost.paid_tok,
    };
    return v;
  }
  if (evt === "span") {
    return reduceSpan(v, (data as SpanEvt).span);
  }

  // turn-scoped events → apply to the current (last) turn
  if (state.turns.length === 0) {
    // No turn yet (events before reset) — seed an anonymous turn so nothing is lost.
    v.turns = [newTurn(0, null)];
  } else {
    v.turns = [...state.turns];
  }
  const i = v.turns.length - 1;
  v.turns[i] = reduceTurn(v.turns[i], evt, data, now);
  return v;
}

function reduceTurn(turn: Turn, evt: EventType, data: any, now: number): Turn {
  const t: Turn = { ...turn };
  switch (evt) {
    case "phase": {
      const d = data as PhaseEvt;
      t.phase = d.phase;
      if (d.phase === "done") {
        t.status = "done";
        t.handback = t.handback || (t.narration?.text ?? "Done — your turn.");
      }
      return t;
    }
    case "plan": {
      t.plan = (data as PlanEvt).items;
      return t;
    }
    case "plan_item": {
      const d = data as PlanItemEvt;
      if (t.plan) t.plan = t.plan.map((p) => (p.id === d.id ? { ...p, status: d.status } : p));
      return t;
    }
    case "tool_call":
      return reduceTool(t, data as ToolCallEvt);
    case "heartbeat":
      return reduceHeartbeat(t, data as HeartbeatEvt);
    case "escalation": {
      const d = data as EscalationEvt;
      t.entries = clamp([...t.entries, {
        key: `esc-${now}-${t.entries.length}`, kind: "escalation",
        title: `${d.from_rung || "rung"} → ${d.to_rung || "next"}`,
        subtitle: d.reason, status: "info", detail: d.reason, hms: d.hms, ts: d.ts,
      }]);
      return t;
    }
    case "gate": {
      const d = data as GateEvt;
      t.entries = clamp([...t.entries, {
        key: `gate-${now}-${t.entries.length}`, kind: "gate",
        title: `gate: ${d.kind}`, status: d.status, subtitle: d.detail,
        detail: d.detail, hms: d.hms, ts: d.ts,
      }]);
      return t;
    }
    case "checkpoint": {
      const d = data as CheckpointEvt;
      t.entries = clamp([...t.entries, {
        key: `ckpt-${now}-${t.entries.length}`, kind: "checkpoint",
        title: d.label || "checkpoint", subtitle: d.commit, status: "pass",
        detail: d.commit, hms: d.hms, ts: d.ts,
      }]);
      return t;
    }
    case "file_op": {
      const d = data as FileOpEvt;
      t.entries = clamp([...t.entries, {
        key: `file-${now}-${t.entries.length}`, kind: "fileop",
        title: `${d.op}: ${d.path}`, status: "info", detail: d.diff_summary,
        hms: d.hms, ts: d.ts,
      }]);
      return t;
    }
    case "shell": {
      const d = data as ShellEvt;
      t.entries = clamp([...t.entries, {
        key: `sh-${now}-${t.entries.length}`, kind: "shell",
        title: d.cmd, status: d.exit_code === 0 || d.exit_code == null ? "info" : "fail",
        detail: d.stream_chunk, hms: d.hms, ts: d.ts,
      }]);
      return t;
    }
    case "narration": {
      const d = data as NarrationEvt;
      const n = { text: d.plain_text, level: (d.level === "warn" ? "warn" : "info") as "info" | "warn" };
      t.narration = n;
      t.narrationLog = [...t.narrationLog, { ...n, ts: now }].slice(-40);
      return t;
    }
    case "token":
      return reduceToken(t, data as TokenEvt);
    default:
      return t;
  }
}

function reduceSpan(state: RunView, sp: Span): RunView {
  if (!sp || !sp.span_id) return state;
  const s = state;
  const exists = !!s.spans[sp.span_id];
  s.spans = { ...s.spans, [sp.span_id]: sp };
  if (!exists) {
    s.spanOrder = [...s.spanOrder, sp.span_id].slice(-3000);
    const parent = sp.parent_span_id || "";
    s.childIndex = { ...s.childIndex, [parent]: [...(s.childIndex[parent] || []), sp.span_id] };
  }
  return s;
}

function reduceToken(turn: Turn, d: TokenEvt): Turn {
  // Token-by-token streaming: each span_id's text accumulates into one in-place
  // "generating" card so tool calls between tokens interleave by insertion order.
  const t = turn;
  const key = `stream-${d.span_id || "main"}`;
  const idx = t.streamIndex[key];
  const entries = [...t.entries];
  if (idx != null && entries[idx]) {
    entries[idx] = { ...entries[idx], detail: (entries[idx].detail || "") + (d.text || "") };
  } else {
    entries.push({ key, kind: "stream", title: "generating", status: "running", detail: d.text || "" });
    t.streamIndex = { ...t.streamIndex, [key]: entries.length - 1 };
  }
  t.entries = clamp(entries);
  return t;
}

function reduceTool(turn: Turn, d: ToolCallEvt): Turn {
  const t = turn;
  const idKey = d.call_id || `${d.tool}#anon`;

  if (d.status === "running") {
    const entry: TimelineEntry = {
      key: `tool-${idKey}-${t.entries.length}`, kind: "tool",
      title: d.tool, subtitle: d.server || undefined, status: "running",
      server: d.server, detail: d.input_summary, hms: d.hms, ts: d.ts,
    };
    const entries = clamp([...t.entries, entry]);
    return {
      ...t, entries,
      toolIndex: { ...t.toolIndex, [idKey]: entries.length - 1 },
      startedSteps: t.startedSteps + 1,
    };
  }

  // ok / fail / slow → update the matching running card if we have it.
  const idx = t.toolIndex[idKey];
  const entries = [...t.entries];
  const patch = (e: TimelineEntry): TimelineEntry => ({
    ...e,
    status: d.status,
    latency_ms: d.latency_ms ?? e.latency_ms,
    detail: [e.detail, d.result_summary, d.reason, d.note].filter(Boolean).join("  ·  ") || e.detail,
  });

  if (idx != null && entries[idx]) {
    entries[idx] = patch(entries[idx]);
  } else {
    entries.push(patch({
      key: `tool-${idKey}-${t.entries.length}`, kind: "tool",
      title: d.tool, subtitle: d.server || undefined, status: d.status,
      server: d.server, hms: d.hms, ts: d.ts,
    }));
  }
  const finished = d.status === "ok" || d.status === "fail";
  return { ...t, entries: clamp(entries), completedSteps: t.completedSteps + (finished ? 1 : 0) };
}

function reduceHeartbeat(turn: Turn, d: HeartbeatEvt): Turn {
  const t = turn;
  if (!d.tool || (d.done == null && d.total == null)) return t; // bare keep-alive
  const entries = [...t.entries];
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.kind === "tool" && e.status === "running" && e.title === d.tool) {
      entries[i] = { ...e, progress: { done: d.done ?? 0, total: d.total ?? 0, eta_s: d.eta_s, item: d.item } };
      break;
    }
  }
  return { ...t, entries };
}

// ── derived selectors (turn-scoped) ──
export function currentTurn(v: RunView): Turn | null {
  return v.turns.length ? v.turns[v.turns.length - 1] : null;
}

export function planProgress(t: Turn): { done: number; total: number } | null {
  if (!t.plan || t.plan.length === 0) return null;
  return { done: t.plan.filter((p) => p.status === "done").length, total: t.plan.length };
}

export function activePlanIndex(t: Turn): number {
  if (!t.plan) return -1;
  return t.plan.findIndex((p) => p.status !== "done");
}

// ── L2 span selectors (run-global) ──
export function rootSpans(v: RunView): Span[] {
  return v.spanOrder
    .map((id) => v.spans[id])
    .filter((sp): sp is Span => !!sp && (!sp.parent_span_id || !v.spans[sp.parent_span_id]));
}

export function childrenOf(v: RunView, spanId: string): Span[] {
  return (v.childIndex[spanId] || []).map((id) => v.spans[id]).filter(Boolean) as Span[];
}

export function spanCount(v: RunView): number {
  return v.spanOrder.length;
}

export function spansForEntry(v: RunView, entry: TimelineEntry): Span[] {
  if (entry.kind !== "tool" || v.spanOrder.length === 0) return [];
  const cands = v.spanOrder
    .map((id) => v.spans[id])
    .filter((sp): sp is Span => !!sp && (sp.name === entry.title || sp.attributes?.tool === entry.title));
  if (entry.ts) {
    const t = entry.ts * 1e9;
    cands.sort((a, b) => Math.abs((a.start_ns || 0) - t) - Math.abs((b.start_ns || 0) - t));
  }
  return cands.slice(0, 4);
}

// Deep-research fan-out within a turn: consecutive research/search/fetch rows are a
// breadth group; a following synthesis row is the convergence node.
const RESEARCH_RE = /research|search|fetch|crawl|source|rerank/i;
const SYNTH_RE = /synth|synthes|converge|summary|report/i;
export interface FanOut { sources: TimelineEntry[]; synthesis: TimelineEntry | null }
export function researchFanOut(t: Turn): FanOut | null {
  const tools = t.entries.filter((e) => e.kind === "tool");
  const sources = tools.filter((e) => RESEARCH_RE.test(e.title) && !SYNTH_RE.test(e.title));
  if (sources.length < 2) return null;
  const synthesis = tools.find((e) => SYNTH_RE.test(e.title)) || null;
  return { sources, synthesis };
}
