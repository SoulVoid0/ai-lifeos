//! Desktop shell: a tray app that runs LifeOS without any terminal windows.
//!
//! On start it checks http://127.0.0.1:8765/api/health. If nothing answers it launches the backend hidden
//! (the bundled `lifeos-backend` sidecar next to the app if present, otherwise `python -m lifeos` from the
//! repo's backend/ folder) with its output in a log file. The backend in turn starts Ollama and, when needed,
//! ComfyUI, also hidden. Closing the window keeps LifeOS running in the tray; Quit in the tray menu asks the
//! backend to stop the helper programs it started, then exits. The backend also watches LIFEOS_PARENT_PID,
//! so it exits if the shell is killed without a clean shutdown.
//!
//! Updates: the app asks the backend for the newest GitHub release's manifest (the backend adds the read
//! token a private repository needs and relays the download). The installer must carry a valid signature for
//! the public key compiled in from LIFEOS_UPDATER_PUBKEY, which the release workflow sets; builds without it
//! can't update themselves. Installing stops the backend first so its files can be replaced, then restarts.

use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_updater::{Update, UpdaterExt};

const BACKEND_ADDR: &str = "127.0.0.1:8765";
/// Passed when Windows starts LifeOS at sign-in: stay in the tray instead of opening the window.
const MINIMIZED_FLAG: &str = "--minimized";

/// Set by the release workflow; empty in local builds.
const UPDATER_PUBKEY: &str = match option_env!("LIFEOS_UPDATER_PUBKEY") {
    Some(k) => k,
    None => "",
};

struct Backend(Mutex<Option<Child>>);
struct PendingUpdate(Mutex<Option<Update>>);

#[derive(Clone, serde::Serialize)]
struct UpdateInfo {
    version: String,
    current: String,
    notes: Option<String>,
}

#[tauri::command]
fn app_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

#[tauri::command]
async fn check_update(app: AppHandle) -> Result<Option<UpdateInfo>, String> {
    if UPDATER_PUBKEY.trim().is_empty() {
        return Err("This copy was built on your PC, so it can't update itself. Install it once from the GitHub release.".into());
    }
    let handle = app.clone();
    let endpoint = format!("http://{BACKEND_ADDR}/api/system/update/manifest").parse().map_err(|e| format!("{e}"))?;
    let update = app
        .updater_builder()
        .endpoints(vec![endpoint])
        .and_then(|b| b.header("X-LifeOS-Client", "1"))
        .map_err(|e| e.to_string())?
        // Windows: the installer replaces the backend's files, so stop it (and what it started) first.
        .on_before_exit(move || stop_backend(&handle))
        .build()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?;
    let info = update.as_ref().map(|u| UpdateInfo { version: u.version.clone(), current: u.current_version.clone(), notes: u.body.clone() });
    *app.state::<PendingUpdate>().0.lock().map_err(|e| e.to_string())? = update;
    Ok(info)
}

#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    let update = app.state::<PendingUpdate>().0.lock().map_err(|e| e.to_string())?.take();
    let update = update.ok_or("No update is ready; check again")?;
    let mut done: u64 = 0;
    let progress = app.clone();
    update
        .download_and_install(
            move |chunk, total| {
                done += chunk as u64;
                let _ = progress.emit("update-progress", (done, total));
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;
    // Windows exits inside install() and the installer starts the new version; elsewhere restart here.
    app.restart();
}

fn http(method: &str, path: &str, timeout_ms: u64) -> Option<String> {
    let addr: SocketAddr = BACKEND_ADDR.parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(300)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(timeout_ms)));
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {BACKEND_ADDR}\r\nX-LifeOS-Client: 1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut buf = String::new();
    let _ = stream.read_to_string(&mut buf);
    Some(buf)
}

fn backend_healthy() -> bool {
    http("GET", "/api/health", 800).is_some_and(|r| r.starts_with("HTTP/1.1 200"))
}

/// Run a child without a console window on Windows.
fn hide(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

fn spawn_backend(log_dir: Option<PathBuf>) -> Option<Child> {
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let sidecar = exe_dir.join(if cfg!(windows) { "lifeos-backend.exe" } else { "lifeos-backend" });
    let parent_pid = std::process::id().to_string();
    let mut cmd = if sidecar.exists() {
        Command::new(sidecar)
    } else {
        let python = std::env::var("LIFEOS_PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
        let backend_dir = std::env::var("LIFEOS_BACKEND_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend"));
        let mut c = Command::new(python);
        c.args(["-m", "lifeos"]).current_dir(backend_dir);
        c
    };
    cmd.env("LIFEOS_PARENT_PID", &parent_pid).stdin(Stdio::null());
    let log = log_dir.and_then(|dir| {
        std::fs::create_dir_all(&dir).ok()?;
        OpenOptions::new().create(true).append(true).open(dir.join("backend.log")).ok()
    });
    match log.and_then(|f| f.try_clone().ok().map(|g| (f, g))) {
        Some((out, err)) => cmd.stdout(out).stderr(err),
        None => cmd.stdout(Stdio::null()).stderr(Stdio::null()),
    };
    hide(&mut cmd).spawn().ok()
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn stop_backend(app: &AppHandle) {
    let Some(mut child) = app.state::<Backend>().0.lock().ok().and_then(|mut c| c.take()) else {
        return;
    };
    // Let the backend stop ComfyUI/Ollama if it started them, then make sure it's gone.
    let _ = http("POST", "/api/system/shutdown", 3000);
    for _ in 0..30 {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open AI LifeOS", true, None::<&str>)?;
    let comfy = MenuItem::with_id(app, "comfy", "Start ComfyUI now", true, None::<&str>)?;
    let update = MenuItem::with_id(app, "update", "Check for updates", true, None::<&str>)?;
    let autostart_on = app.autolaunch().is_enabled().unwrap_or(false);
    let autostart = CheckMenuItem::with_id(app, "autostart", "Start with Windows", true, autostart_on, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit AI LifeOS", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &comfy, &update, &autostart, &sep, &quit])?;
    let toggle = autostart.clone();
    let mut tray = TrayIconBuilder::with_id("lifeos")
        .tooltip("AI LifeOS")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => show_main(app),
            "comfy" => {
                std::thread::spawn(|| {
                    let _ = http("POST", "/api/system/services/comfyui/start", 300_000);
                });
            }
            "update" => {
                // The window shows the result (and the Install & restart button).
                show_main(app);
                let _ = app.emit("check-updates", ());
            }
            "autostart" => {
                let manager = app.autolaunch();
                let on = manager.is_enabled().unwrap_or(false);
                let _ = if on { manager.disable() } else { manager.enable() };
                let _ = toggle.set_checked(manager.is_enabled().unwrap_or(!on));
            }
            "quit" => {
                stop_backend(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        });
    // A dedicated 32px PNG keeps the tray icon crisp instead of a downscaled .ico frame.
    if let Ok(icon) = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png")) {
        tray = tray.icon(icon);
    } else if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        // A second launch (shortcut, Start menu) just brings the running window forward.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![MINIMIZED_FLAG])))
        .plugin(if UPDATER_PUBKEY.trim().is_empty() {
            tauri_plugin_updater::Builder::new().build()
        } else {
            tauri_plugin_updater::Builder::new().pubkey(UPDATER_PUBKEY.trim()).build()
        })
        .manage(PendingUpdate(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![app_version, check_update, install_update])
        .setup(|app| {
            let log_dir = app.path().app_log_dir().ok();
            let child = if backend_healthy() { None } else { spawn_backend(log_dir) };
            if child.is_some() {
                // Give uvicorn a moment; the UI also shows a banner until the backend answers.
                for _ in 0..60 {
                    if backend_healthy() {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(150));
                }
            }
            app.manage(Backend(Mutex::new(child)));
            build_tray(app.handle())?;
            if !std::env::args().any(|a| a == MINIMIZED_FLAG) {
                show_main(app.handle());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window keeps LifeOS (and the crew's missions and automations) running in the tray.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building AI LifeOS");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}
