// Hermes Studio — Tauri 2 desktop appliance. The shell window hosts the React
// shell (first-run / projects / settings); when a project is opened, the full
// hermes-max web UI loads in a webview pointed at the Python backend that this
// process sidecars. The user never touches a terminal.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;
mod detect;

use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(sidecar::SidecarManager::default())
        .setup(|app| {
            // Start the Python sidecar (+ MCP servers) in the background and emit
            // `stack-ready` once /healthz answers; the loading screen waits on it.
            sidecar::spawn_startup(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar::start_stack,
            sidecar::stop_stack,
            sidecar::stack_health,
            detect::probe_capabilities,
            detect::probe_endpoint,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Hermes Studio")
        .run(|app, event| match event {
            // No orphan sidecars survive the app — SIGTERM the group, then SIGKILL.
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                if let Some(mgr) = app.try_state::<sidecar::SidecarManager>() {
                    mgr.stop_all();
                }
            }
            _ => {}
        });
}
