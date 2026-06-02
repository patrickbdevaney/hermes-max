// Workshop status bridge. The embedded web UI talks to its own same-origin
// Python backend (fully functional); the Studio shell, on a different origin,
// can't read that SSE stream cross-origin. So Rust tails the livelog directly
// (no CORS) and forwards a plain-language status + live cost to the shell via
// `workshop-status` tauri events — keeping the studio bar in sync with the web
// UI's chrome with no second poll loop.
//
// On entering a project we also preset the backend's recent-projects file with
// the project's directory, so the web UI launcher defaults to it and the user
// never types a working directory.
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

#[derive(Default)]
pub struct WorkshopTailer {
    stop: Mutex<Option<Arc<AtomicBool>>>,
}

#[derive(Serialize, Clone, Default)]
pub struct WorkshopStatus {
    pub phrase: String,
    pub step: i64,
    pub total: i64,
    pub cost_usd: f64,
    pub tokens: i64,
    pub running: bool,
    pub event: String,
    pub done: bool,
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn livelog() -> PathBuf {
    let dir = std::env::var("HERMES_MAX_LOG_DIR")
        .or_else(|_| std::env::var("HMX_LOG_DIR"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| home().join(".hermes-max").join("logs"));
    dir.join("live.jsonl")
}

fn ledger_path() -> PathBuf {
    std::env::var("INFERENCE_LEDGER_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| home().join(".hermes-max").join("inference").join("ledger.jsonl"))
}

/// The plain-language phrase for the studio bar — the user's vocabulary, not the
/// system's (S3.4).
fn status_phrase(event: &str, has_guidance: bool) -> &'static str {
    match event {
        "llm_call" if has_guidance => "Applying a correction…",
        "llm_call" => "Thinking…",
        "verify_pass" => "Checking the work… ✓",
        "verify_fail" => "Tests didn't pass — fixing…",
        "trigger" => "The planner is stepping in…",
        "guidance" => "Applying a correction…",
        "step_advance" => "Moving to the next part…",
        "run_complete" => "All done ✓",
        "session_end" => "Done — your turn.",
        "done_rejected" => "Almost there — one more check…",
        _ => "Working…",
    }
}

/// Sum cost + tokens recorded since the workshop opened (the live total).
fn ledger_since(start: f64) -> (f64, i64) {
    let (mut cost, mut tok) = (0.0, 0i64);
    if let Ok(f) = std::fs::File::open(ledger_path()) {
        for line in BufReader::new(f).lines().map_while(Result::ok) {
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if v.get("ts").and_then(Value::as_f64).unwrap_or(0.0) >= start {
                    cost += v.get("cost_usd").and_then(Value::as_f64).unwrap_or(0.0);
                    tok += v.get("in_tok").and_then(Value::as_i64).unwrap_or(0)
                        + v.get("out_tok").and_then(Value::as_i64).unwrap_or(0);
                }
            }
        }
    }
    (cost, tok)
}

/// Preset the backend's recent-projects so the web UI launcher defaults to this
/// project's directory (the user never types a path).
fn write_recent(dir: &str) {
    let p = home().join(".hermes-max").join("ui");
    let _ = std::fs::create_dir_all(&p);
    let path = p.join("recent_projects.json");
    let mut items: Vec<Value> = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    items.retain(|it| it.get("path").and_then(Value::as_str) != Some(dir));
    items.insert(0, json!({ "path": dir, "last_used": now() }));
    items.truncate(50);
    if let Ok(s) = serde_json::to_string_pretty(&items) {
        let _ = std::fs::write(path, s);
    }
}

fn start(app: AppHandle, dir: String) {
    write_recent(&dir);
    let tailer = app.state::<WorkshopTailer>();
    if let Some(prev) = tailer.stop.lock().unwrap().take() {
        prev.store(true, Ordering::SeqCst); // stop any prior tail
    }
    let stop = Arc::new(AtomicBool::new(false));
    *tailer.stop.lock().unwrap() = Some(stop.clone());
    let start_ts = now();

    std::thread::spawn(move || {
        let path = livelog();
        let mut offset = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        let mut st = WorkshopStatus { running: false, phrase: "Ready when you are.".into(), ..Default::default() };
        let mut persisted = false;
        while !stop.load(Ordering::SeqCst) {
            if let Ok(mut f) = std::fs::File::open(&path) {
                let len = f.metadata().map(|m| m.len()).unwrap_or(0);
                if len > offset {
                    let _ = f.seek(SeekFrom::Start(offset));
                    let reader = BufReader::new(&mut f);
                    for line in reader.lines().map_while(Result::ok) {
                        offset += line.len() as u64 + 1;
                        let Ok(v) = serde_json::from_str::<Value>(&line) else { continue };
                        if v.get("kind").and_then(Value::as_str) != Some("span") {
                            continue;
                        }
                        let Some(ev) = v.get("span").and_then(Value::as_str).and_then(|s| s.strip_prefix("conductor.")) else { continue };
                        let has_guidance = v.get("has_guidance").and_then(Value::as_bool).unwrap_or(false);
                        st.event = ev.to_string();
                        st.phrase = status_phrase(ev, has_guidance).to_string();
                        if let Some(s) = v.get("step").and_then(Value::as_i64) { st.step = s; }
                        if let Some(t) = v.get("total").and_then(Value::as_i64) { st.total = t; }
                        if ev == "run_complete" || ev == "session_end" {
                            st.running = false;
                            st.done = ev == "run_complete";
                        } else {
                            st.running = true;
                            persisted = false; // a new turn started
                        }
                    }
                }
            }
            let (cost, tok) = ledger_since(start_ts);
            st.cost_usd = cost;
            st.tokens = tok;
            let _ = app.emit("workshop-status", st.clone());
            if st.done && !persisted {
                crate::projects::update_stats(&dir, st.step, st.total, st.cost_usd, st.tokens);
                persisted = true;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    });
}

#[tauri::command]
pub fn start_workshop(app: AppHandle, dir: String) {
    start(app, dir);
}

#[tauri::command]
pub fn stop_workshop(app: AppHandle) {
    if let Some(prev) = app.state::<WorkshopTailer>().stop.lock().unwrap().take() {
        prev.store(true, Ordering::SeqCst);
    }
}
