// Native notifications (tauri-plugin-notification → libnotify on Linux). Fired
// from the workshop livelog tailer on milestone events, gated by per-event
// settings (studio.conf) and, for some, by whether the window is focused — so a
// walk-away build pings you when it's done or needs a decision.
use serde_json::Value;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

pub struct NotifyPrefs {
    pub master: bool,
    pub complete: bool,
    pub attention: bool,
    pub conductor: bool,
}

pub fn prefs() -> NotifyPrefs {
    let s = crate::config::load().settings;
    let b = |k: &str| s.get(k).and_then(Value::as_bool).unwrap_or(true);
    NotifyPrefs {
        master: b("notifications"),
        complete: b("notify_complete"),
        attention: b("notify_attention"),
        conductor: b("notify_conductor"),
    }
}

pub fn focused(app: &AppHandle) -> bool {
    app.get_webview_window("main")
        .and_then(|w| w.is_focused().ok())
        .unwrap_or(false)
}

pub fn send(app: &AppHandle, title: &str, body: &str) {
    if !prefs().master {
        return;
    }
    let _ = app.notification().builder().title(title).body(body).show();
}
