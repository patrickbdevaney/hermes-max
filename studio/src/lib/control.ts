// The v2 control plane — typed wrappers over the Rust control commands, which
// POST to the loopback Python server carrying the per-launch secret. The shell
// never POSTs to the backend directly (cross-origin / blank-Origin on Linux).
import { invoke } from "./tauri";

export interface RunHandle { run_id: string; cwd: string; status: string; mode?: string | null; launch_error?: string | null }

export const runTask = (cwd: string, prompt: string, mode?: string | null, approvalGate = false) =>
  invoke<RunHandle>("run_task", { cwd, prompt, mode, approvalGate });
export const continueRun = (runId: string, prompt: string) =>
  invoke<RunHandle>("continue_run", { runId, prompt });
export const steerRun = (runId: string, text: string) => invoke("steer_run", { runId, text });
export const pauseRun = (runId: string) => invoke("pause_run", { runId });
export const resumeRun = (runId: string) => invoke("resume_run", { runId });
export const interruptRun = (runId: string) => invoke("interrupt_run", { runId });
export const writePlan = (cwd: string, content: string) => invoke("write_plan", { cwd, content });
export const setMode = (mode: string) => invoke("set_mode", { mode });
export const approveGuidance = (runId: string, approve: boolean) => invoke("approve_guidance", { runId, approve });
export const mcpControl = (action: "restart" | "up" | "down") => invoke("mcp_control", { action });
