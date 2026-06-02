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
    children: Mutex<Vec<Child>>,
    started: Mutex<bool>,
}

#[derive(Serialize, Clone)]
pub struct StackStatus {
    pub python_server: bool,
    pub mcp_servers: Vec<(String, bool)>,
    pub hermes_present: bool,
    pub active_run: Option<String>,
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

/// Repo root containing `ui/server`: env override, then walk up from the binary,
/// then a compile-time dev fallback (studio/src-tauri -> repo root).
pub fn repo_root() -> PathBuf {
    if let Ok(r) = std::env::var("HERMES_MAX_ROOT") {
        let p = PathBuf::from(r);
        if p.join("ui").join("server").exists() {
            return p;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        while let Some(d) = dir {
            if d.join("ui").join("server").exists() {
                return d;
            }
            dir = d.parent().map(|p| p.to_path_buf());
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."))
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

    /// Start the stack (idempotent). Returns once /healthz answers or 5s elapse.
    pub fn start(&self) -> StackStatus {
        let mut started = self.started.lock().unwrap();
        if !*started {
            let root = repo_root();
            // 1. Python backend (only if not already serving, e.g. `hm ui` running)
            if !healthz_ok() {
                if let Ok(child) = spawn_logged(
                    Command::new("python3")
                        .args(["-m", "ui.server", "--no-open", "--port", &PORT.to_string()])
                        .current_dir(&root)
                        .env("PYTHONPATH", &root),
                    "python-server.log",
                ) {
                    self.children.lock().unwrap().push(child);
                }
            }
            // 2. poll health up to ~5s
            for _ in 0..25 {
                if healthz_ok() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
            // 3. MCP servers via `hm dev` (best-effort; only if hm is on PATH)
            if which("hm").is_some() {
                if let Ok(child) = spawn_logged(Command::new("hm").arg("dev").current_dir(&root), "mcp.log") {
                    self.children.lock().unwrap().push(child);
                }
            }
            *started = true;
        }
        self.status()
    }

    pub fn status(&self) -> StackStatus {
        let mcp = (9101..=9115u16).map(|p| (format!(":{p}"), tcp_open(p))).collect();
        StackStatus {
            python_server: healthz_ok(),
            mcp_servers: mcp,
            hermes_present: self.hermes_present(),
            active_run: None,
        }
    }

    pub fn stop_all(&self) {
        let mut kids = self.children.lock().unwrap();
        for child in kids.iter() {
            term_group(child.id());
        }
        std::thread::sleep(Duration::from_millis(300)); // grace period
        for child in kids.iter_mut() {
            let _ = child.kill(); // SIGKILL survivors
            let _ = child.wait();
        }
        kids.clear();
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
