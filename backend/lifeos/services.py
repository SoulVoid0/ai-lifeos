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


def user_env() -> dict[str, str]:
    """This process's environment plus the user's saved variables (setx / System Properties) it doesn't have.

    Programs started from a window that was open before a variable was saved don't see it, which would make
    Ollama look for models in the wrong folder (OLLAMA_MODELS)."""
    env = dict(os.environ)
    if os.name != "nt":
        return env
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            i = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if name.upper() != "PATH" and name not in env and isinstance(value, str):
                    env[name] = os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value
    except OSError:
        pass
    return env


BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def _win_pids(prefixes: tuple[str, ...]) -> list[int]:
    """Windows: ids of the processes whose exe name starts with one of prefixes (lowercase)."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = _kernel32()
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    pids = []
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower().startswith(prefixes):
                pids.append(int(e.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return pids


_k32: Any = None


def _kernel32() -> Any:
    """kernel32 with argument types declared, so 64-bit handles aren't squeezed into C ints."""
    global _k32
    if _k32 is None:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        H, D, P = wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p
        for name, args, res in (("CreateToolhelp32Snapshot", [D, D], H), ("Process32FirstW", [H, P], wintypes.BOOL),
                                ("Process32NextW", [H, P], wintypes.BOOL), ("CloseHandle", [H], wintypes.BOOL),
                                ("OpenProcess", [D, wintypes.BOOL, D], H), ("GetPriorityClass", [H], D),
                                ("SetPriorityClass", [H, D], wintypes.BOOL),
                                ("GetProcessTimes", [H, P, P, P, P], wintypes.BOOL)):
            fn = getattr(k, name)
            fn.argtypes, fn.restype = args, res
        _k32 = k
    return _k32


def _win_open(pid: int, access: int) -> Any:
    k32 = _kernel32()
    return k32, k32.OpenProcess(access, False, pid)


def lower_priority(prefixes: tuple[str, ...]) -> int:
    """Windows: put every process whose exe name starts with one of prefixes at below-normal CPU priority, so a model
    running on the CPU doesn't starve the desktop. Returns how many were changed; does nothing elsewhere."""
    if os.name != "nt":
        return 0
    changed = 0
    for pid in _win_pids(prefixes):
        k32, h = _win_open(pid, 0x0200 | 0x1000)  # SET_INFORMATION | QUERY_LIMITED_INFORMATION
        if h:
            if k32.GetPriorityClass(h) != BELOW_NORMAL_PRIORITY_CLASS and k32.SetPriorityClass(h, BELOW_NORMAL_PRIORITY_CLASS):
                changed += 1
            k32.CloseHandle(h)
    return changed


def _cpu_seconds(prefixes: tuple[str, ...]) -> float:
    import ctypes
    from ctypes import wintypes

    total = 0
    for pid in _win_pids(prefixes):
        k32, h = _win_open(pid, 0x1000)
        if not h:
            continue
        c, x, kern, user = (wintypes.FILETIME() for _ in range(4))
        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x), ctypes.byref(kern), ctypes.byref(user)):
            for ft in (kern, user):
                total += (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        k32.CloseHandle(h)
    return total / 1e7  # 100 ns units


def ollama_busy(seconds: float = 2.0) -> bool:
    """Windows: whether Ollama is still crunching (over one core of CPU for a couple of seconds). False elsewhere."""
    if os.name != "nt":
        return False
    before = _cpu_seconds(("ollama", "llama"))
    time.sleep(seconds)
    return _cpu_seconds(("ollama", "llama")) - before > seconds


def restart_ollama() -> bool:
    """Hard stop: end Ollama (server and model runners) and start it again. Used when Stop didn't make it let go."""
    if os.name == "nt":
        for image in ("ollama.exe", "ollama_llama_server.exe", "llama-server.exe"):
            subprocess.run(["taskkill", "/F", "/T", "/IM", image], capture_output=True, timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        proc = _started.get("ollama")
        if not proc or proc.poll() is not None:
            return False  # not ours to kill
        proc.kill()
    activity.log("system", "Restarted Ollama to stop work that kept running after Stop")
    time.sleep(1)
    return ensure_ollama(wait=30)


def _spawn(name: str, args: list[str], cwd: Path | None = None) -> subprocess.Popen:
    logfile = open(config.data_dir() / f"{name}.log", "ab")  # noqa: SIM115 (kept open for the child)
    proc = subprocess.Popen(args, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL, stdout=logfile,
                            stderr=subprocess.STDOUT, env=user_env(), **_hidden())
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
        guesses = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"]
        guesses += [Path(rf"{d}:\Ollama\ollama.exe") for d in "ACDE"]
        return next((str(g) for g in guesses if g.is_file()), None)
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
