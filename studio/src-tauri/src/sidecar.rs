// Sidecar lifecycle — the Python backend (ui.server) + MCP servers, managed
// entirely by the Rust side. The user never sees them start or stop. Each child
// is launched in its own session (setsid) so we can signal the whole process
// group on shutdown; SIGTERM then SIGKILL guarantees no orphans.
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

pub const PORT: u16 = 7080;

#[derive(Default)]
pub struct SidecarManager {
    children: Mutex<Vec<Child>>, // ONLY processes we spawned (adopted ones are never added → never killed)
    started: Mutex<bool>,
    spawned_python: Mutex<bool>, // did WE spawn the python server (vs. adopt a running one)?
}

#[derive(Serialize, Clone)]
pub struct StackStatus {
    pub python_server: bool,
    pub mcp_servers: Vec<(String, bool)>,
    pub hermes_present: bool,
    pub active_run: Option<String>,
    pub needs_repo: bool,       // repo_root unresolved → first-run must set it (1.6)
    pub adopted_python: bool,   // we adopted an already-running server (1.5)
}

// ── small helpers (shared with detect.rs) ────────────────────────────────────
pub fn which(bin: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths).map(|p| p.join(bin)).find(|p| p.is_file())
    })
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn log_path(name: &str) -> PathBuf {
    let dir = home().join(".hermes-max").join("studio").join("logs");
    let _ = std::fs::create_dir_all(&dir);
    dir.join(name)
}

fn valid_root(p: &std::path::Path) -> bool {
    p.join("ui").join("server").is_dir()
}

/// A repo root that ACTUALLY contains ui/server, or None. Order (Phase 1.6):
/// studio.conf (first-run answer) → $HERMES_MAX_ROOT → walk up from the binary →
/// compile-time dev fallback. None on a clean machine → first-run must resolve it.
pub fn resolved_root() -> Option<PathBuf> {
    if let Some(r) = crate::config::load().repo_root {
        let p = PathBuf::from(&r);
        if valid_root(&p) { return Some(p); }
    }
    if let Ok(r) = std::env::var("HERMES_MAX_ROOT") {
        let p = PathBuf::from(r);
        if valid_root(&p) { return Some(p); }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        while let Some(d) = dir {
            if valid_root(&d) { return Some(d); }
            dir = d.parent().map(|p| p.to_path_buf());
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().and_then(|p| p.parent()).map(|p| p.to_path_buf());
    dev.filter(|p| valid_root(p))
}

/// Best-effort root for non-critical callers (logs, `hm` invocations).
pub fn repo_root() -> PathBuf {
    resolved_root().unwrap_or_else(|| home().join("hermes-max"))
}

fn healthz_ok() -> bool {
    let url = format!("http://127.0.0.1:{PORT}/healthz");
    matches!(
        ureq::get(&url).timeout(Duration::from_millis(800)).call(),
        Ok(r) if r.status() == 200
    )
}

fn tcp_open(port: u16) -> bool {
    use std::net::{SocketAddr, TcpStream};
    TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(80),
    )
    .is_ok()
}

fn spawn_logged(cmd: &mut Command, logfile: &str) -> std::io::Result<Child> {
    let log = std::fs::OpenOptions::new().create(true).append(true).open(log_path(logfile))?;
    let errlog = log.try_clone()?;
    cmd.stdin(Stdio::null()).stdout(Stdio::from(log)).stderr(Stdio::from(errlog));
    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        // own session → child becomes a process-group leader we can signal as a group
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    cmd.spawn()
}

#[cfg(unix)]
fn term_group(pid: u32) {
    unsafe {
        // negative pid → the whole group (children too); also hit the leader.
        libc::kill(-(pid as i32), libc::SIGTERM);
        libc::kill(pid as i32, libc::SIGTERM);
    }
}
#[cfg(not(unix))]
fn term_group(_pid: u32) {}

impl SidecarManager {
    pub fn hermes_present(&self) -> bool {
        which("hermes").is_some()
    }

    /// Start the stack (idempotent), ADOPTING anything already running instead of
    /// double-starting it (Phase 1.5). Refuses (returns needs_repo) until the
    /// repo root is resolved (Phase 1.6). Mints the per-launch secret (Phase 1.4).
    pub fn start(&self) -> StackStatus {
        let root = match resolved_root() {
            Some(r) => r,
            None => return self.status(), // needs_repo=true; first-run must resolve it
        };
        let secret = crate::secret::ensure(); // mint + persist the 0600 secret file
        let mut started = self.started.lock().unwrap();
        if !*started {
            // 1. Python backend — ADOPT if /healthz already answers (hm ui up).
            if healthz_ok() {
                *self.spawned_python.lock().unwrap() = false;
            } else if let Ok(child) = spawn_logged(
                Command::new("python3")
                    .args(["-m", "ui.server", "--no-open", "--port", &PORT.to_string()])
                    .current_dir(&root)
                    .env("PYTHONPATH", &root)
                    .env("HERMES_MAX_ROOT", &root)
                    .env("HERMES_STUDIO_SECRET", &secret)
                    .envs(crate::config::agent_env()),
                "python-server.log",
            ) {
                self.children.lock().unwrap().push(child);
                *self.spawned_python.lock().unwrap() = true;
                for _ in 0..25 {
                    if healthz_ok() { break; }
                    std::thread::sleep(Duration::from_millis(200));
                }
            }
            // 2. MCP servers — ADOPT if any MCP port is already open (hm dev/up up),
            // otherwise best-effort `hm dev`.
            let mcp_up = (9101..=9115u16).any(tcp_open);
            if !mcp_up && which("hm").is_some() {
                if let Ok(child) = spawn_logged(
                    Command::new("hm").arg("dev").current_dir(&root).env("HERMES_MAX_ROOT", &root),
                    "mcp.log",
                ) {
                    self.children.lock().unwrap().push(child);
                }
            }
            *started = true; // reconciled to OBSERVED reality below via status()
        }
        self.status()
    }

    pub fn status(&self) -> StackStatus {
        let mcp = (9101..=9115u16).map(|p| (format!(":{p}"), tcp_open(p))).collect();
        let py = healthz_ok();
        let spawned = *self.spawned_python.lock().unwrap();
        StackStatus {
            python_server: py,
            mcp_servers: mcp,
            hermes_present: self.hermes_present(),
            active_run: None,
            needs_repo: resolved_root().is_none(),
            adopted_python: py && !spawned,
        }
    }

    /// Graceful restart (Phase 1.8): REFUSE while a run is streaming — never kill
    /// the backend out from under a live run view. Only restarts our own sidecars.
    pub fn restart(&self) -> StackStatus {
        if crate::stream::is_run_active() {
            return self.status();
        }
        self.stop_all();
        self.start()
    }

    pub fn stop_all(&self) {
        let mut kids = self.children.lock().unwrap();
        for child in kids.iter() {
            term_group(child.id()); // only processes WE spawned are in here
        }
        std::thread::sleep(Duration::from_millis(300)); // grace period
        for child in kids.iter_mut() {
            let _ = child.kill(); // SIGKILL survivors
            let _ = child.wait();
        }
        kids.clear();
        *self.spawned_python.lock().unwrap() = false;
        if let Ok(mut s) = self.started.lock() {
            *s = false;
        }
    }
}

/// Start the stack in a background thread and emit `stack-ready` when up.
pub fn spawn_startup(app: AppHandle) {
    std::thread::spawn(move || {
        let status = app.state::<SidecarManager>().start();
        let _ = app.emit("stack-ready", status);
    });
}

// ── tauri commands ───────────────────────────────────────────────────────────
#[tauri::command]
pub fn start_stack(mgr: tauri::State<SidecarManager>) -> StackStatus {
    mgr.start()
}

#[tauri::command]
pub fn stop_stack(mgr: tauri::State<SidecarManager>) {
    mgr.stop_all();
}

#[tauri::command]
pub fn stack_health(mgr: tauri::State<SidecarManager>) -> StackStatus {
    mgr.status()
}
