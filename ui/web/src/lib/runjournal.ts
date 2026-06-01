// A small localStorage journal of runs this UI has launched: run_id → the prompt,
// cwd, mode and start time. This holds NO secrets (only what the operator typed),
// so persisting it is fine — it lets the Run view seed a turn's user message after
// a reload/deep-link, and backs the Activity history. The live event stream still
// comes from the server (replay re-opens the SSE from that run's log offset); the
// journal is only the human-readable label layer.

export interface RunRecord {
  run_id: string;
  prompt: string;
  cwd: string;
  mode: string | null;
  start_ts: number;       // ms (client clock — display only)
  turns: number;          // how many user messages have been sent
}

const KEY = "hmx.runs.v1";
const MAX = 60;

function load(): RunRecord[] {
  try {
    const raw = localStorage.getItem(KEY);
    const arr = raw ? (JSON.parse(raw) as RunRecord[]) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function save(items: RunRecord[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(items.slice(0, MAX)));
  } catch {
    /* storage disabled / full — the journal is best-effort */
  }
}

export const journal = {
  list(): RunRecord[] {
    return load().sort((a, b) => b.start_ts - a.start_ts);
  },
  get(runId: string): RunRecord | undefined {
    return load().find((r) => r.run_id === runId);
  },
  add(rec: Omit<RunRecord, "turns"> & { turns?: number }): void {
    const items = load().filter((r) => r.run_id !== rec.run_id);
    items.unshift({ turns: 1, ...rec });
    save(items);
  },
  bumpTurn(runId: string): void {
    const items = load();
    const r = items.find((x) => x.run_id === runId);
    if (r) { r.turns += 1; save(items); }
  },
  remove(runId: string): void {
    save(load().filter((r) => r.run_id !== runId));
  },
};
