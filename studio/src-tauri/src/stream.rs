// Phase 1.1/1.7 — the SINGLE source of truth for the shell. Rust opens the
// loopback SSE stream (no CORS), parses the FULL typed event set the web UI's
// reducer consumes PLUS the Phase 2 token events (gen.token / gen.reasoning /
// gen.tool_call_delta — schema baked in now so no later phase touches Rust), and
// forwards everything over a Tauri Channel (NOT emit — Channels are built for
// ordered high-throughput streaming). High-frequency token deltas are coalesced
// to ~50Hz; structured events flush immediately. This replaces workshop.rs and
// its dual-observer race entirely: there is exactly one stream.
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager};

use crate::sidecar::PORT;

// True while a run is streaming — gates graceful restart (Phase 1.8).
static ACTIVE_RUN: AtomicBool = AtomicBool::new(false);
pub fn is_run_active() -> bool {
    ACTIVE_RUN.load(Ordering::SeqCst)
}

#[derive(Default)]
pub struct StreamState {
    stop: Mutex<Option<Arc<AtomicBool>>>,
}

#[derive(Serialize, Clone, Default)]
pub struct Chrome {
    pub step: i64,
    pub total: i64,
    pub turns: i64,
    pub cost_usd: f64,
    pub tokens: i64,
    pub running: bool,
    pub done: bool,
    pub phrase: String,
    pub model: Option<String>,
    pub tier: Option<String>,
}

/// One coalesced frame to the shell. `tokens`/`reasoning` are concatenated
/// deltas since the last frame; `events` are structured events (verbatim, for
/// Phase 3's native feed.ts reducer); `chrome` is the derived HUD state.
#[derive(Serialize, Clone)]
pub struct StreamMsg {
    pub tokens: String,
    pub reasoning: String,
    pub events: Vec<Value>,
    pub chrome: Chrome,
    pub done: bool,
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn ledger_path() -> PathBuf {
    std::env::var("INFERENCE_LEDGER_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| home().join(".hermes-max").join("inference").join("ledger.jsonl"))
}

/// Read a run descriptor → (start_ts, cwd). Per-run cost is attributed by the
/// run's OWN start_ts window (Phase 1.9), never a workshop-open window.
fn run_descriptor(run_id: &str) -> (f64, Option<String>) {
    let safe: String = run_id.chars().filter(|c| c.is_alphanumeric() || *c == '-' || *c == '_').collect();
    let p = home().join(".hermes-max").join("runs").join(format!("{safe}.json"));
    if let Ok(s) = std::fs::read_to_string(p) {
        if let Ok(v) = serde_json::from_str::<Value>(&s) {
            let ts = v.get("start_ts").and_then(Value::as_f64).unwrap_or(0.0);
            let cwd = v.get("cwd").and_then(Value::as_str).map(|s| s.to_string());
            return (ts, cwd);
        }
    }
    (0.0, None)
}

/// Sum cost + tokens from ledger rows at/after `start_ts` (this run's window).
fn ledger_since(start_ts: f64) -> (f64, i64) {
    let (mut cost, mut tok) = (0.0, 0i64);
    if let Ok(f) = std::fs::File::open(ledger_path()) {
        for line in BufReader::new(f).lines().map_while(Result::ok) {
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if v.get("ts").and_then(Value::as_f64).unwrap_or(0.0) >= start_ts {
                    cost += v.get("cost_usd").and_then(Value::as_f64).unwrap_or(0.0);
                    tok += v.get("in_tok").and_then(Value::as_i64).unwrap_or(0)
                        + v.get("out_tok").and_then(Value::as_i64).unwrap_or(0);
                }
            }
        }
    }
    (cost, tok)
}

fn status_phrase(event: &str, has_guidance: bool) -> &'static str {
    match event {
        "llm_call" if has_guidance => "Applying a correction…",
        "llm_call" => "Thinking…",
        "verify_pass" => "Checking the work… ✓",
        "verify_fail" => "Tests didn't pass — fixing…",
        "trigger" | "guidance" => "The planner is stepping in…",
        "step_advance" => "Moving to the next part…",
        "run_complete" => "All done ✓",
        "session_end" => "Done — your turn.",
        "done_rejected" => "Almost there — one more check…",
        _ => "Working…",
    }
}

/// Start (or restart) the single run stream → Channel. Replaces start_workshop.
#[tauri::command]
pub fn start_run_stream(app: AppHandle, run_id: String, on_event: Channel<StreamMsg>) {
    let st = app.state::<StreamState>();
    if let Some(prev) = st.stop.lock().unwrap().take() {
        prev.store(true, Ordering::SeqCst);
    }
    let stop = Arc::new(AtomicBool::new(false));
    *st.stop.lock().unwrap() = Some(stop.clone());

    let (start_ts, cwd) = run_descriptor(&run_id);
    let project_name = cwd.as_deref().and_then(crate::projects::name_for_dir).unwrap_or_else(|| "Your project".into());
    let url = format!("http://127.0.0.1:{PORT}/api/events/{run_id}");

    std::thread::spawn(move || {
        ACTIVE_RUN.store(true, Ordering::SeqCst);
        let resp = match ureq::get(&url).call() {
            Ok(r) => r,
            Err(e) => {
                let _ = on_event.send(StreamMsg {
                    tokens: format!("[stream error: {e}]"), reasoning: String::new(),
                    events: vec![], chrome: Chrome::default(), done: true,
                });
                ACTIVE_RUN.store(false, Ordering::SeqCst);
                return;
            }
        };
        let reader = BufReader::new(resp.into_reader());

        let mut cur = String::new();
        let mut tok_buf = String::new();
        let mut rz_buf = String::new();
        let mut evs: Vec<Value> = Vec::new();
        let mut chrome = Chrome { running: true, phrase: "Working…".into(), ..Default::default() };
        let mut last_flush = Instant::now();
        let mut last_cost = Instant::now() - Duration::from_secs(2);
        let mut fail_streak = 0i64;
        let mut persisted = false;

        let do_flush = |tok_buf: &mut String, rz_buf: &mut String, evs: &mut Vec<Value>,
                        chrome: &Chrome, done: bool, ch: &Channel<StreamMsg>| {
            let _ = ch.send(StreamMsg {
                tokens: std::mem::take(tok_buf),
                reasoning: std::mem::take(rz_buf),
                events: std::mem::take(evs),
                chrome: chrome.clone(),
                done,
            });
        };

        for line in reader.lines() {
            if stop.load(Ordering::SeqCst) { break; }
            let line = match line { Ok(l) => l, Err(_) => break };

            if let Some(ev) = line.strip_prefix("event:") {
                cur = ev.trim().to_string();
                continue;
            }
            if !line.starts_with("data:") {
                continue; // id: / retry: / blank
            }
            let data = line[5..].trim();
            if data == "[DONE]" {
                chrome.running = false; chrome.done = true; chrome.phrase = "All done ✓".into();
                do_flush(&mut tok_buf, &mut rz_buf, &mut evs, &chrome, true, &on_event);
                break;
            }
            let v: Value = match serde_json::from_str(data) { Ok(v) => v, Err(_) => continue };

            match cur.as_str() {
                "gen.token" => { if let Some(t) = v.get("text").and_then(Value::as_str) { tok_buf.push_str(t); } }
                "gen.reasoning" => { if let Some(t) = v.get("text").and_then(Value::as_str) { rz_buf.push_str(t); } }
                "gen.tool_call_delta" => { evs.push(serde_json::json!({"event": "gen.tool_call_delta", "data": v})); }
                "conductor" => {
                    let cev = v.get("event").and_then(Value::as_str).unwrap_or("");
                    let has_g = v.get("has_guidance").and_then(Value::as_bool).unwrap_or(false);
                    chrome.phrase = status_phrase(cev, has_g).to_string();
                    if let Some(s) = v.get("step").and_then(Value::as_i64) { chrome.step = s; }
                    if let Some(t) = v.get("total").and_then(Value::as_i64) { chrome.total = t; }
                    if let Some(m) = v.get("model").and_then(Value::as_str) { chrome.model = Some(m.to_string()); }
                    if let Some(t) = v.get("tier").and_then(Value::as_str) { chrome.tier = Some(t.to_string()); }
                    match cev {
                        "llm_call" => chrome.turns += 1,
                        "run_complete" | "session_end" => { chrome.running = false; chrome.done = cev == "run_complete"; }
                        "verify_fail" => {
                            fail_streak += 1;
                            if fail_streak == 3 && crate::notify::prefs().attention {
                                crate::notify::send(&app, &format!("{project_name} needs attention"),
                                    "Tests haven't passed — you may want to steer it.");
                            }
                        }
                        "verify_pass" | "step_advance" => fail_streak = 0,
                        "trigger" => if crate::notify::prefs().conductor && !crate::notify::focused(&app) {
                            crate::notify::send(&app, &format!("Planner stepped in on {project_name}"),
                                "The cloud planner is correcting the build.");
                        },
                        "done_rejected" => if crate::notify::prefs().complete {
                            crate::notify::send(&app, &format!("{project_name}: almost done"), "One more check before it's ready.");
                        },
                        _ => {}
                    }
                    evs.push(serde_json::json!({"event": "conductor", "data": v}));
                    do_flush(&mut tok_buf, &mut rz_buf, &mut evs, &chrome, false, &on_event); // structured → immediate
                    last_flush = Instant::now();
                    continue;
                }
                other if !other.is_empty() => { evs.push(serde_json::json!({"event": other, "data": v})); }
                _ => {}
            }

            // refresh per-run cost (throttled) + coalesce tokens to ~50Hz
            if last_cost.elapsed() >= Duration::from_millis(750) {
                let (cost, tok) = ledger_since(start_ts);
                chrome.cost_usd = cost; chrome.tokens = tok;
                last_cost = Instant::now();
            }
            if last_flush.elapsed() >= Duration::from_millis(20) {
                do_flush(&mut tok_buf, &mut rz_buf, &mut evs, &chrome, false, &on_event);
                last_flush = Instant::now();
            }
        }

        // settle: final cost + project stats + tray + completion notify
        let (cost, tok) = ledger_since(start_ts);
        chrome.cost_usd = cost; chrome.tokens = tok;
        do_flush(&mut tok_buf, &mut rz_buf, &mut evs, &chrome, chrome.done, &on_event);
        let tip = if chrome.done { format!("Hermes Studio — {project_name} is ready") } else { "Hermes Studio — idle".to_string() };
        crate::tray::set_tooltip(&app, &tip);
        if chrome.done && !persisted {
            if let Some(ref dir) = cwd {
                crate::projects::update_stats(dir, chrome.step, chrome.total, chrome.cost_usd, chrome.tokens);
            }
            if crate::notify::prefs().complete {
                crate::notify::send(&app, &format!("✓ {project_name} is ready"), &format!("Built · ${:.2}", chrome.cost_usd));
            }
            persisted = true;
        }
        let _ = persisted;
        ACTIVE_RUN.store(false, Ordering::SeqCst);
    });
}

#[tauri::command]
pub fn stop_run_stream(app: AppHandle) {
    if let Some(prev) = app.state::<StreamState>().stop.lock().unwrap().take() {
        prev.store(true, Ordering::SeqCst);
    }
    ACTIVE_RUN.store(false, Ordering::SeqCst);
}
