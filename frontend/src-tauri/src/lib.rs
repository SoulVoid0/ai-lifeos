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
use tauri::{AppHandle, Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
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

/// Live wallpaper: a borderless window showing the crew (the web UI's #/wallpaper page), placed behind the
/// desktop icons on Windows (the WorkerW trick wallpaper engines use) and kept at the bottom elsewhere.
#[tauri::command]
fn set_wallpaper(app: AppHandle, on: bool) -> Result<bool, String> {
    if let Some(w) = app.get_webview_window("wallpaper") {
        if on {
            return Ok(true);
        }
        let _ = w.destroy();
        #[cfg(windows)]
        desktop::refresh_wallpaper();
        return Ok(false);
    }
    if !on {
        return Ok(false);
    }
    let monitor = app
        .get_webview_window("main")
        .and_then(|w| w.primary_monitor().ok().flatten())
        .ok_or("No monitor found")?;
    let size = monitor.size();
    let scale = monitor.scale_factor();
    let win = WebviewWindowBuilder::new(&app, "wallpaper", WebviewUrl::App("index.html#/wallpaper".into()))
        .title("AI LifeOS wallpaper")
        .decorations(false)
        .resizable(false)
        .skip_taskbar(true)
        .focused(false)
        .always_on_bottom(true)
        .position(0.0, 0.0)
        .inner_size(size.width as f64 / scale, size.height as f64 / scale)
        .visible(true)
        .build()
        .map_err(|e| e.to_string())?;
    #[cfg(windows)]
    {
        let hwnd = win.hwnd().map_err(|e| e.to_string())?;
        desktop::attach(hwnd.0 as isize, size.width as i32, size.height as i32)?;
    }
    #[cfg(not(windows))]
    let _ = win;
    Ok(true)
}

#[cfg(windows)]
mod desktop {
    //! Behind the desktop icons: ask Progman to create its WorkerW layer (message 0x052C), then make our window a
    //! child of it. Windows 11 24H2 moved that layer inside Progman, so both layouts are handled.
    use std::ffi::c_void;

    type Hwnd = isize;
    #[link(name = "user32")]
    extern "system" {
        fn FindWindowW(class: *const u16, title: *const u16) -> Hwnd;
        fn FindWindowExW(parent: Hwnd, after: Hwnd, class: *const u16, title: *const u16) -> Hwnd;
        fn SendMessageTimeoutW(h: Hwnd, msg: u32, w: usize, l: isize, flags: u32, timeout: u32, result: *mut usize) -> isize;
        fn EnumWindows(cb: extern "system" fn(Hwnd, isize) -> i32, l: isize) -> i32;
        fn SetParent(child: Hwnd, parent: Hwnd) -> Hwnd;
        fn SetWindowPos(h: Hwnd, after: Hwnd, x: i32, y: i32, cx: i32, cy: i32, flags: u32) -> i32;
        fn GetWindowLongPtrW(h: Hwnd, index: i32) -> isize;
        fn SetWindowLongPtrW(h: Hwnd, index: i32, value: isize) -> isize;
        fn GetSystemMetrics(index: i32) -> i32;
        fn SystemParametersInfoW(action: u32, param: u32, pv: *mut c_void, winini: u32) -> i32;
    }
    const GWL_STYLE: i32 = -16;
    const WS_CHILD: u32 = 0x4000_0000;
    const WS_POPUP: u32 = 0x8000_0000;
    const WS_CAPTION: u32 = 0x00C0_0000;
    const WS_THICKFRAME: u32 = 0x0004_0000;
    const SWP_NOACTIVATE: u32 = 0x0010;
    const SWP_SHOWWINDOW: u32 = 0x0040;
    const SWP_NOMOVE: u32 = 0x0002;
    const SWP_NOSIZE: u32 = 0x0001;

    fn w(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    static mut BEHIND_ICONS: Hwnd = 0;

    extern "system" fn find_worker(top: Hwnd, _: isize) -> i32 {
        unsafe {
            let defview = FindWindowExW(top, 0, w("SHELLDLL_DefView").as_ptr(), std::ptr::null());
            if defview != 0 {
                // Classic layout: the WorkerW that comes right after the icons' window.
                BEHIND_ICONS = FindWindowExW(0, top, w("WorkerW").as_ptr(), std::ptr::null());
            }
        }
        1
    }

    pub fn attach(hwnd: Hwnd, width: i32, height: i32) -> Result<(), String> {
        unsafe {
            let progman = FindWindowW(w("Progman").as_ptr(), std::ptr::null());
            if progman == 0 {
                return Err("The desktop (Progman) wasn't found".into());
            }
            let mut res = 0usize;
            SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, &mut res);
            SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, &mut res);
            BEHIND_ICONS = 0;
            EnumWindows(find_worker, 0);
            let style = GetWindowLongPtrW(hwnd, GWL_STYLE) as u32;
            let child = (style & !(WS_POPUP | WS_CAPTION | WS_THICKFRAME)) | WS_CHILD;
            SetWindowLongPtrW(hwnd, GWL_STYLE, child as isize);
            // Child coordinates start at the virtual screen's top left; the primary monitor is at 0,0 on screen.
            let (x, y) = (-GetSystemMetrics(76), -GetSystemMetrics(77));
            let inner_worker = FindWindowExW(progman, 0, w("WorkerW").as_ptr(), std::ptr::null());
            if BEHIND_ICONS != 0 && inner_worker == 0 {
                SetParent(hwnd, BEHIND_ICONS);
                SetWindowPos(hwnd, 0, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW);
            } else {
                // Windows 11 24H2: icons (SHELLDLL_DefView) and WorkerW are both inside Progman; go between them.
                let defview = FindWindowExW(progman, 0, w("SHELLDLL_DefView").as_ptr(), std::ptr::null());
                SetParent(hwnd, progman);
                SetWindowPos(hwnd, 0, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW);
                if defview != 0 {
                    SetWindowPos(hwnd, defview, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE);
                }
                if inner_worker != 0 {
                    SetWindowPos(inner_worker, hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE);
                }
            }
        }
        Ok(())
    }

    /// After the wallpaper window closes, have Windows repaint the normal wallpaper.
    pub fn refresh_wallpaper() {
        unsafe {
            let mut buf = [0u16; 260];
            // SPI_GETDESKWALLPAPER, then SPI_SETDESKWALLPAPER with the same file (SPIF_SENDCHANGE).
            if SystemParametersInfoW(0x0073, buf.len() as u32, buf.as_mut_ptr() as *mut c_void, 0) != 0 {
                SystemParametersInfoW(0x0014, 0, buf.as_mut_ptr() as *mut c_void, 0x02);
            }
        }
    }
}

/// Ctrl+Shift+Space: grab the screen before LifeOS comes to the front, then open the "ask about the screen" box.
fn screen_hotkey(app: &AppHandle) {
    let handle = app.clone();
    std::thread::spawn(move || {
        let _ = http("POST", "/api/screen/capture", 8000);
        show_main(&handle);
        let _ = handle.emit("screen-ask", ());
    });
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
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        screen_hotkey(app);
                    }
                })
                .build(),
        )
        .manage(PendingUpdate(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![app_version, check_update, install_update, set_wallpaper])
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
            // Another program may own the shortcut already; LifeOS works without it.
            let _ = app.global_shortcut().register(Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space));
            if !std::env::args().any(|a| a == MINIMIZED_FLAG) {
                show_main(app.handle());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window keeps LifeOS (and the crew's missions and automations) running in the tray.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
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
