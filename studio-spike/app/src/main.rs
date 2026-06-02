// THROWAWAY SPIKE — Phase 0 items 2 + 3.
//   2. A Tauri CHANNEL sustains the token rate (coalesced ~50Hz) without jank.
//   3. A custom protocol (hermes://localhost/) serves a single-origin index.html.
// The webview self-measures frame jank and writes a verdict, so the gate can run
// headless under Xvfb with no human in the loop. Discard after the gate.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};

use serde::Serialize;
use tauri::ipc::Channel;
use tauri::{WebviewUrl, WebviewWindowBuilder};

#[derive(Serialize, Clone)]
struct Batch {
    tokens: String,
    events: Vec<serde_json::Value>,
    done: bool,
}

/// Open the loopback SSE stream in Rust (no CORS), parse typed events, coalesce
/// token deltas into ~50Hz batches, and forward over the Channel. Structured
/// events ride along in the same batch.
#[tauri::command]
fn start_stream(on_event: Channel<Batch>) {
    let url = std::env::var("SPIKE_SSE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:7099/api/events/spike".to_string());
    std::thread::spawn(move || {
        let resp = match ureq::get(&url).call() {
            Ok(r) => r,
            Err(e) => {
                let _ = on_event.send(Batch { tokens: format!("[stream error: {e}]"), events: vec![], done: true });
                return;
            }
        };
        let reader = BufReader::new(resp.into_reader());
        let mut cur = String::new();
        let mut buf = String::new();
        let mut evs: Vec<serde_json::Value> = Vec::new();
        let mut last = std::time::Instant::now();

        let flush = |buf: &mut String, evs: &mut Vec<serde_json::Value>, done: bool, ch: &Channel<Batch>| {
            if !buf.is_empty() || !evs.is_empty() || done {
                let _ = ch.send(Batch {
                    tokens: std::mem::take(buf),
                    events: std::mem::take(evs),
                    done,
                });
            }
        };

        for line in reader.lines() {
            let line = match line { Ok(l) => l, Err(_) => break };
            if let Some(ev) = line.strip_prefix("event:") {
                cur = ev.trim().to_string();
            } else if let Some(d) = line.strip_prefix("data:") {
                let d = d.trim();
                if d == "[DONE]" {
                    flush(&mut buf, &mut evs, true, &on_event);
                    break;
                }
                if cur == "gen.token" || cur == "gen.reasoning" {
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(d) {
                        if let Some(t) = v.get("text").and_then(|x| x.as_str()) {
                            buf.push_str(t);
                        }
                    }
                } else if let Ok(v) = serde_json::from_str::<serde_json::Value>(d) {
                    evs.push(serde_json::json!({ "event": cur, "data": v }));
                }
            } else if line.is_empty() {
                cur.clear();
                if last.elapsed().as_millis() >= 20 {
                    flush(&mut buf, &mut evs, false, &on_event); // ~50Hz coalesce
                    last = std::time::Instant::now();
                }
            }
        }
        flush(&mut buf, &mut evs, false, &on_event);
    });
}

/// The webview writes its self-measured verdict here; we mirror to stdout and
/// exit so the headless harness can read it.
#[tauri::command]
fn write_verdict(app: tauri::AppHandle, json: String) {
    let path = std::env::var("SPIKE_VERDICT").unwrap_or_else(|_| "/tmp/spike_verdict.json".to_string());
    let _ = std::fs::write(&path, &json);
    println!("VERDICT {json}");
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(300));
        app.exit(0);
    });
}

fn main() {
    let gate = std::env::var("SPIKE_GATE_SECONDS").unwrap_or_else(|_| "30".to_string());
    tauri::Builder::default()
        // Item 3: a custom protocol serving a single-origin index.html (a single
        // buffered response — which is exactly what custom protocols CAN do; this
        // is NOT stream proxying, which is impossible on Linux).
        .register_uri_scheme_protocol("hermes", move |_ctx, request| {
            let path = request.uri().path();
            let (mime, bytes): (&str, Vec<u8>) = if path == "/" || path == "/index.html" {
                let html = include_str!("../index.html").replace("__GATE_SECONDS__", &gate);
                ("text/html", html.into_bytes())
            } else {
                ("text/plain", b"ok".to_vec())
            };
            tauri::http::Response::builder()
                .status(200)
                .header("Content-Type", mime)
                .body(bytes)
                .unwrap()
        })
        .invoke_handler(tauri::generate_handler![start_stream, write_verdict])
        .setup(|app| {
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::CustomProtocol("hermes://localhost/".parse().unwrap()),
            )
            .title("Hermes Studio — Phase 0 spike")
            .inner_size(900.0, 700.0)
            .build()?;
            // safety net: hard-exit if the gate never writes a verdict
            let h = app.handle().clone();
            let cap = std::env::var("SPIKE_GATE_SECONDS").ok().and_then(|s| s.parse::<u64>().ok()).unwrap_or(30);
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_secs(cap + 20));
                h.exit(0);
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running spike-app");
}
