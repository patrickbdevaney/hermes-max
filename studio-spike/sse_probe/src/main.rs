// THROWAWAY SPIKE — Phase 0 item 1: prove Rust opens the loopback SSE stream,
// stays connected for the whole run, and parses EVERY typed event. No CORS in
// Rust (the whole point of the v2 architecture). No GUI. Prints a JSON verdict.
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::time::Instant;

fn main() {
    let url = std::env::var("SPIKE_SSE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:7099/api/events/spike".to_string());
    eprintln!("sse_probe → {url}");

    let resp = match ureq::get(&url).call() {
        Ok(r) => r,
        Err(e) => {
            println!("{}", serde_json::json!({"ok": false, "error": e.to_string()}));
            std::process::exit(1);
        }
    };

    let reader = BufReader::new(resp.into_reader());
    let mut counts: HashMap<String, u64> = HashMap::new();
    let mut total: u64 = 0;
    let start = Instant::now();
    let mut first_event_ms: Option<u128> = None;
    let mut last_event = Instant::now();
    let mut max_gap_ms: u128 = 0;
    let mut cur_event = String::new();
    let mut done = false;

    for line in reader.lines() {
        let line = match line { Ok(l) => l, Err(_) => break };
        if line.starts_with("event:") {
            cur_event = line[6..].trim().to_string();
        } else if line.starts_with("data:") {
            let data = line[5..].trim();
            if data == "[DONE]" { done = true; break; }
        } else if line.is_empty() && !cur_event.is_empty() {
            // a full SSE frame has been read
            total += 1;
            *counts.entry(cur_event.clone()).or_insert(0) += 1;
            if first_event_ms.is_none() {
                first_event_ms = Some(start.elapsed().as_millis());
            }
            let gap = last_event.elapsed().as_millis();
            if gap > max_gap_ms { max_gap_ms = gap; }
            last_event = Instant::now();
            cur_event.clear();
        }
    }

    let secs = start.elapsed().as_secs_f64();
    let verdict = serde_json::json!({
        "ok": true,
        "done_sentinel_seen": done,
        "connected_secs": (secs * 100.0).round() / 100.0,
        "total_events": total,
        "by_event": counts,
        "ttfb_ms": first_event_ms,
        "max_gap_ms": max_gap_ms,
        "avg_rate_hz": if secs > 0.0 { (total as f64 / secs * 10.0).round() / 10.0 } else { 0.0 },
    });
    println!("{verdict}");
}
