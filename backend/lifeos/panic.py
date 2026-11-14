"""The panic button: stop everything LifeOS is doing, at once, from anywhere.

Triggered by the red button in the app, the hotkey (Ctrl+Shift+F12 in the desktop app), the phone remote, or by
saying "panic". Depending on the settings it: cancels every mission and image/video job, stops speech and
listening, pauses automation (so nothing new starts without your approval), unloads AI models from the GPU, mutes
the PC and hides the LifeOS windows. "All clear" undoes the pause (only if the panic was what paused it).
"""
from __future__ import annotations

import threading
from typing import Any

from . import activity, common, db, llm, missions, settings


def cfg() -> dict[str, Any]:
    return common.group("panic")


def trigger(source: str = "app") -> dict[str, Any]:
    c = cfg()
    done: list[str] = []
    stopped = missions.stop_all()  # also interrupts media jobs and unloads models in the background
    done.append(f"stopped {stopped} mission(s)")
    if c["stop_media"]:
        from . import media

        media.stop()
    try:
        from . import voice

        voice.stop_speaking()
        done.append("stopped speaking")
    except Exception:  # noqa: BLE001 - voice is optional
        pass
    paused_by_panic = False
    if c["pause_automation"] and not settings.get("automation_paused"):
        settings.update({"automation_paused": True})
        paused_by_panic = True
        done.append("paused automation")
    if c["unload_models"]:
        threading.Thread(target=llm.unload_local_models, daemon=True, name="lifeos-panic-unload").start()
        done.append("unloading AI models")
    if c["mute"]:
        try:
            from . import system

            system.set_volume(level=0)  # not "mute": that toggles, and would unmute an already-muted PC
            done.append("muted")
        except Exception:  # noqa: BLE001 - not every system has a mixer we can reach
            pass
    common.save_group("panic", {"active": True, "since": db.now(), "paused_by_panic": paused_by_panic or c["paused_by_panic"]})
    activity.log("system", f"PANIC ({source}): " + ", ".join(done))
    return {**status(), "done": done, "hide_windows": c["hide_windows"]}


def clear() -> dict[str, Any]:
    c = cfg()
    if c["paused_by_panic"] and settings.get("automation_paused"):
        settings.update({"automation_paused": False})
    common.save_group("panic", {"active": False, "paused_by_panic": False})
    activity.log("system", "Panic cleared")
    return status()


def status() -> dict[str, Any]:
    c = cfg()
    return {"active": c["active"], "since": c["since"] or None, "config": c,
            "automation_paused": bool(settings.get("automation_paused"))}
