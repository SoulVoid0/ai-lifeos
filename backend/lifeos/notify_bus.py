"""The LifeOS desktop's notification server (Linux): other apps' notifications land in the LifeOS bell.

Apps send desktop notifications over D-Bus to org.freedesktop.Notifications (the freedesktop notification spec).
In the LifeOS session nothing else owns that name, so the backend takes it and stores every notification in the
same table as reminders and crew messages. On a desktop that already has a notification server (GNOME, KDE)
the name is taken and this quietly does nothing.
"""
# No "from __future__ import annotations": dbus-next reads the D-Bus signatures from the annotations.

import asyncio
import logging
import os
import sys
import threading
from typing import Any

from . import db, settings

log = logging.getLogger("lifeos.notify_bus")
NAME = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
_state: dict[str, Any] = {"running": False, "error": "", "next_id": 1}
# Chatty sources that would flood the bell.
IGNORED_APPS = {"notify-send-volume", "volume", "brightness"}


def store(app: str, summary: str, body: str) -> int | None:
    """Save one notification from another app. Returns its row id, or None if it was dropped."""
    app = (app or "").strip()
    if not summary.strip() or app.lower() in IGNORED_APPS:
        return None
    if not settings.get("notification_prefs").get("apps", True):
        return None
    title = f"{app}: {summary}" if app and app.lower() not in summary.lower() else summary
    with db.session() as conn:
        cur = conn.execute("INSERT INTO notifications(title, body, kind, created_at) VALUES (?,?,?,?)",
                           (title[:200], (body or "")[:2000], "app", db.now()))
        return int(cur.lastrowid)


def status() -> dict[str, Any]:
    return {"supported": sys.platform.startswith("linux"), "running": _state["running"], "error": _state["error"]}


def _build_interface():
    from dbus_next.service import ServiceInterface, method, signal  # type: ignore[import-not-found]

    class Notifications(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(NAME)

        @method()
        def GetCapabilities(self) -> "as":  # noqa: F722,F821
            return ["body", "persistence"]

        @method()
        def Notify(self, app_name: "s", replaces_id: "u", app_icon: "s", summary: "s", body: "s",  # noqa: F821
                   actions: "as", hints: "a{sv}", expire_timeout: "i") -> "u":  # noqa: F722,F821
            try:
                store(app_name, summary, body)
            except Exception:  # never let a bad notification break the bus
                log.exception("storing a notification failed")
            nid = replaces_id or _state["next_id"]
            _state["next_id"] = max(_state["next_id"], nid) + 1
            return nid

        @method()
        def CloseNotification(self, id: "u"):  # noqa: A002,F821
            self.NotificationClosed(id, 3)

        @method()
        def GetServerInformation(self) -> "ssss":  # noqa: F821
            return ["AI LifeOS", "AI LifeOS", "1", "1.2"]

        @signal()
        def NotificationClosed(self, id: "u", reason: "u") -> "uu":  # noqa: A002,F821
            return [id, reason]

        @signal()
        def ActionInvoked(self, id: "u", action_key: "s") -> "us":  # noqa: A002,F821
            return [id, action_key]

    return Notifications()


async def _serve() -> None:
    from dbus_next import BusType  # type: ignore[import-not-found]
    from dbus_next.aio import MessageBus  # type: ignore[import-not-found]
    from dbus_next.constants import RequestNameReply  # type: ignore[import-not-found]

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    bus.export(PATH, _build_interface())
    reply = await bus.request_name(NAME, 4)  # DO_NOT_QUEUE: another server keeps the name
    if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
        _state["error"] = "another notification server is running"
        bus.disconnect()
        return
    _state["running"] = True
    await bus.wait_for_disconnect()
    _state["running"] = False


def start() -> bool:
    """Start the server in a background thread when it can work here; True if it was started."""
    if not sys.platform.startswith("linux") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    if not settings.get("os_notifications"):
        return False
    try:
        import dbus_next  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        _state["error"] = "dbus-next is not installed"
        return False

    def run() -> None:
        try:
            asyncio.run(_serve())
        except Exception as exc:  # no bus, permission, ...
            _state["error"] = str(exc)
            log.info("notification server not started: %s", exc)

    threading.Thread(target=run, name="lifeos-notify-bus", daemon=True).start()
    return True
