// The Part-2 feed/flow/chrome model: a SINGLE pure reducer that folds the SSE event
// stream into three memory-bounded view states the world-class Run UI renders:
//   • items[]  — a flat, typed, virtualizable event feed (Fix A)
//   • flow     — a derived step/conductor graph (Fix B)
//   • chrome   — the persistent run HUD: step/turn/cost/tok-s (Fix C)
//
// Everything here is hard-capped (Fix D): the feed is a circular buffer of at most
// MAX_FEED_ITEMS, the flow keeps at most MAX_GRAPH_NODES nodes, and ingestion is
// applied in batches (App buffers raw SSE frames and flushes every BATCH_FLUSH_MS) so
// a fast run never thrashes React or grows the heap without bound. Pure + framework-
// agnostic (no React import) so it is trivially testable and reusable in Tauri.
import type {
  EventType, ToolCallEvt, FileOpEvt, GateEvt, CheckpointEvt, EscalationEvt,
  CostEvt, NarrationEvt, ShellEvt, PhaseEvt, ConductorEvt,
} from "../types";

// ── memory limits (Fix D) ──────────────────────────────────────────────────
export const MAX_FEED_ITEMS = 500;     // circular buffer; oldest dropped
export const MAX_GRAPH_NODES = 200;    // flow steps + conductor nodes ceiling
export const BATCH_FLUSH_MS = 100;     // SSE coalescing window (App-side)
export const MAX_SERIES = 64;          // sparkline sample ring (tok/s, cost-rate)

// ── the typed feed item (Fix A) ─────────────────────────────────────────────
export type FeedKind =
  | "user" | "llm" | "tool" | "file" | "verify" | "conductor" | "guidance"
  | "step" | "complete" | "narration" | "gate" | "checkpoint" | "escalation"
  | "cost" | "shell" | "phase" | "reasoning";

export type Tone = "info" | "good" | "warn" | "bad" | "accent" | "muted";

export interface FeedItem {
  id: number;            // monotonic, stable React key
  kind: FeedKind;
  tone: Tone;
  icon: string;          // single glyph rendered in the gutter
  title: string;
  detail?: string;
  meta?: string;         // right-aligned secondary (timing / model / tokens)
  body?: string;         // multi-line payload for the expandable detail (diff / code)
  step?: number;
  ts: number;            // ms
  hms?: string;
  repeat: number;        // collapsed consecutive-identical count (1 = unique)
}

// ── the derived flow graph (Fix B) ──────────────────────────────────────────
export type StepStatus = "pending" | "active" | "complete" | "failed";
export interface FlowStep { n: number; status: StepStatus; turns: number; lastVerify?: string }
export interface ConductorNode {
  id: string; step: number; reason: string; tier?: string; model?: string;
  resolved: boolean; tokens?: number; cost?: number; failures?: number;
  ts: number;            // ms — for ordering the swimlane
}
// Phase 7 — the memory/compaction surface. pre_llm_call re-injects the execution
// contract every turn (the anchor that survives context compaction); we count
// re-injections + compaction events and keep the latest contract text.
export interface MemoryState {
  anchors: number;        // pre_llm_call re-injections (one per turn)
  compactions: number;    // observed context-compaction events
  lastContract?: string;  // the re-injected execution contract (what survives)
}

export interface FlowState {
  total: number;          // best-known step count (0 until first llm_call)
  current: number;        // current step
  steps: FlowStep[];
  conductors: ConductorNode[];
  done: boolean;
  memory: MemoryState;
}

// ── the run chrome HUD (Fix C) ──────────────────────────────────────────────
export interface ChromeMetrics {
  step: number; total: number; turns: number;
  cost_usd: number; tokps: number | null;
  model?: string; tier?: string;              // planner identity (from guidance)
  running: boolean;
  // planner / executor cost split — the seed of the cost thesis, made visible
  // in the persistent chrome. Planner = the rare cloud guidance calls; executor
  // = the local worker absorbing the bulk of tokens (typically free).
  plannerTokens: number; plannerCost: number;
  execProvider?: string; execFreeTok: number; execPaidTok: number;
  // capped sample rings driving the live sparklines (tok/s trend, cost-rate).
  tokpsHist: number[]; costHist: number[];
}

export interface FeedState {
  items: FeedItem[];
  flow: FlowState;
  chrome: ChromeMetrics;
  // internal accumulators (not rendered directly)
  _nextId: number;
  _costEventTotal: number;     // last cumulative total from `cost` events
  _guidanceTotal: number;      // summed conductor guidance cost
  _lastRespTs: number;         // ms of last llm_response (for tok/s fallback)
  _ewmaTokps: number | null;
  _streamId: number;           // id of the active streamed-answer item (0 = none)
  _reasonId: number;           // id of the active streamed-reasoning item (0 = none)
}

export const initialFeed: FeedState = {
  items: [],
  flow: { total: 0, current: 1, steps: [], conductors: [], done: false, memory: { anchors: 0, compactions: 0 } },
  chrome: {
    step: 1, total: 0, turns: 0, cost_usd: 0, tokps: null, running: true,
    plannerTokens: 0, plannerCost: 0, execFreeTok: 0, execPaidTok: 0,
    tokpsHist: [], costHist: [],
  },
  _nextId: 1,
  _costEventTotal: 0,
  _guidanceTotal: 0,
  _lastRespTs: 0,
  _ewmaTokps: null,
  _streamId: 0,
  _reasonId: 0,
};

export type FeedAction =
  | { type: "batch"; events: { evt: EventType; data: any; now: number }[] }
  | { type: "user"; text: string; now: number }
  | { type: "reset"; userText?: string | null };

// ── helpers ─────────────────────────────────────────────────────────────────
function trunc(s: unknown, n: number): string {
  const str = String(s ?? "").replace(/\s+/g, " ").trim();
  return str.length <= n ? str : str.slice(0, n - 1) + "…";
}

// capped sample ring for the sparklines — O(1) bounded memory (Fix D).
function pushSeries(arr: number[], v: number): number[] {
  if (!isFinite(v)) return arr;
  const next = [...arr, v];
  return next.length > MAX_SERIES ? next.slice(next.length - MAX_SERIES) : next;
}

// ── Phase 2 streaming fold ──────────────────────────────────────────────────
// One live item per turn grows as gen.* deltas arrive (the "typing" effect);
// the row shows a live tail, the full text lives in `body` (expandable). The
// item finalizes (stops growing) the moment any structural event arrives.
function streamTail(t: string): string {
  const one = t.replace(/\s+/g, " ").trim();
  return one.length > 110 ? "…" + one.slice(-110) : one;
}

function streamInto(
  s: FeedState, which: "_streamId" | "_reasonId",
  kind: FeedKind, tone: Tone, icon: string, title: string, text: string, now: number,
): FeedState {
  const id = s[which];
  if (id > 0) {
    const items = s.items.map((it) =>
      it.id === id ? { ...it, body: (it.body || "") + text, detail: streamTail((it.body || "") + text), ts: now } : it);
    return { ...s, items };
  }
  const newId = s._nextId;
  const it: FeedItem = { id: newId, kind, tone, icon, title, detail: streamTail(text), body: text, ts: now, repeat: 1 };
  const next = [...s.items, it];
  const capped = next.length > MAX_FEED_ITEMS ? next.slice(next.length - MAX_FEED_ITEMS) : next;
  return { ...s, items: capped, _nextId: newId + 1, [which]: newId };
}

// A structural event ends the current generation → freeze the streamed items so
// the next turn's tokens start fresh ones.
function finalizeStreams(s: FeedState): FeedState {
  return s._streamId === 0 && s._reasonId === 0 ? s : { ...s, _streamId: 0, _reasonId: 0 };
}

function ensureSteps(flow: FlowState, total: number): FlowStep[] {
  const steps = flow.steps.slice();
  const want = Math.min(Math.max(total, flow.current), MAX_GRAPH_NODES);
  while (steps.length < want) steps.push({ n: steps.length + 1, status: "pending", turns: 0 });
  return steps;
}

function setStep(steps: FlowStep[], n: number, patch: Partial<FlowStep>): FlowStep[] {
  return steps.map((s) => (s.n === n ? { ...s, ...patch } : s));
}

// push with circular-buffer cap + consecutive-duplicate collapsing
function push(items: FeedItem[], it: Omit<FeedItem, "id" | "repeat">, nextId: number): {
  items: FeedItem[]; nextId: number;
} {
  const last = items[items.length - 1];
  if (last && last.kind === it.kind && last.title === it.title && last.detail === it.detail) {
    const merged = { ...last, repeat: last.repeat + 1, ts: it.ts, hms: it.hms, meta: it.meta };
    return { items: [...items.slice(0, -1), merged], nextId };
  }
  const next = [...items, { ...it, id: nextId, repeat: 1 }];
  const capped = next.length > MAX_FEED_ITEMS ? next.slice(next.length - MAX_FEED_ITEMS) : next;
  return { items: capped, nextId: nextId + 1 };
}

// ── the reducer ──────────────────────────────────────────────────────────────
export function reduceFeed(state: FeedState, action: FeedAction): FeedState {
  if (action.type === "reset") {
    const s: FeedState = { ...initialFeed, items: [], flow: { ...initialFeed.flow, steps: [], conductors: [] } };
    if (action.userText) {
      const r = push(s.items, { kind: "user", tone: "accent", icon: "›", title: action.userText, ts: 0 }, s._nextId);
      s.items = r.items; s._nextId = r.nextId;
    }
    return s;
  }
  if (action.type === "user") {
    const r = push(state.items, { kind: "user", tone: "accent", icon: "›", title: action.text, ts: action.now }, state._nextId);
    return { ...state, items: r.items, _nextId: r.nextId };
  }
  // batch: apply each event in order against a single working copy
  let s: FeedState = state;
  for (const { evt, data, now } of action.events) s = applyOne(s, evt, data, now);
  return s;
}

function applyOne(state: FeedState, evt: EventType, data: any, now: number): FeedState {
  let s = { ...state };
  const add = (it: Omit<FeedItem, "id" | "repeat">) => {
    const r = push(s.items, it, s._nextId);
    s.items = r.items; s._nextId = r.nextId;
  };

  // Phase 2 — streamed token deltas grow a live item; everything else is a
  // structural event that finalizes the current stream first.
  if (evt === "gen.token") {
    const t = data?.text ?? data?.content ?? "";
    return t ? streamInto(s, "_streamId", "llm", "info", "✎", "responding…", t, now) : s;
  }
  if (evt === "gen.reasoning" || evt === "gen.thinking") {
    const t = data?.text ?? data?.content ?? "";
    return t ? streamInto(s, "_reasonId", "reasoning", "muted", "✲", "thinking…", t, now) : s;
  }
  s = finalizeStreams(s);

  switch (evt) {
    case "conductor":
      return applyConductor(s, data as ConductorEvt, now, add);

    case "tool_call": {
      const d = data as ToolCallEvt;
      if (d.status === "running") return s; // collapse: only surface terminal tool rows in the feed
      const ok = d.status === "ok";
      add({
        kind: "tool", tone: ok ? "info" : d.status === "fail" ? "bad" : "muted",
        icon: ok ? "•" : d.status === "fail" ? "✗" : "·",
        title: d.tool, detail: trunc(d.result_summary || d.input_summary || d.server || "", 120),
        meta: d.latency_ms != null ? `${(d.latency_ms / 1000).toFixed(1)}s` : undefined,
        ts: now, hms: d.hms,
      });
      return s;
    }
    case "file_op": {
      const d = data as FileOpEvt;
      add({ kind: "file", tone: "accent", icon: "✎", title: trunc(d.path, 80),
            detail: trunc(d.diff_summary || d.op, 80), body: d.diff_summary, meta: d.op, ts: now, hms: d.hms });
      return s;
    }
    case "shell": {
      const d = data as ShellEvt;
      const bad = d.exit_code != null && d.exit_code !== 0;
      add({ kind: "shell", tone: bad ? "bad" : "muted", icon: "$", title: trunc(d.cmd, 100),
            detail: trunc(d.stream_chunk || "", 80), ts: now, hms: d.hms });
      return s;
    }
    case "gate": {
      const d = data as GateEvt;
      const pass = d.status === "pass";
      add({ kind: "gate", tone: pass ? "good" : "bad", icon: pass ? "✓" : "✗",
            title: `gate: ${d.kind}`, detail: trunc(d.detail, 100), ts: now, hms: d.hms });
      return s;
    }
    case "checkpoint": {
      const d = data as CheckpointEvt;
      add({ kind: "checkpoint", tone: "good", icon: "◆", title: d.label || "checkpoint",
            detail: trunc(d.commit, 80), ts: now, hms: d.hms });
      return s;
    }
    case "escalation": {
      const d = data as EscalationEvt;
      add({ kind: "escalation", tone: "warn", icon: "↳",
            title: `${d.from_rung || "rung"} → ${d.to_rung || "next"}`,
            detail: trunc(d.reason, 100), ts: now, hms: d.hms });
      return s;
    }
    case "narration": {
      const d = data as NarrationEvt;
      add({ kind: "narration", tone: d.level === "warn" ? "warn" : "muted", icon: "…",
            title: trunc(d.plain_text, 160), ts: now, hms: d.hms });
      return s;
    }
    case "phase": {
      const d = data as PhaseEvt;
      if (d.phase === "done") {
        s.chrome = { ...s.chrome, running: false };
      }
      return s;
    }
    case "cost": {
      const d = data as CostEvt;
      s._costEventTotal = d.total_usd;
      const cost_usd = Math.max(s._costEventTotal, s._guidanceTotal);
      s.chrome = {
        ...s.chrome, cost_usd,
        execProvider: d.provider ?? s.chrome.execProvider,
        execFreeTok: d.free_tok ?? s.chrome.execFreeTok,
        execPaidTok: d.paid_tok ?? s.chrome.execPaidTok,
        costHist: pushSeries(s.chrome.costHist, cost_usd),
      };
      return s;
    }
    default:
      return s;
  }
}

function applyConductor(
  state: FeedState, d: ConductorEvt, now: number,
  add: (it: Omit<FeedItem, "id" | "repeat">) => void,
): FeedState {
  const s = state;
  const step = d.step ?? s.flow.current;
  let flow = s.flow;
  let chrome = { ...s.chrome };

  switch (d.event) {
    case "llm_call": {
      const total = d.total ?? flow.total;
      const steps = setStep(ensureSteps({ ...flow, current: step, total }, total), step, { status: "active", turns: d.turns_on_step ?? 0 });
      flow = { ...flow, current: step, total: Math.max(total, flow.total), steps };
      chrome = { ...chrome, step, total: Math.max(total, chrome.total), turns: chrome.turns + 1, running: true };
      break;
    }
    case "llm_response": {
      const out = d.output_tokens ?? d.tokens ?? 0;
      let tokps = chrome.tokps;
      if (out > 0) {
        let inst: number | null = null;
        if (d.elapsed_s && d.elapsed_s > 0) inst = out / d.elapsed_s;
        else if (s._lastRespTs > 0 && now > s._lastRespTs) inst = out / ((now - s._lastRespTs) / 1000);
        if (inst != null && isFinite(inst)) {
          const ewma = s._ewmaTokps == null ? inst : 0.6 * s._ewmaTokps + 0.4 * inst;
          s._ewmaTokps = ewma;
          tokps = ewma;
        }
      }
      s._lastRespTs = now;
      chrome = {
        ...chrome, tokps,
        tokpsHist: tokps != null ? pushSeries(chrome.tokpsHist, tokps) : chrome.tokpsHist,
      };
      // Reasoning collapses to a one-line, de-emphasized summary the instant the
      // turn completes (1.2). Live token-by-token thinking isn't folded into the
      // feed model, so we surface the honest post-hoc summary with a token badge.
      const think = d.thinking_tokens ?? 0;
      if (think > 0) {
        add({ kind: "reasoning", tone: "muted", icon: "✲",
              title: `thought for ${think.toLocaleString()} tokens`,
              meta: d.model || undefined, ts: now, hms: d.hms, step });
      }
      break;
    }
    case "tool":
    case "file_write": {
      add({ kind: "file", tone: "accent", icon: "✎", title: trunc(d.file || "wrote file", 80),
            detail: `step ${step}`, ts: now, hms: d.hms, step });
      return { ...s, flow, chrome };
    }
    case "verify_pass": {
      add({ kind: "verify", tone: "good", icon: "✓", title: "verify passed",
            detail: trunc(d.result, 100), meta: `step ${step}`, ts: now, hms: d.hms, step });
      flow = { ...flow, steps: setStep(ensureSteps(flow, flow.total), step, { status: "complete", lastVerify: "pass" }) };
      break;
    }
    case "verify_fail": {
      add({ kind: "verify", tone: "bad", icon: "✗",
            title: `verify failed (×${d.failures ?? 1})`, meta: `step ${step}`, ts: now, hms: d.hms, step });
      flow = { ...flow, steps: setStep(ensureSteps(flow, flow.total), step, { status: "failed", lastVerify: "fail" }) };
      break;
    }
    case "trigger": {
      const id = `c${s._nextId}`;
      add({ kind: "conductor", tone: "warn", icon: "⚡",
            title: `conductor: ${trunc(d.reason, 40)}`, meta: `${d.tier ?? ""} · step ${step}`.trim(),
            ts: now, hms: d.hms, step });
      const conductors = [...flow.conductors,
        { id, step, reason: d.reason || "?", tier: d.tier, resolved: false, failures: d.failures, ts: now }]
        .slice(-MAX_GRAPH_NODES);
      flow = { ...flow, conductors };
      break;
    }
    case "guidance": {
      const cost = typeof d.cost === "number" ? d.cost : 0;
      s._guidanceTotal += cost;
      add({ kind: "guidance", tone: "accent", icon: "✦", title: "frontier guidance ready",
            detail: trunc(d.model, 40),
            meta: [d.tokens ? `${d.tokens} tok` : "", cost ? `$${cost.toFixed(4)}` : "free"].filter(Boolean).join(" · "),
            ts: now, hms: d.hms, step });
      // resolve the most recent unresolved conductor node for this step
      let resolved = false;
      const conductors = flow.conductors.map((c) => {
        if (!resolved && !c.resolved && c.step === step) { resolved = true; return { ...c, resolved: true, model: d.model, tokens: d.tokens, cost }; }
        return c;
      });
      flow = { ...flow, conductors };
      chrome = {
        ...chrome, cost_usd: Math.max(s._costEventTotal, s._guidanceTotal),
        model: d.model || chrome.model, tier: d.tier || chrome.tier,
        plannerCost: s._guidanceTotal,
        plannerTokens: chrome.plannerTokens + (d.tokens ?? 0),
      };
      break;
    }
    case "step_advance": {
      const to = d.to_step ?? d.step ?? (flow.current + 1);
      const steps = setStep(ensureSteps({ ...flow, current: to }, Math.max(to, flow.total)), flow.current,
        flow.steps.find((x) => x.n === flow.current)?.status === "complete" ? {} : { status: "complete" });
      add({ kind: "step", tone: "info", icon: "→", title: `step ${flow.current} → ${to}`, ts: now, hms: d.hms, step: to });
      flow = { ...flow, current: to, steps: setStep(steps, to, { status: "active" }) };
      chrome = { ...chrome, step: to };
      break;
    }
    case "run_complete":
    case "session_end": {
      const done = d.event === "run_complete" || !!d.done;
      add({ kind: "complete", tone: done ? "good" : "muted", icon: done ? "◆" : "◇",
            title: done ? "run complete — verified" : "session ended",
            detail: d.total_turns != null ? `${d.total_turns} turns` : undefined, ts: now, hms: d.hms });
      flow = { ...flow, done };
      chrome = { ...chrome, running: false };
      break;
    }
    case "guidance_applied": {
      add({ kind: "guidance", tone: "accent", icon: "✦", title: "guidance applied", meta: `step ${step}`, ts: now, hms: d.hms, step });
      break;
    }
    case "budget_exhausted": {
      add({ kind: "conductor", tone: "warn", icon: "⚠", title: `escalation budget exhausted (${d.tier ?? ""})`, ts: now, hms: d.hms });
      break;
    }
    case "done_rejected": {
      // The agent thinks it's done, but the verify gate hasn't gone green — the
      // ground-truth distinction the verify-gate spine makes visible (3.3).
      add({ kind: "verify", tone: "warn", icon: "⊘", title: "done rejected — tests not yet green",
            meta: `step ${step}`, ts: now, hms: d.hms, step });
      flow = { ...flow, steps: setStep(ensureSteps(flow, flow.total), step, { lastVerify: "rejected" }) };
      break;
    }
    case "pre_llm_call": {
      // Phase 7.2 — the execution contract is re-injected every turn; this is the
      // anchor that survives context compaction (why the agent doesn't drift).
      const contract = d.contract || d.basis || d.note || flow.memory.lastContract;
      flow = { ...flow, memory: { ...flow.memory, anchors: flow.memory.anchors + 1, lastContract: contract } };
      break;
    }
    case "compaction":
    case "context_compacted":
    case "compact": {
      // Phase 7.1 — make the (usually invisible) compaction event legible.
      add({ kind: "narration", tone: "muted", icon: "♻", title: "context compacted — anchors survived",
            detail: trunc(flow.memory.lastContract, 80), ts: now, hms: d.hms, step });
      flow = { ...flow, memory: { ...flow.memory, compactions: flow.memory.compactions + 1 } };
      break;
    }
    default:
      return s;
  }
  return { ...s, flow, chrome };
}

// ── selectors ─────────────────────────────────────────────────────────────
export function conductorsForStep(flow: FlowState, n: number): ConductorNode[] {
  return flow.conductors.filter((c) => c.step === n);
}
