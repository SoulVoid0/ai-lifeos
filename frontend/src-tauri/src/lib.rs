//! Desktop shell: opens the UI and makes sure the Python backend is running.
//!
//! On start it checks http://127.0.0.1:8765/api/health. If nothing answers it launches the
//! backend (a bundled `lifeos-backend` executable next to the app if present, otherwise
//! `python -m lifeos` from the repo's backend/ folder) and stops it again on exit. The backend
//! also watches LIFEOS_PARENT_PID, so it exits if the shell is killed without a clean shutdown.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const BACKEND_ADDR: &str = "127.0.0.1:8765";

struct Backend(Mutex<Option<Child>>);

fn backend_healthy() -> bool {
    let addr: SocketAddr = BACKEND_ADDR.parse().expect("valid address");
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let request = format!("GET /api/health HTTP/1.1\r\nHost: {BACKEND_ADDR}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buf = String::new();
    let _ = stream.read_to_string(&mut buf);
    buf.starts_with("HTTP/1.1 200")
}

fn spawn_backend() -> Option<Child> {
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let sidecar = exe_dir.join(if cfg!(windows) { "lifeos-backend.exe" } else { "lifeos-backend" });
    let parent_pid = std::process::id().to_string();
    if sidecar.exists() {
        return Command::new(sidecar).env("LIFEOS_PARENT_PID", &parent_pid).spawn().ok();
    }
    let python = std::env::var("LIFEOS_PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
    let backend_dir = std::env::var("LIFEOS_BACKEND_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend"));
    Command::new(python)
        .args(["-m", "lifeos"])
        .current_dir(backend_dir)
        .env("LIFEOS_PARENT_PID", &parent_pid)
        .spawn()
        .ok()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let child = if backend_healthy() { None } else { spawn_backend() };
            if child.is_some() {
                // Give uvicorn a moment; the UI also shows a banner until the backend answers.
                for _ in 0..40 {
                    if backend_healthy() {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(150));
                }
            }
            app.manage(Backend(Mutex::new(child)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building AI LifeOS");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Backend>().0.lock().ok().and_then(|mut c| c.take()) {
                let _ = child.kill();
            }
        }
    });
}
