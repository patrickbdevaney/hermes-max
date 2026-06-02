// Studio configuration (~/.hermes-max/studio.conf) + the first-run write paths.
// Studio is the source of truth for the AI source: the endpoint URL lives in
// studio.conf, provider keys live in the OS keychain. Both are injected into the
// Python sidecar's environment at start (agent_env), and the agent the backend
// spawns inherits them — so the shell never cross-origin POSTs to the backend.
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::keychain;
use crate::sidecar::SidecarManager;

#[derive(Serialize, Deserialize, Default, Clone)]
pub struct StudioConfig {
    #[serde(default)]
    pub endpoint_url: Option<String>,
    #[serde(default)]
    pub provider: Option<String>, // active cloud provider id (e.g. "groq")
    // Display / notification prefs (S4) — kept here so one file holds all of it.
    #[serde(default)]
    pub settings: serde_json::Value,
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn conf_path() -> PathBuf {
    let dir = home().join(".hermes-max");
    let _ = std::fs::create_dir_all(&dir);
    dir.join("studio.conf")
}

pub fn load() -> StudioConfig {
    std::fs::read_to_string(conf_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save(cfg: &StudioConfig) -> Result<(), String> {
    let json = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(conf_path(), json).map_err(|e| e.to_string())
}

/// The environment the Python sidecar (and the agent it spawns) should run with:
/// the configured endpoint + every stored provider key.
pub fn agent_env() -> Vec<(String, String)> {
    let mut env = Vec::new();
    let cfg = load();
    if let Some(url) = cfg.endpoint_url.filter(|u| !u.trim().is_empty()) {
        env.push(("VLLM_BASE_URL".to_string(), url.clone()));
        env.push(("OPENAI_BASE_URL".to_string(), url));
    }
    for e in keychain::PROVIDER_ENVS {
        if let Some(v) = keychain::get(e) {
            env.push((e.to_string(), v));
        }
    }
    env
}

#[derive(Serialize)]
pub struct ApplyResult {
    pub ok: bool,
    pub error: Option<String>,
    pub model: Option<String>,
}

// ── tauri commands ───────────────────────────────────────────────────────────
#[tauri::command]
pub fn load_studio_config() -> StudioConfig {
    load()
}

#[tauri::command]
pub fn save_studio_settings(settings: serde_json::Value) -> Result<(), String> {
    let mut cfg = load();
    cfg.settings = settings;
    save(&cfg)
}

#[tauri::command]
pub fn configure_endpoint(url: String, mgr: State<SidecarManager>) -> ApplyResult {
    match crate::detect::probe_endpoint(url.clone()) {
        p if p.ok => {
            let mut cfg = load();
            cfg.endpoint_url = Some(url);
            cfg.provider = None;
            if let Err(e) = save(&cfg) {
                return ApplyResult { ok: false, error: Some(e), model: None };
            }
            mgr.restart(); // backend picks up the new endpoint
            ApplyResult { ok: true, error: None, model: p.model }
        }
        p => ApplyResult { ok: false, error: p.error, model: None },
    }
}

#[tauri::command]
pub fn save_provider_key(provider: String, env: String, key: String, mgr: State<SidecarManager>) -> ApplyResult {
    match keychain::validate_key(&env, &key) {
        Ok(model) => {
            if let Err(e) = keychain::store(&env, &key) {
                return ApplyResult { ok: false, error: Some(e), model: None };
            }
            let mut cfg = load();
            cfg.provider = Some(provider);
            let _ = save(&cfg);
            mgr.restart(); // backend + agent inherit the new key
            ApplyResult { ok: true, error: None, model }
        }
        Err(e) => ApplyResult { ok: false, error: Some(e), model: None },
    }
}

#[tauri::command]
pub fn open_url(url: String) {
    #[cfg(target_os = "linux")]
    let _ = std::process::Command::new("xdg-open").arg(&url).spawn();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg(&url).spawn();
}

#[tauri::command]
pub fn restart_stack(mgr: State<SidecarManager>) -> crate::sidecar::StackStatus {
    mgr.restart()
}
