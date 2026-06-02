// System tray — keeps Studio alive in the background for walk-away builds. The
// window can be closed (hidden) while the sidecar keeps building; the tray
// tooltip reflects build state, and clicking the icon brings the window back.
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};

pub const TRAY_ID: &str = "main";

fn show(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "tray_open", "Open", true, None::<&str>)?;
    let newp = MenuItem::with_id(app, "tray_new", "New Project…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "tray_quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &newp, &quit])?;

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip("Hermes Studio — idle")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "tray_open" => show(app),
            "tray_new" => {
                show(app);
                let _ = app.emit("tray-new-project", ());
            }
            "tray_quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show(tray.app_handle());
            }
        });

    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
    }
    builder.build(app)?;
    Ok(())
}

pub fn set_tooltip(app: &AppHandle, text: &str) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(text));
    }
}

/// Phase 5.3 — encode build state in the tray icon GLYPH (a coloured dot), not
/// just the tooltip: Linux libayatana has no hover/leave events, so the tooltip
/// alone is invisible until clicked. building=gold · needs-you=amber · done=green
/// · idle=slate.
pub fn set_state(app: &AppHandle, state: &str) {
    let (r, g, b) = match state {
        "building" => (217u8, 165, 33),
        "needs" => (230, 194, 0),
        "done" => (95, 215, 95),
        _ => (120, 120, 130),
    };
    let (w, h) = (22u32, 22u32);
    let (cx, cy, rad) = (11.0f64, 11.0, 9.0);
    let mut px = vec![0u8; (w * h * 4) as usize];
    for y in 0..h {
        for x in 0..w {
            let dx = x as f64 - cx;
            let dy = y as f64 - cy;
            if dx * dx + dy * dy <= rad * rad {
                let i = ((y * w + x) * 4) as usize;
                px[i] = r; px[i + 1] = g; px[i + 2] = b; px[i + 3] = 255;
            }
        }
    }
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_icon(Some(tauri::image::Image::new_owned(px, w, h)));
    }
}
