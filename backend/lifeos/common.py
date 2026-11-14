"""Small helpers shared by the newer feature modules: grouped settings, bell notifications and file downloads."""
from __future__ import annotations

import copy
import logging
import os
import shutil
import subprocess
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx

from . import config, db, settings

log = logging.getLogger("lifeos.common")


def group(key: str) -> dict[str, Any]:
    """A grouped setting (a dict in settings.DEFAULTS) with its defaults filled in."""
    stored = settings.get(key) or {}
    out = copy.deepcopy(settings.DEFAULTS[key])
    out.update({k: v for k, v in stored.items() if k in out})
    return out


def save_group(key: str, changes: dict[str, Any], check: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Merge known keys into a grouped setting (type-checked against the defaults), validate, store."""
    current = group(key)
    defaults = settings.DEFAULTS[key]
    for k, v in changes.items():
        if k not in defaults or v is None:
            continue
        want = type(defaults[k])
        if want is float and isinstance(v, int) and not isinstance(v, bool):
            v = float(v)
        if want is int and isinstance(v, float) and v.is_integer():
            v = int(v)
        if not isinstance(v, want) or (want is int and isinstance(v, bool)):
            raise ValueError(f"{key}.{k} must be {want.__name__}")
        current[k] = v
    if check:
        check(current)
    settings.update({key: current})
    return current


def notify(title: str, body: str | None = None, kind: str = "system", pref: str | None = None) -> None:
    """Put a message in the LifeOS bell (the app shows it as a toast and an OS notification)."""
    if pref and not settings.get("notification_prefs").get(pref, True):
        return
    with db.session() as conn:
        conn.execute("INSERT INTO notifications(title, body, kind, created_at) VALUES (?,?,?,?)",
                     (title[:200], (body or "")[:1000] or None, kind, db.now()))


def feature_dir(name: str) -> Path:
    path = config.data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def hidden() -> dict[str, Any]:
    """subprocess kwargs that keep a console window from flashing up on Windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def download(url: str, dest: Path, job: dict[str, Any] | None = None, timeout: float = 60) -> Path:
    """Download to dest (via a .part file, resuming a partial one). Progress goes into job (done/total bytes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                              timeout=httpx.Timeout(timeout, connect=20)) as r:
                if r.status_code == 416:  # already complete
                    break
                r.raise_for_status()
                if have and r.status_code != 206:
                    have = 0  # the server ignored the range: start over
                total = int(r.headers.get("content-length") or 0) + have
                if job is not None:
                    job.update(total=total, done=have)
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        if job is not None:
                            job["done"] = job.get("done", 0) + len(chunk)
            break
        except httpx.TransportError as exc:
            if attempt == 3:
                raise RuntimeError(f"download of {url} kept failing: {exc}") from exc
            time.sleep(2 + attempt * 3)
    os.replace(part, dest)
    return dest


def extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for m in z.namelist():
                if m.startswith("/") or ".." in Path(m).parts:
                    raise ValueError(f"unsafe path in {archive.name}: {m}")
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise ValueError(f"unsafe path in {archive.name}: {m.name}")
            t.extractall(target)
    archive.unlink(missing_ok=True)


def background(name: str, fn: Callable[[], Any]) -> threading.Thread:
    def run() -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - a background job must never take the backend down
            log.exception("%s failed", name)

    t = threading.Thread(target=run, name=f"lifeos-{name}", daemon=True)
    t.start()
    return t


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None
