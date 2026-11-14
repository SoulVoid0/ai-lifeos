"""Game mode: when a game starts, LifeOS gets out of the GPU's way; when it ends, the crew comes back.

Detection (every few seconds, cheap):
  * a running program from a game library folder (Steam, Epic, GOG, EA, Ubisoft, Riot, Battle.net, Xbox, itch),
  * a program on your own list of games,
  * on Windows, a full-screen Direct3D app in front (what Windows' own "Focus assist: when I'm playing" uses),
    unless it's a browser or video player,
  * or you turn it on yourself (app, phone remote, "Hey LifeOS, game mode on").

While a game runs:
  * local models are unloaded from VRAM and ComfyUI is told to free its memory (or stopped, if LifeOS started it),
  * new model requests wait (missions and automations pause where they are and carry on afterwards), the wake word
    listener and the live wallpaper pause,
  * nothing is cancelled and nothing is lost.
When the game closes (and stays closed for a short grace period), the default model is loaded again in the
background so the first request after gaming is fast.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from . import activity, common

log = logging.getLogger("lifeos.gamemode")

GAME_DIRS = re.compile(r"[\\/](steamapps[\\/]common|epic games|gog galaxy[\\/]games|gog games|ea games|origin games|"
                       r"ubisoft game launcher[\\/]games|riot games|xboxgames|battle\.net[\\/]|blizzard|"
                       r"itch[\\/]apps|heroic[\\/]|lutris[\\/]|games[\\/]steam|windowsapps[\\/].*(xbox|game))", re.I)
NOT_GAMES = {"steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "galaxyclient.exe", "eadesktop.exe",
             "origin.exe", "upc.exe", "ubisoftconnect.exe", "riotclientservices.exe", "battle.net.exe", "agent.exe",
             "crashreportclient.exe", "unitycrashhandler64.exe", "easyanticheat.exe", "beservice.exe", "steam",
             "steamwebhelper", "gameoverlayui.exe", "ue4prereqsetup_x64.exe", "vc_redist.x64.exe", "dxsetup.exe",
             "reaper", "pressure-vessel-wrap", "proton", "wineserver", "explorer.exe"}
NOT_FULLSCREEN_GAMES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "vlc.exe",
                        "mpc-hc64.exe", "mpc-be64.exe", "potplayermini64.exe", "wmplayer.exe", "video.ui.exe",
                        "microsoft.media.player.exe", "powerpnt.exe", "ai lifeos.exe", "ai-lifeos.exe", "obs64.exe",
                        "applicationframehost.exe", "lockapp.exe", "searchhost.exe", "startmenuexperiencehost.exe"}
GRACE_S = 20

_state: dict[str, Any] = {"active": False, "game": None, "since": None, "reason": None, "manual": None,
                          "last_seen": 0.0, "stopped_comfy": False, "unloaded": 0}
_lock = threading.Lock()
_clear = threading.Event()
_clear.set()
_thread: dict[str, Any] = {"t": None, "stop": threading.Event()}


def cfg() -> dict[str, Any]:
    return common.group("game_mode")


def configure(changes: dict[str, Any]) -> dict[str, Any]:
    def check(c: dict[str, Any]) -> None:
        c["games"] = [str(g).strip()[:120] for g in c["games"] if str(g).strip()][:100]

    return common.save_group("game_mode", changes, check)


def active() -> bool:
    return bool(_state["active"])


def status() -> dict[str, Any]:
    return {**{k: v for k, v in _state.items()}, "config": cfg()}


# ---------------------------------------------------------------- detection

def _fullscreen_app() -> str | None:
    """Windows: the exe of a full-screen Direct3D app (a game) in front, if any."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return None
    except (AttributeError, OSError):
        return None
    if state.value not in (2, 3):  # QUNS_BUSY (full-screen app) or QUNS_RUNNING_D3D_FULL_SCREEN
        return None
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        name = psutil.Process(pid.value).name()
    except (psutil.Error, ValueError):
        return None
    if name.lower() in NOT_FULLSCREEN_GAMES:
        return None
    return name


def _is_game(name: str, exe: str, games: set[str]) -> bool:
    low = name.lower()
    if low in NOT_GAMES:
        return False
    if low in games or low.removesuffix(".exe") in games:
        return True
    return bool(exe and GAME_DIRS.search(exe))


def detect() -> tuple[str | None, str | None]:
    """(game name, how it was found) or (None, None)."""
    c = cfg()
    games = {g.lower() for g in c["games"]} | {g.lower().removesuffix(".exe") for g in c["games"]}
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if p.info["pid"] == me:
                continue
            name, exe = p.info.get("name") or "", p.info.get("exe") or ""
        except (psutil.Error, KeyError):
            continue
        if _is_game(name, exe, games):
            return Path(name).stem or Path(exe).stem, "library" if exe and GAME_DIRS.search(exe) else "your list"
    if c["fullscreen"]:
        fs = _fullscreen_app()
        if fs:
            return Path(fs).stem, "full screen"
    return None, None


# ---------------------------------------------------------------- what happens

def _free_gpu() -> None:
    from . import llm, media, services

    n = llm.unload_local_models()
    _state["unloaded"] = n
    try:
        media.stop()
    except Exception:  # noqa: BLE001
        pass
    if services.comfy_up():
        url = media.cfg()["comfy_url"].rstrip("/")
        try:
            httpx.post(f"{url}/free", json={"unload_models": True, "free_memory": True}, timeout=10)
        except httpx.HTTPError:
            pass
        if cfg()["stop_comfy"] and services._alive("comfyui"):
            proc = services._started.get("comfyui")
            if proc is not None:
                try:
                    if os.name == "nt":
                        import subprocess

                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        os.killpg(proc.pid, 15)
                    _state["stopped_comfy"] = True
                except Exception:  # noqa: BLE001
                    pass


def _warm_up() -> None:
    from . import llm, settings

    model = settings.get("fast_model") or settings.get("chat_model")
    if not model or llm.parse_ref(model)[0] != "ollama":
        return
    try:
        httpx.post(f"{settings.get('ollama_url').rstrip('/')}/api/generate",
                   json={"model": llm.parse_ref(model)[1], "keep_alive": settings.get("ollama_keep_alive")}, timeout=300)
    except httpx.HTTPError:
        pass


def _enter(game: str, reason: str) -> None:
    with _lock:
        if _state["active"]:
            _state.update(game=game, reason=reason)
            return
        _state.update(active=True, game=game, reason=reason, since=time.time(), stopped_comfy=False)
        _clear.clear()
    activity.log("system", f"Game mode on: {game} ({reason}). AI models were moved off the GPU.")
    if cfg()["free_gpu"]:
        common.background("gamemode-free", _free_gpu)
    if cfg()["notify"]:
        common.notify(f"Game mode: {game}", "The crew paused and freed the GPU. Their work continues when you're done.",
                      kind="system")


def _leave() -> None:
    with _lock:
        if not _state["active"]:
            return
        played = int((time.time() - (_state["since"] or time.time())) / 60)
        game = _state["game"]
        _state.update(active=False, game=None, reason=None, since=None)
        _clear.set()
    activity.log("system", f"Game mode off after {played} min of {game}. The crew is back.")
    if cfg()["reload_after"]:
        common.background("gamemode-warm", _warm_up)
    if cfg()["notify"]:
        common.notify("Game over: the crew is back", f"{game} closed after {played} min. Paused work carries on.",
                      kind="system")


def set_manual(on: bool | None) -> dict[str, Any]:
    """Force game mode on or off (None = automatic again)."""
    _state["manual"] = on
    if on:
        _enter("Manual", "turned on by you")
    elif on is False:
        _leave()
    return status()


def tick() -> None:
    c = cfg()
    if _state["manual"] is not None:
        return
    if not c["enabled"] or not c["auto_detect"]:
        if _state["active"]:
            _leave()
        return
    game, reason = detect()
    now = time.time()
    if game:
        _state["last_seen"] = now
        _enter(game, reason or "")
    elif _state["active"] and now - _state["last_seen"] >= GRACE_S:
        _leave()


def _loop(stop: threading.Event) -> None:
    while not stop.wait(5):
        try:
            tick()
        except Exception:  # noqa: BLE001
            log.exception("Game detection failed")


def start() -> None:
    if _thread["t"] and _thread["t"].is_alive():
        return
    stop = threading.Event()
    _thread.update(stop=stop, t=threading.Thread(target=_loop, args=(stop,), daemon=True, name="lifeos-gamemode"))
    _thread["t"].start()


def stop() -> None:
    _thread["stop"].set()


def wait_until_clear(abort: threading.Event | None = None, max_wait: float | None = None) -> None:
    """Called before a local model or GPU job starts: hold it while a game runs (if set to hold AI work)."""
    if _clear.is_set() or not cfg()["hold_ai"]:
        return
    started = time.monotonic()
    while not _clear.wait(1):
        if abort is not None and abort.is_set():
            return
        if max_wait is not None and time.monotonic() - started > max_wait:
            return
