// Per-launch shared secret (Phase 1.4) — the DNS-rebinding hardening that works
// on Linux, where the tauri:// origin sends a BLANK Origin header (wry #366) so
// Origin allowlisting can't distinguish Studio from an attacker page.
//
// Rust mints a fresh secret each launch, writes it to ~/.hermes-max/studio.secret
// (0600), and injects it into any sidecar it spawns. Control POSTs from Rust
// carry it as X-HMX-Secret; the Python server requires a matching secret on
// control endpoints. The FILE (not just env) is the source of truth so an
// ADOPTED server (started by `hm ui` before Studio) can validate too — it reads
// the same file. A DNS-rebinding attacker can't read a local 0600 file, so the
// loopback server stays safe without Origin allowlisting.
use std::path::PathBuf;
use std::sync::OnceLock;

static SECRET: OnceLock<String> = OnceLock::new();

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

pub fn path() -> PathBuf {
    home().join(".hermes-max").join("studio.secret")
}

fn mint() -> String {
    // 16 bytes of OS entropy → 32 hex chars. No RNG crate: read /dev/urandom.
    #[cfg(unix)]
    {
        use std::io::Read;
        if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
            let mut b = [0u8; 16];
            if f.read_exact(&mut b).is_ok() {
                return b.iter().map(|x| format!("{x:02x}")).collect();
            }
        }
    }
    // weak fallback (the file is still 0600); never reached on a healthy Linux box
    format!(
        "{:x}{:x}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    )
}

/// Mint-once-per-process, persist (0600), and return the secret.
pub fn ensure() -> String {
    SECRET
        .get_or_init(|| {
            let s = mint();
            let p = path();
            if let Some(dir) = p.parent() {
                let _ = std::fs::create_dir_all(dir);
            }
            if std::fs::write(&p, &s).is_ok() {
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o600));
                }
            }
            s
        })
        .clone()
}
