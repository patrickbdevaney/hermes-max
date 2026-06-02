// studio.conf access — the endpoint/provider + display/notification settings.
import { invoke } from "./tauri";

export interface StudioConfig {
  endpoint_url?: string | null;
  provider?: string | null;
  repo_root?: string | null;
  settings?: Record<string, boolean | string>;
}

export const loadStudioConfig = () => invoke<StudioConfig>("load_studio_config");
export const saveStudioSettings = (settings: Record<string, boolean | string>) =>
  invoke("save_studio_settings", { settings });
// v2 1.6 — validate + persist where hermes-max lives (must contain ui/server).
export const setRepoRoot = (path: string) => invoke<StudioConfig>("set_repo_root", { path });
export const pickDirectory = () => invoke<string | null>("pick_directory");
