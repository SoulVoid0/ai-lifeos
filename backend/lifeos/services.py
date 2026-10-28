"""Helper programs LifeOS can start for you, hidden: the Ollama server and ComfyUI.

Both are only started when they aren't already answering, and only programs LifeOS started itself are
stopped again when it exits. On Windows they run without a console window; their output goes to log files
in the LifeOS data folder.
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from . import activity, config, settings

log = logging.getLogger("lifeos.services")

# Common places for the comfyui-rocm / ComfyUI-Zluda / portable launchers, checked when no launcher is set.
COMFY_CANDIDATES = [
    rf"{d}:\{folder}\{bat}" for d in "ACDE" for folder in ("comfyui-rocm", "ComfyUI-Zluda", "ComfyUI_windows_portable")
    for bat in ("comfyui-user.bat", "comfyui-rocm.bat", "comfyui-n.bat", "comfyui.bat", "run_nvidia_gpu.bat")
]
LAUNCHER_TYPES = (".bat", ".cmd", ".exe", ".ps1", ".sh")

_lock = threading.Lock()
_started: dict[str, subprocess.Popen] = {}
_state: dict[str, str] = {"comfyui": "", "ollama": ""}


def _hidden() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _spawn(name: str, args: list[str], cwd: Path | None = None) -> subprocess.Popen:
    logfile = open(config.data_dir() / f"{name}.log", "ab")  # noqa: SIM115 (kept open for the child)
    proc = subprocess.Popen(args, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL, stdout=logfile,
                            stderr=subprocess.STDOUT, **_hidden())
    _started[name] = proc
    return proc


def _wait(check, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(1.0)
    return False


# ---------------------------------------------------------------- Ollama

def ollama_up() -> bool:
    try:
        return httpx.get(f"{settings.get('ollama_url').rstrip('/')}/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def ollama_binary() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        guess = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if guess.is_file():
            return str(guess)
    return None


def ensure_ollama(wait: float = 20) -> bool:
    """Start `ollama serve` hidden if Ollama isn't running (and auto-start is on). Returns whether it's up."""
    if ollama_up():
        return True
    url = settings.get("ollama_url")
    if not settings.get("auto_start_ollama") or not any(h in url for h in ("127.0.0.1", "localhost")):
        return False
    binary = ollama_binary()
    if not binary:
        _state["ollama"] = "Ollama isn't installed (https://ollama.com/download)"
        return False
    with _lock:
        proc = _started.get("ollama")
        if not proc or proc.poll() is not None:
            log.info("Starting Ollama: %s serve", binary)
            _spawn("ollama", [binary, "serve"])
            activity.log("system", "Started Ollama in the background")
    ok = _wait(ollama_up, wait)
    _state["ollama"] = "" if ok else "Ollama didn't start; see ollama.log in the LifeOS data folder"
    return ok


# ---------------------------------------------------------------- ComfyUI

def comfy_launcher() -> Path | None:
    from . import media

    configured = media.cfg().get("comfy_launcher", "").strip()
    if configured:
        p = Path(configured).expanduser()
        return p if p.is_file() else None
    if os.name != "nt":
        return None
    return next((Path(c) for c in COMFY_CANDIDATES if Path(c).is_file()), None)


def comfy_up() -> bool:
    from . import media

    try:
        return httpx.get(f"{media.cfg()['comfy_url'].rstrip('/')}/system_stats", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_comfy(wait: float = 240) -> bool:
    """Start ComfyUI hidden with its launcher if it isn't answering. Blocks until it's ready or `wait` runs out."""
    from . import media

    if comfy_up():
        return True
    c = media.cfg()
    if c["backend"] != "comfyui" or not any(h in c["comfy_url"] for h in ("127.0.0.1", "localhost")):
        return False
    launcher = comfy_launcher()
    if not launcher:
        _state["comfyui"] = ("Set the ComfyUI launcher (for example A:\\comfyui-rocm\\comfyui-user.bat) under "
                             "Gallery → Backend so LifeOS can start it")
        return False
    if launcher.suffix.lower() not in LAUNCHER_TYPES:
        _state["comfyui"] = "The ComfyUI launcher must be a .bat, .cmd, .ps1, .sh or .exe file"
        return False
    with _lock:
        proc = _started.get("comfyui")
        if not proc or proc.poll() is not None:
            ext = launcher.suffix.lower()
            if ext in (".bat", ".cmd"):
                args = ["cmd.exe", "/c", str(launcher)]
            elif ext == ".ps1":
                args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher)]
            elif ext == ".sh":
                args = ["bash", str(launcher)]
            else:
                args = [str(launcher)]
            log.info("Starting ComfyUI: %s", args)
            _spawn("comfyui", args, cwd=launcher.parent)
            activity.log("system", f"Started ComfyUI in the background ({launcher.name})")
            _state["comfyui"] = "starting"
    ok = _wait(comfy_up, wait)
    _state["comfyui"] = "" if ok else "ComfyUI didn't answer in time; see comfyui.log in the LifeOS data folder"
    return ok


def status() -> dict[str, Any]:
    launcher = comfy_launcher()
    return {
        "ollama": {"up": ollama_up(), "started_by_lifeos": _alive("ollama"), "binary": ollama_binary(),
                   "note": _state["ollama"]},
        "comfyui": {"up": comfy_up(), "started_by_lifeos": _alive("comfyui"),
                    "launcher": str(launcher) if launcher else None, "note": _state["comfyui"]},
    }


def _alive(name: str) -> bool:
    p = _started.get(name)
    return bool(p and p.poll() is None)


def start_background() -> None:
    """Called at startup: bring up Ollama, and ComfyUI too if it's set to start with LifeOS."""
    from . import media

    def run() -> None:
        try:
            ensure_ollama()
            if media.cfg().get("comfy_autostart"):
                ensure_comfy()
        except Exception:  # never take the backend down over a helper program
            log.exception("Starting helper programs failed")

    threading.Thread(target=run, name="lifeos-services", daemon=True).start()


def stop_all() -> None:
    """Stop only what LifeOS started (the whole process tree, since .bat launchers start python children)."""
    for name, proc in list(_started.items()):
        if proc.poll() is not None:
            continue
        log.info("Stopping %s (pid %s)", name, proc.pid)
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(proc.pid, 15)
        except (OSError, subprocess.SubprocessError):
            proc.kill()
    _started.clear()


atexit.register(stop_all)
