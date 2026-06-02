// The typed SSE event model — the seam shared with the backend (ui/server/feeds.py).
// One channel, discriminated by the SSE `event:` name. Tier 1 emits a subset; the
// rest (token / file_op / shell / checkpoint) are typed here so Tier 3 slots in
// without a contract change.

export type EventType =
  | "token" | "phase" | "plan" | "plan_item" | "tool_call" | "file_op"
  | "shell" | "gate" | "checkpoint" | "escalation" | "cost" | "narration"
  | "heartbeat" | "span" | "conductor";

export interface Base { run_id: string; ts?: number; hms?: string }

export interface TokenEvt extends Base { span_id?: string; text: string }
export interface PhaseEvt extends Base { phase: string; status: string }
export interface PlanItem { id: string; text: string; status: "pending" | "done" | "active" }
export interface PlanEvt extends Base { items: PlanItem[] }
export interface PlanItemEvt extends Base { id: string; status: PlanItem["status"] }

export type ToolStatus = "running" | "ok" | "fail" | "slow";
export interface ToolCallEvt extends Base {
  call_id?: string; tool: string; server?: string | null;
  input_summary?: string; result_summary?: string; status: ToolStatus;
  latency_ms?: number | null; reason?: string; elapsed_s?: number; note?: string;
  est_s?: number | null;
}
export interface FileOpEvt extends Base { op: "created" | "modified" | "deleted"; path: string; diff_summary?: string }
export interface ShellEvt extends Base { cmd: string; stream_chunk?: string; exit_code?: number | null }
export interface GateEvt extends Base { kind: string; status: "pass" | "fail"; detail?: string }
export interface CheckpointEvt extends Base { label: string; commit?: string }
export interface EscalationEvt extends Base { from_rung: string; to_rung: string; reason: string }
export interface CostEvt extends Base {
  delta_usd: number; total_usd: number; free: boolean;
  free_tok?: number; paid_tok?: number; provider?: string;
}
export interface NarrationEvt extends Base { plain_text: string; level?: "info" | "warn" }

// The conductor plugin's in-harness event feed (ui/server/feeds.py passes
// `conductor.<event>` livelog spans through verbatim). `event` discriminates:
// llm_call | llm_response | tool/file_write | verify_pass | verify_fail | trigger |
// guidance | guidance_applied | step_advance | run_complete | session_end | budget_exhausted.
export interface ConductorEvt extends Base {
  event: string;
  step?: number; total?: number; turns_on_step?: number; has_guidance?: boolean;
  reason?: string; tier?: string; model?: string;
  tokens?: number; thinking_tokens?: number; output_tokens?: number; elapsed_s?: number;
  cost?: number; failures?: number; result?: string; file?: string;
  from_step?: number; to_step?: number; done?: boolean; final_step?: number; total_turns?: number;
}
export type HeartbeatEvt = Base & { tool?: string; done?: number | null; total?: number | null;
  eta_s?: number | null; elapsed_s?: number | null; item?: string | null; note?: string | null };

// ── REST payloads ──
export interface ProviderStatus { name: string; present: boolean; reachable: boolean | null }
export interface RosterEntry { role: string; rung: string }

// The agent's DRIVER, detected from the active mode's executor (feeds.driver_status).
// Never hardcoded: a reachable vLLM endpoint = local|remote; a cloud executor with a
// key present = cloud; nothing usable = none.
export type DriverState = "local" | "remote" | "cloud" | "none";
export interface DriverStatus {
  state: DriverState;
  provider?: string | null;
  host?: string | null;
  base_url?: string;
  model?: string | null;
  latency_ms?: number | null;
  reachable?: boolean | null;
  label: string;
  detail?: string;
}

export interface StatusPayload {
  mode: string; providers: ProviderStatus[]; roster: RosterEntry[];
  today_spend_usd: number; free_rpd_remaining: Record<string, number | null>;
  gpu_present: boolean; driver?: DriverStatus; warnings: string[];
}
export interface ProviderKeyStatus {
  name: string; api_key_env: string | null; keyless: boolean;
  tier: string; present: boolean;
}
export interface KeysStatus {
  backend: string; backend_label: string; is_keychain: boolean;
  providers: ProviderKeyStatus[];
}
export interface TestResult {
  ok: boolean; latency_ms?: number; model?: string | null; status?: number;
  error?: string; detail?: string;
}
export interface ConfigResult {
  ok: boolean; applied?: string[]; warnings?: string[];
  error?: string; available?: string[]; config?: Record<string, unknown>;
}

// ── cost ledger (feeds.cost_payload → lib.inference.ledger.report) ──
export interface CostBucket { usd: number; tok: number; calls: number }
export interface CostReport {
  window: string; calls: number; total_usd: number; free_tok: number; paid_tok: number;
  by_provider: Record<string, CostBucket>;
  by_model: Record<string, CostBucket>;
  by_role: Record<string, CostBucket>;
  free_budget_remaining: Record<string, number | null>;
}

// A run surfaced by the server registry (Fix 4) — terminal, hm dev, or UI-launched.
export interface RunSummary {
  run_id: string; cwd?: string | null; prompt?: string | null; mode?: string | null;
  start_ts?: number | null; origin?: string; status?: string; active?: boolean;
}

// ── Phase 4: persistent run history (SQLite + FTS5 over the livelog) ──
export interface HistoryRun {
  run_id: string; prompt?: string | null; cwd?: string | null; mode?: string | null;
  origin?: string; start_ts?: number | null; end_ts?: number | null; status?: string;
  step_count?: number; turn_count?: number; cost_usd?: number;
  free_tok?: number; paid_tok?: number; conductor_fires?: number;
  verify_pass?: number; verify_fail?: number;
}
export interface HistoryEvent { event: string; data: any; seq: number; ts: number; hms: string }
export interface HistoryDetail { summary: HistoryRun; events: HistoryEvent[] }

export interface RecentProject { path: string; last_used: number | null }
export interface RunHandle {
  run_id: string; cwd: string; prompt: string | null; mode: string | null;
  start_ts: number; launched: boolean; status: string; launch_error: string | null;
}

// ── L2: normalized OTLP spans (the raw tree) ──
export interface SpanEvent { name: string; time_ns: number; attributes: Record<string, any> }
export interface Span {
  trace_id: string; span_id: string; parent_span_id: string;
  name: string; kind: number; start_ns: number; end_ns: number;
  duration_ms: number | null;
  status: { code: "unset" | "ok" | "error"; message: string };
  attributes: Record<string, any>; events: SpanEvent[];
  service: string; scope: string;
}
export interface SpanEvt extends Base { span: Span }

// ── L1 timeline entries (the reduced view state) ──
export type EntryKind = "tool" | "gate" | "escalation" | "checkpoint" | "phase" | "fileop" | "shell" | "stream";
export interface TimelineEntry {
  key: string;
  kind: EntryKind;
  title: string;
  subtitle?: string;
  status: ToolStatus | "pass" | "fail" | "info";
  server?: string | null;
  latency_ms?: number | null;
  detail?: string;          // raw I/O summary, reason, etc. (the L2-lite expand)
  progress?: { done: number; total: number; eta_s?: number | null; item?: string | null };
  hms?: string;
  ts?: number;
}
