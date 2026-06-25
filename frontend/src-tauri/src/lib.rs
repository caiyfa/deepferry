use std::sync::Mutex;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

const SIDECAR_ARGS: &[&str] = &["--transport", "http", "--port", "8000"];

struct SidecarState(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState(Mutex::new(None)))
        .setup(|app| {
            // The bundled sidecar binary only exists in release builds.
            // During `tauri dev` the Python sidecar is started separately:
            //   uv run deepferry mcp-server --transport http --port 8000
            #[cfg(not(debug_assertions))]
            {
                let sidecar = app
                    .shell()
                    .sidecar("python-sidecar")
                    .expect("python-sidecar binary not found in bundle")
                    .args(SIDECAR_ARGS)
                    .spawn()
                    .expect("failed to spawn python sidecar");
                let state = app.state::<SidecarState>();
                *state.0.lock().unwrap() = Some(sidecar.child);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                let state = app_handle.state::<SidecarState>();
                if let Some(child) = state.0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
