// First-run actions — typed wrappers over the Rust commands that configure the
// AI source. Endpoint + keys are stored Rust-side (studio.conf + OS keychain)
// and injected into the Python sidecar's environment, which the agent inherits —
// so the shell never needs to cross-origin POST to the backend.
import { invoke } from "./tauri";

export interface Provider {
  id: string;
  name: string;
  env: string;        // the env var the agent reads
  keyUrl: string;     // where to get a key
  pricingUrl: string;
  free?: boolean;
}

export const PROVIDERS: Provider[] = [
  { id: "anthropic", name: "Anthropic", env: "ANTHROPIC_API_KEY", keyUrl: "https://console.anthropic.com/settings/keys", pricingUrl: "https://www.anthropic.com/pricing" },
  { id: "openai", name: "OpenAI", env: "OPENAI_API_KEY", keyUrl: "https://platform.openai.com/api-keys", pricingUrl: "https://openai.com/api/pricing" },
  { id: "groq", name: "Groq", env: "GROQ_API_KEY", keyUrl: "https://console.groq.com/keys", pricingUrl: "https://groq.com/pricing", free: true },
  { id: "deepseek", name: "DeepSeek", env: "DEEPSEEK_API_KEY", keyUrl: "https://platform.deepseek.com/api_keys", pricingUrl: "https://api-docs.deepseek.com/quick_start/pricing" },
  { id: "together", name: "Together", env: "TOGETHER_API_KEY", keyUrl: "https://api.together.ai/settings/api-keys", pricingUrl: "https://www.together.ai/pricing" },
  { id: "openrouter", name: "OpenRouter", env: "OPENROUTER_API_KEY", keyUrl: "https://openrouter.ai/keys", pricingUrl: "https://openrouter.ai/models" },
];

export interface ApplyResult { ok: boolean; error?: string; model?: string | null }

// Validate + persist a local OpenAI-compatible endpoint, then restart the stack
// so the backend (and the agent it spawns) pick it up.
export const configureEndpoint = (url: string) =>
  invoke<ApplyResult>("configure_endpoint", { url });

// Validate + store a provider key in the OS keychain, then restart the stack.
export const saveProviderKey = (provider: string, env: string, key: string) =>
  invoke<ApplyResult>("save_provider_key", { provider, env, key });

export const openUrl = (url: string) => invoke("open_url", { url });
