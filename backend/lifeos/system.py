"""The computer itself: status (CPU, memory, disk, battery), running programs, volume and power.

The platform layer of LifeOS as a shell. Each action has a Windows, Linux and macOS implementation behind
one function, so the same assistant tools work on the desktop app today and on a LifeOS-based Linux system later.
Ending programs and power actions are "destructive" tools: they always ask first.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Any, Literal

import psutil

from . import activity, config

PowerAction = Literal["lock", "sleep", "restart", "shutdown", "sign_out"]
POWER_LABEL = {"lock": "Lock the screen", "sleep": "Put the computer to sleep", "restart": "Restart the computer",
               "shutdown": "Shut down the computer", "sign_out": "Sign out"}
# Programs that keep the system (or LifeOS) alive; ending them crashes the session.
PROTECTED = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "memory compression", "secure system", "lsaiso.exe",
    "systemd", "init", "kthreadd", "launchd", "kernel_task", "windowserver", "loginwindow", "xorg", "xwayland",
    "gnome-shell", "kwin_wayland", "kwin_x11", "plasmashell", "sddm", "gdm", "lightdm", "dbus-daemon", "pipewire",
    "lifeos-backend.exe", "lifeos-backend", "ai-lifeos.exe", "ai lifeos.exe", "ollama.exe", "ollama",
}


def _run(argv: list[str], timeout: int = 10) -> None:
    flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    subprocess.run(argv, check=True, timeout=timeout, capture_output=True, **flags)


# ---------------------------------------------------------------- status

def status() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    data_drive = str(config.data_dir().anchor or "/") if sys.platform == "win32" else "/"
    disk = psutil.disk_usage(data_drive)
    out: dict[str, Any] = {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count() or 1,
        "memory": {"used_gb": round(mem.used / 2**30, 1), "total_gb": round(mem.total / 2**30, 1), "percent": mem.percent},
        "disk": {"path": data_drive, "free_gb": round(disk.free / 2**30, 1), "total_gb": round(disk.total / 2**30, 1),
                 "percent": disk.percent},
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "battery": None,
        "platform": sys.platform,
    }
    try:
        b = psutil.sensors_battery()
    except (AttributeError, OSError, RuntimeError):
        b = None
    if b is not None:
        out["battery"] = {"percent": round(b.percent), "plugged_in": bool(b.power_plugged),
                          "minutes_left": None if b.secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN)
                          else round(b.secsleft / 60)}
    return out


def summary(s: dict[str, Any] | None = None) -> str:
    s = s or status()
    parts = [f"CPU {s['cpu_percent']:.0f}%",
             f"memory {s['memory']['used_gb']}/{s['memory']['total_gb']} GB ({s['memory']['percent']:.0f}%)",
             f"{s['disk']['free_gb']} GB free on {s['disk']['path']}"]
    if s["battery"]:
        b = s["battery"]
        parts.append(f"battery {b['percent']}%" + (" charging" if b["plugged_in"] else
                                                   f", about {b['minutes_left']} min left" if b["minutes_left"] else ""))
    parts.append(f"up {s['uptime_hours']} h")
    return ", ".join(parts)


# ---------------------------------------------------------------- programs

def processes(limit: int = 25, sort: Literal["memory", "cpu"] = "memory") -> list[dict[str, Any]]:
    """Programs grouped by name (Chrome's 30 processes are one line), heaviest first."""
    groups: dict[str, dict[str, Any]] = {}
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "username"]):
        info = p.info
        name = info.get("name") or ""
        if not name or info.get("memory_info") is None:
            continue
        g = groups.setdefault(name.lower(), {"name": name, "pids": [], "memory_mb": 0.0, "cpu_percent": 0.0,
                                             "protected": name.lower() in PROTECTED})
        g["pids"].append(info["pid"])
        g["memory_mb"] += info["memory_info"].rss / 2**20
        g["cpu_percent"] += info.get("cpu_percent") or 0.0
    rows = sorted(groups.values(), key=lambda g: -(g["memory_mb"] if sort == "memory" else g["cpu_percent"]))[:limit]
    for g in rows:
        g["memory_mb"] = round(g["memory_mb"])
        g["cpu_percent"] = round(g["cpu_percent"], 1)
        g["count"] = len(g["pids"])
    return rows


def _matching(name: str) -> list[psutil.Process]:
    want = name.lower().strip()
    want_exe = want if want.endswith(".exe") or sys.platform != "win32" else want + ".exe"
    out = []
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n in (want, want_exe):
            out.append(p)
    return out


def end_program(name: str) -> dict[str, Any]:
    procs = _matching(name)
    if not procs:
        raise ValueError(f"No running program called {name!r}")
    real = procs[0].info.get("name") or name
    if real.lower() in PROTECTED or any(p.pid in (0, 1, os.getpid(), os.getppid()) for p in procs):
        raise ValueError(f"{real} keeps Windows or LifeOS running and can't be ended from here")
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    activity.log("system", f"Ended {real} ({len(procs)} process(es))")
    return {"ended": real, "processes": len(procs), "forced": len(alive)}


# ---------------------------------------------------------------- volume

def _win_volume_keys(key: int, times: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(times):
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)


def set_volume(level: int | None = None, change: Literal["up", "down", "mute"] | None = None) -> dict[str, Any]:
    if level is None and change is None:
        raise ValueError("Give a level from 0 to 100, or up, down or mute")
    if level is not None:
        level = max(0, min(100, int(level)))
    if sys.platform == "win32":
        up, down, mute = 0xAF, 0xAE, 0xAD  # each volume key press is 2%
        if change == "mute":
            _win_volume_keys(mute, 1)
        elif change in ("up", "down"):
            _win_volume_keys(up if change == "up" else down, 5)
        else:
            _win_volume_keys(down, 50)
            _win_volume_keys(up, round(level / 2))
    elif sys.platform == "darwin":
        script = ("set volume output muted not (output muted of (get volume settings))" if change == "mute" else
                  f"set volume output volume ((output volume of (get volume settings)) {'+' if change == 'up' else '-'} 10)"
                  if change else f"set volume output volume {level}")
        _run(["osascript", "-e", script])
    elif shutil.which("wpctl"):
        target = "@DEFAULT_AUDIO_SINK@"
        if change == "mute":
            _run(["wpctl", "set-mute", target, "toggle"])
        else:
            _run(["wpctl", "set-volume", "-l", "1.0", target, "10%+" if change == "up" else "10%-" if change == "down"
                  else f"{level / 100:.2f}"])
    elif shutil.which("pactl"):
        sink = "@DEFAULT_SINK@"
        if change == "mute":
            _run(["pactl", "set-sink-mute", sink, "toggle"])
        else:
            _run(["pactl", "set-sink-volume", sink, "+10%" if change == "up" else "-10%" if change == "down" else f"{level}%"])
    else:
        raise ValueError("No volume control found (install PipeWire's wpctl or PulseAudio's pactl)")
    what = "Muted/unmuted" if change == "mute" else f"Volume {change}" if change else f"Volume set to {level}%"
    activity.log("system", what)
    return {"volume": level, "change": change}


# ---------------------------------------------------------------- power

def _power_argv(action: PowerAction) -> list[str]:
    if sys.platform == "win32":
        return {"lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
                "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                "restart": ["shutdown.exe", "/r", "/t", "10"],
                "shutdown": ["shutdown.exe", "/s", "/t", "10"],
                "sign_out": ["shutdown.exe", "/l"]}[action]
    if sys.platform == "darwin":
        return {"lock": ["pmset", "displaysleepnow"], "sleep": ["pmset", "sleepnow"],
                "restart": ["osascript", "-e", 'tell app "System Events" to restart'],
                "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
                "sign_out": ["osascript", "-e", 'tell app "System Events" to log out']}[action]
    return {"lock": ["loginctl", "lock-session"], "sleep": ["systemctl", "suspend"], "restart": ["systemctl", "reboot"],
            "shutdown": ["systemctl", "poweroff"],
            "sign_out": ["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "self")]}[action]


def power(action: PowerAction) -> dict[str, Any]:
    if action not in POWER_LABEL:
        raise ValueError(f"Unknown power action {action!r}")
    activity.log("system", POWER_LABEL[action])
    try:
        _run(_power_argv(action))
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"{POWER_LABEL[action]} failed: {exc}") from exc
    note = " in 10 seconds (run 'shutdown /a' to cancel)" if sys.platform == "win32" and action in ("restart", "shutdown") else ""
    return {"action": action, "summary": f"{POWER_LABEL[action]}{note}."}
