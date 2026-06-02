// studio.conf access — the endpoint/provider + display/notification settings.
import { invoke } from "./tauri";

export interface StudioConfig {
  endpoint_url?: string | null;
  provider?: string | null;
  settings?: Record<string, boolean | string>;
}

export const loadStudioConfig = () => invoke<StudioConfig>("load_studio_config");
export const saveStudioSettings = (settings: Record<string, boolean | string>) =>
  invoke("save_studio_settings", { settings });
