// Capability detection — the silent first-run probe (hermes presence, configured
// endpoint reachability, provider keys) and a standalone endpoint test. Pure
// read-only probes with short timeouts; results drive the first-run screen.
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::sidecar::which;

#[derive(Serialize)]
pub struct DetectResult {
    pub hermes_present: bool,
    pub hermes_version: Option<String>,
    pub endpoint_configured: bool,
    pub endpoint_url: Option<String>,
    pub endpoint_reachable: Option<bool>,
    pub endpoint_model: Option<String>,
    pub keys_configured: Vec<String>,
    pub suggested_mode: String, // "Local" | "Cloud" | "NeedsSetup"
}

#[derive(Serialize)]
pub struct EndpointProbe {
    pub ok: bool,
    pub latency_ms: Option<u64>,
    pub model: Option<String>,
    pub error: Option<String>,
}

const PROVIDER_KEYS: &[(&str, &str)] = &[
    ("Anthropic", "ANTHROPIC_API_KEY"),
    ("OpenAI", "OPENAI_API_KEY"),
    ("Groq", "GROQ_API_KEY"),
    ("DeepSeek", "DEEPSEEK_API_KEY"),
    ("Together", "TOGETHER_API_KEY"),
    ("OpenRouter", "OPENROUTER_API_KEY"),
    ("Gemini", "GEMINI_API_KEY"),
    ("Cerebras", "CEREBRAS_API_KEY"),
];

fn endpoint_from_env() -> Option<String> {
    for k in ["VLLM_BASE_URL", "OPENAI_BASE_URL", "HERMES_ENDPOINT"] {
        if let Ok(v) = std::env::var(k) {
            if !v.trim().is_empty() {
                return Some(v);
            }
        }
    }
    None
}

/// GET {base}/models — returns (reachable, first model id).
fn probe_models(base: &str) -> (Option<bool>, Option<String>) {
    let url = format!("{}/models", base.trim_end_matches('/'));
    match ureq::get(&url).timeout(Duration::from_millis(1500)).call() {
        Ok(resp) => {
            let model = resp
                .into_json::<serde_json::Value>()
                .ok()
                .and_then(|j| {
                    j.get("data")
                        .and_then(|d| d.get(0))
                        .and_then(|m| m.get("id"))
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string())
                });
            (Some(true), model)
        }
        Err(_) => (Some(false), None),
    }
}

#[tauri::command]
pub fn probe_capabilities() -> DetectResult {
    let hermes_present = which("hermes").is_some();
    let hermes_version = if hermes_present {
        std::process::Command::new("hermes")
            .arg("--version")
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    } else {
        None
    };

    let endpoint_url = endpoint_from_env();
    let endpoint_configured = endpoint_url.is_some();
    let (endpoint_reachable, endpoint_model) = match &endpoint_url {
        Some(u) => probe_models(u),
        None => (None, None),
    };

    // A provider counts as configured if its key is in this process's env OR in
    // the OS keychain (where Studio stores keys; the sidecar injects them).
    let stored = crate::keychain::configured();
    let keys_configured: Vec<String> = PROVIDER_KEYS
        .iter()
        .filter(|(_, env)| {
            std::env::var(env).map(|v| !v.trim().is_empty()).unwrap_or(false)
                || stored.iter().any(|s| s == env)
        })
        .map(|(name, _)| name.to_string())
        .collect();

    let suggested_mode = if endpoint_reachable == Some(true) {
        "Local"
    } else if !keys_configured.is_empty() {
        "Cloud"
    } else {
        "NeedsSetup"
    }
    .to_string();

    DetectResult {
        hermes_present,
        hermes_version,
        endpoint_configured,
        endpoint_url,
        endpoint_reachable,
        endpoint_model,
        keys_configured,
        suggested_mode,
    }
}

#[tauri::command]
pub fn probe_endpoint(url: String) -> EndpointProbe {
    let t0 = Instant::now();
    match probe_models(&url) {
        (Some(true), model) => EndpointProbe {
            ok: true,
            latency_ms: Some(t0.elapsed().as_millis() as u64),
            model,
            error: None,
        },
        _ => EndpointProbe {
            ok: false,
            latency_ms: None,
            model: None,
            error: Some("Couldn't reach an OpenAI-compatible /models endpoint there.".into()),
        },
    }
}
