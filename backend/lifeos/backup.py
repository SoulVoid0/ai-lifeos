"""Backups and moving to another PC: a zip of the LifeOS data folder, made by hand or daily.

Point the backup folder at a cloud sync folder (OneDrive, Google Drive, Dropbox) and every PC signed in to it sees
the backups; restoring one on another PC carries over the crew, memories, tasks, workflows, lessons and settings.
The database is copied with SQLite's online backup, so a backup is consistent even while missions run. Passwords
and tokens are not included: they live in the system credential store (or a file only this PC can decrypt), so
they're entered again after restoring on a new PC. Restoring happens on the next start, after the current data is
put aside in a backup-before-restore folder.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import activity, config, settings

PREFIX = "AI-LifeOS-backup-"
_RESTORE = ".restore-backup-from"
_SKIP_DIRS = {"backup-before-import", "backup-before-restore"}
_SKIP_SUFFIX = (".log", ".db-wal", ".db-shm", ".tmp")
_SKIP_NAMES = {"secrets.bin", "lifeos.db", _RESTORE}


def _folder() -> Path:
    raw = str(settings.get("backup_folder") or "").strip()
    if not raw:
        raise ValueError("Choose a backup folder first (Settings → Backup & sync)")
    p = Path(raw).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _include(rel: Path, include_media: bool) -> bool:
    if any(part.startswith(tuple(_SKIP_DIRS)) for part in rel.parts) or rel.name in _SKIP_NAMES:
        return False
    if rel.name.endswith(_SKIP_SUFFIX) or rel.name.startswith(".imported-") or rel.name.startswith(".import-"):
        return False
    return include_media or rel.parts[0] != "media"


def create(include_media: bool | None = None) -> dict[str, Any]:
    target = _folder()
    data = config.data_dir()
    if include_media is None:
        include_media = bool(settings.get("backup_include_media"))
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = target / f"{PREFIX}{stamp}.zip"
    tmp_db = Path(tempfile.mkdtemp()) / "lifeos.db"
    try:
        src = sqlite3.connect(str(config.db_path()))
        dst = sqlite3.connect(str(tmp_db))
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        partial = out.with_suffix(".zip.tmp")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db, "lifeos.db")
            for p in sorted(data.rglob("*")):
                rel = p.relative_to(data)
                if p.is_file() and _include(rel, include_media):
                    z.write(p, rel.as_posix())
            z.writestr("lifeos-backup.txt", f"AI LifeOS backup {stamp}\nmedia included: {include_media}\n")
        partial.replace(out)
    finally:
        shutil.rmtree(tmp_db.parent, ignore_errors=True)
    _prune(target)
    settings.update({"backup_last_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    activity.log("backup", f"Backed up LifeOS data to {out}", details={"bytes": out.stat().st_size})
    return {"path": str(out), "bytes": out.stat().st_size}


def _prune(target: Path) -> None:
    keep = max(1, int(settings.get("backup_keep") or 7))
    for old in sorted(target.glob(f"{PREFIX}*.zip"), reverse=True)[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups() -> list[dict[str, Any]]:
    try:
        target = _folder()
    except ValueError:
        return []
    return [{"name": p.name, "path": str(p), "bytes": p.stat().st_size,
             "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
            for p in sorted(target.glob(f"{PREFIX}*.zip"), reverse=True)]


def request_restore(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_file() or not p.name.startswith(PREFIX) or p.suffix != ".zip":
        raise ValueError("Pick one of the AI-LifeOS-backup-*.zip files")
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        if "lifeos.db" not in names or any(n.startswith("/") or ".." in Path(n).parts for n in names):
            raise ValueError("That file isn't a LifeOS backup")
    (config.data_dir() / _RESTORE).write_text(str(p), encoding="utf-8")
    return {"pending": str(p)}


def pending_restore() -> str | None:
    f = config.data_dir() / _RESTORE
    return f.read_text(encoding="utf-8").strip() if f.is_file() else None


def restore_pending() -> Path | None:
    """Run at startup, before the database is opened: replace the data folder's content with a requested backup."""
    data = config.data_dir()
    marker = data / _RESTORE
    if not marker.is_file():
        return None
    src = Path(marker.read_text(encoding="utf-8").strip())
    marker.unlink(missing_ok=True)
    if not src.is_file():
        return None
    aside = data / f"backup-before-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    aside.mkdir()
    for p in list(data.iterdir()):
        if p == aside or p.name.startswith(tuple(_SKIP_DIRS)) or p.name.endswith(".log"):
            continue
        if p.name == "secrets.bin":
            continue  # this PC's own encrypted secrets stay
        shutil.move(str(p), str(aside / p.name))
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            dest = (data / name).resolve()
            if data.resolve() not in dest.parents:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as fin, open(dest, "wb") as fout:
                shutil.copyfileobj(fin, fout)
    return src


def due() -> bool:
    cfg = settings.get_all()
    if not cfg.get("backup_daily") or not cfg.get("backup_folder"):
        return False
    last = cfg.get("backup_last_at")
    return not last or datetime.fromisoformat(last) < datetime.now(timezone.utc) - timedelta(hours=23)
