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
    // Where hermes-max lives — resolved at first run (Phase 1.6). Closes the
    // builds-vs-installs gap: HERMES_MAX_ROOT / walk-up / compile-time fallback
    // all fail on a clean machine, so we ask once and persist it.
    #[serde(default)]
    pub repo_root: Option<String>,
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

/// Parse the repo's .env (KEY=VALUE, `export ` and quotes tolerated) so Studio
/// inherits whatever the repo already has configured — endpoint + provider keys.
pub fn repo_dotenv() -> std::collections::HashMap<String, String> {
    let mut m = std::collections::HashMap::new();
    let path = crate::sidecar::repo_root().join(".env");
    if let Ok(s) = std::fs::read_to_string(path) {
        for line in s.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((k, v)) = line.split_once('=') {
                let k = k.trim().trim_start_matches("export ").trim();
                let v = v.trim().trim_matches('"').trim_matches('\'');
                if !k.is_empty() {
                    m.insert(k.to_string(), v.to_string());
                }
            }
        }
    }
    m
}

/// The environment the Python sidecar (and the agent it spawns) should run with.
/// The repo's ENTIRE .env is inherited (the stdlib backend may not load it
/// itself), then Studio's own choices are layered ON TOP: keychain keys and the
/// studio.conf endpoint win. So a user never re-enters what the repo already
/// holds, but anything they set in Studio overrides it.
pub fn agent_env() -> Vec<(String, String)> {
    let mut map = repo_dotenv(); // base: everything already in the repo's .env
    let cfg = load();

    for e in keychain::PROVIDER_ENVS {
        if let Some(v) = keychain::get(e) {
            map.insert(e.to_string(), v);
        }
    }
    if let Some(url) = cfg.endpoint_url.filter(|u| !u.trim().is_empty()) {
        map.insert("VLLM_BASE_URL".to_string(), url.clone());
        map.insert("OPENAI_BASE_URL".to_string(), url);
    }
    map.into_iter().collect()
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
pub fn configure_endpoint(url: String, force: bool, mgr: State<SidecarManager>) -> ApplyResult {
    let probe = crate::detect::probe_endpoint(url.clone());
    if !probe.ok && !force {
        // couldn't confirm it — let the UI offer "use it anyway"
        return ApplyResult { ok: false, error: probe.error, model: None };
    }
    let mut cfg = load();
    cfg.endpoint_url = Some(url);
    cfg.provider = None;
    if let Err(e) = save(&cfg) {
        return ApplyResult { ok: false, error: Some(e), model: None };
    }
    mgr.restart(); // backend picks up the new endpoint
    // Saved either way; on a forced save we couldn't confirm a model list, which
    // is fine (the server may need a key or be slow) — the endpoint is used.
    ApplyResult { ok: true, error: None, model: probe.model }
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

/// First-run repo-root resolution (Phase 1.6). Validates that `ui/server` lives
/// under the given path before persisting it; the sidecar refuses to spawn until
/// this resolves.
#[tauri::command]
pub fn set_repo_root(path: String) -> Result<StudioConfig, String> {
    let base = std::path::PathBuf::from(shellexpand(&path));
    if !base.join("ui").join("server").is_dir() {
        return Err("That folder doesn't look like hermes-max — I couldn't find ui/server inside it.".into());
    }
    let mut cfg = load();
    cfg.repo_root = Some(base.to_string_lossy().to_string());
    save(&cfg)?;
    Ok(cfg)
}

fn shellexpand(p: &str) -> String {
    if let Some(rest) = p.strip_prefix("~/") {
        if let Some(h) = std::env::var_os("HOME") {
            return format!("{}/{}", h.to_string_lossy(), rest);
        }
    }
    p.to_string()
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
