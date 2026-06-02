// Phase 4/6 — project memory + checkpoints + the multi-project run list, all via
// Rust (which reads the filesystem / git / the loopback backend directly).
import { invoke } from "./tauri";

export interface ProjectMemory {
  plan_present: boolean;
  plan_steps: number;
  file_count: number;
  last_objective: string | null;
}
export interface Checkpoint { commit: string; short: string; subject: string; ts: number }
export interface RunRow { run_id: string; cwd?: string | null; prompt?: string | null; active?: boolean; status?: string }

export const projectMemory = (cwd: string) => invoke<ProjectMemory>("project_memory", { cwd });
export const checkpoints = (cwd: string) => invoke<Checkpoint[]>("checkpoints", { cwd });
export const forkCheckpoint = (cwd: string, commit: string, name: string) =>
  invoke<{ ok: boolean; branch: string }>("fork_checkpoint", { cwd, commit, name });
export const activeRuns = () => invoke<{ runs: RunRow[] }>("active_runs");
