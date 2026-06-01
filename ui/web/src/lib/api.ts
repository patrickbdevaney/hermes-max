// Typed REST client. The launch token rides as the `X-HMX-Token` header on every
// call (and as a query param on the SSE URL, since EventSource can't set headers).
import { launchToken } from "./token";
import type {
  StatusPayload, RecentProject, RunHandle, KeysStatus, TestResult, ConfigResult,
  CostReport,
} from "../types";

// The CSRF cookie is set SameSite=Strict by the server on page load; we echo it
// back as a header on POSTs (double-submit). Read it live (not once) so it's picked
// up even if the page is served via a dev proxy that sets it on the first API call.
function csrf(): string {
  const m = document.cookie.match(/(?:^|; )hmx_csrf=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}

// The token header is only meaningful when the operator opted into --token (remote
// exposure); empty by default and ignored by the server.
function authHeaders(): Record<string, string> {
  return launchToken ? { "X-HMX-Token": launchToken } : {};
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: authHeaders(), credentials: "same-origin" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-HMX-CSRF": csrf(), ...authHeaders() },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { error?: string }).error || `${path} → ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => get<StatusPayload>("/api/status"),
  config: () => get<Record<string, unknown>>("/api/config"),
  cost: (window = "today") => get<CostReport>(`/api/cost?window=${encodeURIComponent(window)}`),
  recent: () => get<{ projects: RecentProject[] }>("/api/projects/recent"),
  run: (cwd: string, prompt: string, mode?: string | null) =>
    post<RunHandle>("/api/run", { cwd, prompt, mode }),
  // Turn 2+ of a conversation: continue the same run (server reuses its cwd/session).
  continueRun: (runId: string, prompt: string) =>
    post<RunHandle>("/api/run", { run_id: runId, prompt }),
  // Switch the active posture; re-resolves the role chains server-side.
  setMode: (mode: string) => post<ConfigResult>("/api/config", { mode }),
  // Tier 2: config surface. The secret rides in the POST body and never comes back.
  keysStatus: () => get<KeysStatus>("/api/keys/status"),
  storeKey: (provider: string, value: string) =>
    post<{ ok: boolean; error?: string; backend_label?: string }>(
      `/api/keys/${encodeURIComponent(provider)}`, { value }),
  testConnection: (provider: string) =>
    post<TestResult>("/api/test-connection", { provider }),
  applyConfig: (body: { mode?: string; vllm_base_url?: string }) =>
    post<ConfigResult>("/api/config", body),
  // Open the OS-native directory picker on the local machine (Fix 3).
  browseDir: (start?: string) =>
    post<{ path: string | null; cancelled?: boolean; error?: string; hint?: string }>(
      "/api/browse-dir", { start }),
  // EventSource URL (token in query — EventSource has no header API).
  eventsUrl: (runId: string) =>
    `/api/events/${encodeURIComponent(runId)}?token=${encodeURIComponent(launchToken)}`,
};
