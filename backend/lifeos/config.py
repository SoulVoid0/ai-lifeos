"""Static configuration. User-editable settings live in the database (see settings.py)."""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

log = logging.getLogger("lifeos.config")


def data_dir() -> Path:
    """Directory holding the SQLite database and other local state."""
    override = os.environ.get("LIFEOS_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", Path.home())) / "AI-LifeOS"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ai-lifeos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return Path(os.environ.get("LIFEOS_DB", data_dir() / "lifeos.db"))


# Python from the Microsoft Store runs in an app container, so whatever it writes to %APPDATA% really lands in
# %LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.*\LocalCache\Roaming. The desktop app's backend is a
# normal program and sees the real %APPDATA%, so data made while running the backend with Store Python is moved over.
# Settings and agent models aren't counted: they're what people change first in a fresh app.
_CONTENT_TABLES = ("approved_folders", "files", "tasks", "memories", "messages", "missions", "media", "lessons",
                   "workflows", "projects")
_MARKER = ".imported-store-python-data"
_REQUEST = ".import-store-python-data-now"


def _store_python_dirs() -> list[Path]:
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    if not packages.is_dir():
        return []
    found = [p / "LocalCache" / "Roaming" / "AI-LifeOS" for p in packages.glob("PythonSoftwareFoundation.Python.*")]
    found = [d for d in found if (d / "lifeos.db").is_file()]
    return sorted(found, key=lambda d: (d / "lifeos.db").stat().st_mtime, reverse=True)


def _content(db: Path) -> int:
    """Rough amount of user data in a LifeOS database (0 for a missing or fresh one)."""
    if not db.is_file():
        return 0
    total = 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in _CONTENT_TABLES:
                if t in names:
                    total += con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0
    return total


def _auto_sources() -> list[Path]:
    if os.environ.get("LIFEOS_DATA_DIR") or os.environ.get("LIFEOS_DB"):
        return []
    if os.name != "nt" or "PythonSoftwareFoundation" in sys.executable or "WindowsApps" in sys.executable:
        return []
    return _store_python_dirs()


def store_python_data() -> dict:
    """For Settings: the Store Python data folder found, whether it was imported, whether an import is queued."""
    sources = _auto_sources()
    target = data_dir()
    return {"found": str(sources[0]) if sources else None, "imported": (target / _MARKER).is_file()
            and (target / _MARKER).read_text(encoding="utf-8").startswith("imported"),
            "pending": (target / _REQUEST).exists()}


def request_store_python_import() -> dict:
    """Import on the next start even if the app already has its own data (it's backed up first)."""
    if not _auto_sources():
        raise FileNotFoundError("no data from Microsoft Store Python on this PC")
    (data_dir() / _REQUEST).write_text("requested\n", encoding="utf-8")
    return store_python_data()


def import_store_python_data(sources: list[Path] | None = None) -> Path | None:
    """Copy LifeOS data written by Microsoft Store Python into the real data folder.

    Runs automatically once, when that copy holds at least as much as the real one (settings and agent models
    don't count), or on the next start after an import was requested in Settings. The real folder's database is
    kept in a backup folder first. Returns the folder imported from, if any.
    """
    if sources is None:
        sources = _auto_sources()
    target = data_dir()
    marker, request = target / _MARKER, target / _REQUEST
    forced = request.exists()
    request.unlink(missing_ok=True)
    if not sources or (marker.exists() and not forced):
        return None
    source = sources[0]
    if source.resolve() == target.resolve():
        return None
    theirs, ours = _content(source / "lifeos.db"), _content(target / "lifeos.db")
    if not forced and (theirs < ours or not (source / "lifeos.db").stat().st_size):
        marker.write_text("nothing to import\n", encoding="utf-8")
        return None
    backup = target / f"backup-before-import-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir()
    for name in ("lifeos.db", "lifeos.db-wal", "lifeos.db-shm", "secrets.bin"):
        if (target / name).exists():
            shutil.move(str(target / name), str(backup / name))
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.log", _MARKER, _REQUEST, "backup-*"))
    marker.write_text(f"imported from {source}\nprevious database: {backup}\n", encoding="utf-8")
    log.warning("Imported LifeOS data from %s (previous database kept in %s)", source, backup)
    return source


HOST = os.environ.get("LIFEOS_HOST", "127.0.0.1")
PORT = int(os.environ.get("LIFEOS_PORT", "8765"))

# Every API request must carry this header. Browsers cannot add custom headers to
# cross-origin requests without a CORS preflight, which only allowed origins pass,
# so random web pages cannot drive the local API.
CLIENT_HEADER = "X-LifeOS-Client"

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]

# Text extraction limits for indexing.
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_EXCERPT_CHARS = 4000
MAX_INDEX_FILES = 50_000
