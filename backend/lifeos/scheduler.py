"""Background jobs: fire due reminders, keep the file index fresh, embed pending memories."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Callable

from . import activity, db, files, integrations, memory, projects, settings, tasks, workflows

log = logging.getLogger("lifeos.scheduler")


def fire_reminders() -> int:
    due = tasks.due_reminders()
    notify = settings.get("notification_prefs").get("reminders", True)
    for t in due:
        if notify:
            with db.session() as conn:
                conn.execute("INSERT INTO notifications(title, body, task_id, kind, created_at) VALUES (?,?,?,?,?)",
                             (f"Reminder: {t['title']}", t.get("notes"), t["id"], "reminder", db.now()))
        tasks.mark_reminded(t["id"])
        activity.log("reminder", f"Reminder sent: {t['title']}", details={"task_id": t["id"]})
    return len(due)


class Scheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._jobs: list[tuple[str, float, Callable[[], object]]] = [
            ("reminders", 20, fire_reminders),
            ("workflows", 30, workflows.run_due),
            ("reindex", 30 * 60, files.index_all),
            ("projects", 30 * 60, projects.detect),
            ("calendars", 30 * 60, integrations.sync_all),
            ("embed-memories", 10 * 60, memory.reembed_missing),
        ]
        self._last: dict[str, float] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lifeos-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            for name, interval, job in self._jobs:
                if now - self._last.get(name, 0) >= interval:
                    self._last[name] = now
                    try:
                        job()
                    except Exception:  # a failing job must not kill the scheduler
                        log.exception("Job %s failed", name)
            self._stop.wait(5)


def _process_alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent() -> None:
    """When launched by the desktop shell, exit if the shell dies without shutting us down."""
    pid = os.environ.get("LIFEOS_PARENT_PID")
    if not pid or not pid.isdigit():
        return

    def loop() -> None:
        while _process_alive(int(pid)):
            time.sleep(2)
        log.info("Desktop shell %s exited; stopping backend", pid)
        from . import services

        services.stop_all()
        os._exit(0)

    threading.Thread(target=loop, name="lifeos-parent-watch", daemon=True).start()


def notifications(unread_only: bool = True, limit: int = 50) -> list[dict]:
    with db.session() as conn:
        sql = "SELECT * FROM notifications" + (" WHERE read = 0" if unread_only else "") + " ORDER BY id DESC LIMIT ?"
        return db.rows_to_dicts(conn.execute(sql, (limit,)).fetchall())


def mark_read(notification_id: int | None = None) -> None:
    with db.session() as conn:
        if notification_id is None:
            conn.execute("UPDATE notifications SET read = 1")
        else:
            conn.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
