// Phase 4 — project memory + checkpoints, read straight from disk in Rust (no
// backend round-trip, no CORS). Surfaces durable state as MEMORY in the user's
// vocabulary ("47 files indexed · the plan") and exposes the conductor's git
// checkpoints with a SAFE, non-destructive fork.
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::Serialize;
use serde_json::{json, Value};

#[derive(Serialize)]
pub struct ProjectMemory {
    pub plan_present: bool,
    pub plan_steps: usize,
    pub file_count: usize,
    pub last_objective: Option<String>,
}

fn count_files(dir: &Path, cap: usize) -> usize {
    const SKIP: &[&str] = &[".git", "node_modules", "target", "dist", "__pycache__", ".venv", "venv", ".hermes-conductor"];
    let mut count = 0usize;
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        if count >= cap { break; }
        let rd = match std::fs::read_dir(&d) { Ok(r) => r, Err(_) => continue };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            let p = e.path();
            if p.is_dir() {
                if !SKIP.contains(&name.as_str()) && !name.starts_with('.') { stack.push(p); }
            } else {
                count += 1;
                if count >= cap { break; }
            }
        }
    }
    count
}

#[tauri::command]
pub fn project_memory(cwd: String) -> ProjectMemory {
    let base = PathBuf::from(&cwd);
    let plan = std::fs::read_to_string(base.join("PLAN.md")).ok();
    let plan_present = plan.is_some();
    let (plan_steps, last_objective) = match plan.as_deref() {
        Some(p) => {
            let steps = p.lines().filter(|l| {
                let t = l.trim_start();
                t.starts_with("- [") || t.starts_with("* [") || t.starts_with("1.") || t.starts_with("## ")
            }).count();
            let obj = p.lines()
                .find(|l| l.trim_start().starts_with('#'))
                .map(|l| l.trim_start_matches('#').trim().to_string())
                .filter(|s| !s.is_empty());
            (steps, obj)
        }
        None => (0, None),
    };
    ProjectMemory { plan_present, plan_steps, file_count: count_files(&base, 5000), last_objective }
}

/// The conductor's git checkpoints (a verified commit per checkpoint). Newest first.
#[tauri::command]
pub fn checkpoints(cwd: String) -> Vec<Value> {
    let mut out = Vec::new();
    if let Ok(o) = Command::new("git")
        .args(["-C", &cwd, "log", "--max-count=25", "--pretty=%H%x1f%s%x1f%ct"])
        .output()
    {
        if o.status.success() {
            for line in String::from_utf8_lossy(&o.stdout).lines() {
                let p: Vec<&str> = line.split('\u{1f}').collect();
                if p.len() == 3 {
                    out.push(json!({ "commit": p[0], "short": &p[0][..p[0].len().min(8)],
                                     "subject": p[1], "ts": p[2].parse::<i64>().unwrap_or(0) }));
                }
            }
        }
    }
    out
}

/// Fork from a checkpoint — SAFE: auto-stashes any dirty state, then branches at
/// the commit (the current branch + work are preserved). Phase 4.4.
#[tauri::command]
pub fn fork_checkpoint(cwd: String, commit: String, name: String) -> Result<Value, String> {
    let safe: String = name.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '-' }).collect();
    let branch = format!("studio-fork-{}", if safe.trim_matches('-').is_empty() { "checkpoint".into() } else { safe });
    let _ = Command::new("git").args(["-C", &cwd, "stash", "push", "-u", "-m", "studio-fork-autostash"]).output();
    let o = Command::new("git").args(["-C", &cwd, "checkout", "-b", &branch, &commit]).output().map_err(|e| e.to_string())?;
    if o.status.success() {
        Ok(json!({ "ok": true, "branch": branch }))
    } else {
        Err(String::from_utf8_lossy(&o.stderr).trim().to_string())
    }
}
