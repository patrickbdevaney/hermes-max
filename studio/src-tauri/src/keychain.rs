// OS keychain (keyring crate) + provider key validation. On Linux this uses the
// pure-Rust secret-service backend (the org.freedesktop.secrets DBus service);
// on macOS the native keychain (with the apple-native feature). Keys are stored
// under one service, accounted by the env var the agent reads — so injecting
// them into the Python sidecar's environment (config::agent_env) is a lookup.
use std::time::Duration;

use keyring::Entry;
use serde_json::Value;

const SERVICE: &str = "hermes-max";

/// All provider env vars Studio knows how to store/inject.
pub const PROVIDER_ENVS: &[&str] = &[
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPINFRA_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
];

pub fn store(account: &str, secret: &str) -> Result<(), String> {
    Entry::new(SERVICE, account)
        .and_then(|e| e.set_password(secret))
        .map_err(|e| e.to_string())
}

pub fn get(account: &str) -> Option<String> {
    Entry::new(SERVICE, account).ok().and_then(|e| e.get_password().ok())
}

/// env vars (and the provider name behind them) that have a stored key.
pub fn configured() -> Vec<String> {
    PROVIDER_ENVS.iter().filter(|e| get(e).is_some()).map(|e| e.to_string()).collect()
}

fn provider_base(env: &str) -> Option<&'static str> {
    match env {
        "ANTHROPIC_API_KEY" => Some("https://api.anthropic.com/v1"),
        "OPENAI_API_KEY" => Some("https://api.openai.com/v1"),
        "GROQ_API_KEY" => Some("https://api.groq.com/openai/v1"),
        "CEREBRAS_API_KEY" => Some("https://api.cerebras.ai/v1"),
        "GEMINI_API_KEY" => Some("https://generativelanguage.googleapis.com/v1beta/openai"),
        "DEEPSEEK_API_KEY" => Some("https://api.deepseek.com/v1"),
        "DEEPINFRA_API_KEY" => Some("https://api.deepinfra.com/v1/openai"),
        "OPENROUTER_API_KEY" => Some("https://openrouter.ai/api/v1"),
        "TOGETHER_API_KEY" => Some("https://api.together.xyz/v1"),
        _ => None,
    }
}

fn first_model_id(j: &Value) -> Option<String> {
    j.get("data")
        .and_then(|d| d.get(0))
        .and_then(|m| m.get("id"))
        .and_then(|s| s.as_str())
        .map(|s| s.to_string())
}

/// Validate a key by listing models with it. Returns the first model id on
/// success, an error string on failure.
pub fn validate_key(env: &str, key: &str) -> Result<Option<String>, String> {
    let base = provider_base(env).ok_or_else(|| "Unknown provider.".to_string())?;
    let url = format!("{base}/models");
    let req = if env == "ANTHROPIC_API_KEY" {
        ureq::get(&url).set("x-api-key", key).set("anthropic-version", "2023-06-01")
    } else {
        ureq::get(&url).set("Authorization", &format!("Bearer {key}"))
    }
    .timeout(Duration::from_secs(8));

    match req.call() {
        Ok(resp) => Ok(resp.into_json::<Value>().ok().as_ref().and_then(first_model_id)),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            Err("That key was rejected — double-check you pasted the whole thing.".into())
        }
        Err(ureq::Error::Status(code, _)) => Err(format!("The provider returned an error (HTTP {code}).")),
        Err(_) => Err("Couldn't reach the provider — check your internet connection.".into()),
    }
}
