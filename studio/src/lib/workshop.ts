// Workshop status — typed wrappers over the Rust livelog bridge. The studio bar
// stays in sync with the embedded web UI's chrome via these `workshop-status`
// events (Rust tails the livelog; the cross-origin shell can't read the SSE).
import { invoke, listen } from "./tauri";

export interface WorkshopStatus {
  phrase: string;
  step: number;
  total: number;
  cost_usd: number;
  tokens: number;
  running: boolean;
  event: string;
  done: boolean;
}

export const startWorkshop = (dir: string) => invoke("start_workshop", { dir });
export const stopWorkshop = () => invoke("stop_workshop");
export const onWorkshopStatus = (cb: (s: WorkshopStatus) => void) =>
  listen<WorkshopStatus>("workshop-status", (e) => cb(e.payload));
