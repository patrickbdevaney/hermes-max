// Capability detection + stack health — thin typed wrappers over the Rust
// commands (detect.rs / sidecar.rs), all routed through the single Tauri seam.
import { invoke } from "./tauri";

export type SuggestedMode = "Local" | "Cloud" | "NeedsSetup";

export interface DetectResult {
  hermes_present: boolean;
  hermes_version: string | null;
  endpoint_configured: boolean;
  endpoint_url: string | null;
  endpoint_reachable: boolean | null;
  endpoint_model: string | null;
  keys_configured: string[];
  suggested_mode: SuggestedMode;
}

export interface EndpointProbe {
  ok: boolean;
  latency_ms: number | null;
  model: string | null;
  error: string | null;
}

export interface StackStatus {
  python_server: boolean;
  mcp_servers: [string, boolean][];
  hermes_present: boolean;
  active_run: string | null;
  needs_repo: boolean;       // repo_root unresolved → first-run must set it (v2 1.6)
  adopted_python: boolean;   // adopted an already-running server (v2 1.5)
}

export const probeCapabilities = () => invoke<DetectResult>("probe_capabilities");
export const probeEndpoint = (url: string) => invoke<EndpointProbe>("probe_endpoint", { url });
export const stackHealth = () => invoke<StackStatus>("stack_health");
export const startStack = () => invoke<StackStatus>("start_stack");
export const stopStack = () => invoke("stop_stack");
