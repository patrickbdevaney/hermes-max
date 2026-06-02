// Phase 1.3 — the control plane. Every control is an independent Tauri command
// that POSTs to the loopback Python server FROM RUST (no CORS in Rust) carrying
// the per-launch secret (Phase 1.4). The render plane is React; the control
// plane is Rust commands; they meet at typed events. This is why the shell never
// needs cross-origin POST access to the backend.
use serde_json::{json, Value};

fn post(path: &str, body: Value) -> Result<Value, String> {
    let url = format!("http://127.0.0.1:{}{}", crate::sidecar::PORT, path);
    match ureq::post(&url)
        .set("X-HMX-Secret", &crate::secret::ensure())
        .set("Content-Type", "application/json")
        .send_json(body)
    {
        Ok(r) => r.into_json::<Value>().map_err(|e| e.to_string()),
        Err(ureq::Error::Status(c, r)) => Err(format!("HTTP {c}: {}", r.into_string().unwrap_or_default())),
        Err(e) => Err(e.to_string()),
    }
}

fn get(path: &str) -> Result<Value, String> {
    let url = format!("http://127.0.0.1:{}{}", crate::sidecar::PORT, path);
    match ureq::get(&url).set("X-HMX-Secret", &crate::secret::ensure()).call() {
        Ok(r) => r.into_json::<Value>().map_err(|e| e.to_string()),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
pub fn run_task(cwd: String, prompt: String, mode: Option<String>, approval_gate: bool) -> Result<Value, String> {
    post("/api/run", json!({ "cwd": cwd, "prompt": prompt, "mode": mode, "approval_gate": approval_gate }))
}

/// All runs the backend knows about (Phase 6 multi-project glance — map to
/// projects by cwd to show which are live).
#[tauri::command]
pub fn active_runs() -> Result<Value, String> {
    get("/api/runs")
}

#[tauri::command]
pub fn continue_run(run_id: String, prompt: String) -> Result<Value, String> {
    post("/api/run", json!({ "run_id": run_id, "prompt": prompt }))
}

/// Non-destructive nudge delivered at the next safe point (Python queues it).
#[tauri::command]
pub fn steer_run(run_id: String, text: String) -> Result<Value, String> {
    post(&format!("/api/run/{run_id}/steer"), json!({ "text": text }))
}

/// Cooperative pause (Phase 1.8 / Hard Decision #6): writes a flag the conductor
/// honours between steps — NOT an OS SIGSTOP on the model HTTP call.
#[tauri::command]
pub fn pause_run(run_id: String) -> Result<Value, String> {
    post(&format!("/api/run/{run_id}/pause"), json!({}))
}

#[tauri::command]
pub fn resume_run(run_id: String) -> Result<Value, String> {
    post(&format!("/api/run/{run_id}/resume"), json!({}))
}

/// Abort the current turn (SIGTERM the process group) — the destructive control.
#[tauri::command]
pub fn interrupt_run(run_id: String) -> Result<Value, String> {
    post(&format!("/api/run/{run_id}/signal"), json!({ "action": "interrupt" }))
}

#[tauri::command]
pub fn write_plan(cwd: String, content: String) -> Result<Value, String> {
    post("/api/plan", json!({ "cwd": cwd, "content": content }))
}

#[tauri::command]
pub fn set_mode(mode: String) -> Result<Value, String> {
    post("/api/config", json!({ "mode": mode }))
}

/// Conductor approval gate (Phase 1.3): approve/reject pending guidance by
/// writing a flag the conductor consults before re-injection.
#[tauri::command]
pub fn approve_guidance(run_id: String, approve: bool) -> Result<Value, String> {
    post(&format!("/api/run/{run_id}/approve"), json!({ "approve": approve }))
}

/// MCP control (Phase 1.3) — restart/start/stop the server stack via `hm`.
#[tauri::command]
pub fn mcp_control(action: String) -> Result<Value, String> {
    let verb = match action.as_str() {
        "restart" => "restart",
        "up" => "up",
        "down" => "down",
        other => return Err(format!("unknown mcp action: {other}")),
    };
    let root = crate::sidecar::repo_root();
    match std::process::Command::new("hm").arg(verb).current_dir(&root).status() {
        Ok(s) if s.success() => Ok(json!({ "ok": true, "action": verb })),
        Ok(s) => Err(format!("hm {verb} exited with {s}")),
        Err(e) => Err(format!("couldn't run hm {verb}: {e}")),
    }
}
