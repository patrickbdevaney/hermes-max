// Projects — a project is a working directory + a name + run history. Stored in
// ~/.hermes-max/studio/projects.json (the user never hears "working directory").
// The actual build files live wherever the user chose; Studio just remembers the
// path and opens the agent there.
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

#[derive(Serialize, Deserialize, Clone)]
pub struct Project {
    pub id: String,
    pub name: String,
    pub dir: String,
    #[serde(default)]
    pub prompt: Option<String>,
    pub created_ts: f64,
    #[serde(default)]
    pub last_run_ts: Option<f64>,
    #[serde(default)]
    pub last_status: Option<String>,
    #[serde(default)]
    pub last_step: Option<i64>,
    #[serde(default)]
    pub last_total: Option<i64>,
    #[serde(default)]
    pub lifetime_cost_usd: f64,
    #[serde(default)]
    pub lifetime_tokens: i64,
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn studio_dir() -> PathBuf {
    let d = home().join(".hermes-max").join("studio");
    let _ = std::fs::create_dir_all(&d);
    d
}

fn projects_json() -> PathBuf {
    studio_dir().join("projects.json")
}

fn now_ts() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn slug(name: &str) -> String {
    let s: String = name
        .trim()
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect();
    let s = s.trim_matches('-').replace("--", "-");
    if s.is_empty() { "project".into() } else { s }
}

fn load_all() -> Vec<Project> {
    std::fs::read_to_string(projects_json())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_all(list: &[Project]) -> Result<(), String> {
    let json = serde_json::to_string_pretty(list).map_err(|e| e.to_string())?;
    std::fs::write(projects_json(), json).map_err(|e| e.to_string())
}

fn unique_id(base: &str, existing: &[Project]) -> String {
    let mut id = base.to_string();
    let mut n = 2;
    while existing.iter().any(|p| p.id == id) {
        id = format!("{base}-{n}");
        n += 1;
    }
    id
}

// ── tauri commands ───────────────────────────────────────────────────────────
#[tauri::command]
pub fn list_projects() -> Vec<Project> {
    let mut list = load_all();
    list.sort_by(|a, b| {
        b.last_run_ts
            .unwrap_or(b.created_ts)
            .partial_cmp(&a.last_run_ts.unwrap_or(a.created_ts))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    list
}

#[tauri::command]
pub fn create_project(name: String, dir: Option<String>, new_folder: bool) -> Result<Project, String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err("Give your project a name.".into());
    }
    let mut list = load_all();

    let target: PathBuf = if new_folder || dir.as_deref().map(|s| s.trim().is_empty()).unwrap_or(true) {
        // create ~/Projects/<slug> (uniquified)
        let root = home().join("Projects");
        let mut base = root.join(slug(&name));
        let mut n = 2;
        while base.exists() {
            base = root.join(format!("{}-{n}", slug(&name)));
            n += 1;
        }
        std::fs::create_dir_all(&base).map_err(|e| format!("couldn't create the folder: {e}"))?;
        base
    } else {
        let p = PathBuf::from(dir.unwrap());
        if !p.is_dir() {
            return Err("That folder doesn't exist.".into());
        }
        p
    };

    let project = Project {
        id: unique_id(&slug(&name), &list),
        name,
        dir: target.to_string_lossy().to_string(),
        prompt: None,
        created_ts: now_ts(),
        last_run_ts: None,
        last_status: Some("ready".into()),
        last_step: None,
        last_total: None,
        lifetime_cost_usd: 0.0,
        lifetime_tokens: 0,
    };
    list.push(project.clone());
    save_all(&list)?;
    Ok(project)
}

#[tauri::command]
pub fn rename_project(id: String, name: String) -> Result<Project, String> {
    let mut list = load_all();
    let p = list.iter_mut().find(|p| p.id == id).ok_or("unknown project")?;
    p.name = name.trim().to_string();
    let updated = p.clone();
    save_all(&list)?;
    Ok(updated)
}

/// Forget a project (removes the entry only — never deletes the user's files).
#[tauri::command]
pub fn delete_project(id: String) -> Result<(), String> {
    let mut list = load_all();
    list.retain(|p| p.id != id);
    save_all(&list)
}

#[tauri::command]
pub fn open_path(path: String) {
    #[cfg(target_os = "linux")]
    let _ = std::process::Command::new("xdg-open").arg(&path).spawn();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg(&path).spawn();
}

#[tauri::command]
pub fn pick_directory(app: AppHandle) -> Option<String> {
    app.dialog()
        .file()
        .blocking_pick_folder()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().to_string())
}
