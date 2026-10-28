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
_CONTENT_TABLES = ("settings", "approved_folders", "files", "tasks", "memories", "messages", "missions", "media",
                   "lessons", "workflows", "projects")
_MARKER = ".imported-store-python-data"


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
            if "agents" in names:  # models picked by the user count too
                total += con.execute("SELECT COUNT(*) FROM agents WHERE model != ''").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0
    return total


def import_store_python_data(sources: list[Path] | None = None) -> Path | None:
    """Once, copy LifeOS data written by Microsoft Store Python into the real data folder.

    Only happens when that copy holds more than the real one; the real folder's database is kept in a backup
    folder first. Returns the folder imported from, if any.
    """
    if sources is None:
        if os.environ.get("LIFEOS_DATA_DIR") or os.environ.get("LIFEOS_DB"):
            return None
        if os.name != "nt" or "PythonSoftwareFoundation" in sys.executable or "WindowsApps" in sys.executable:
            return None
        sources = _store_python_dirs()
    target = data_dir()
    marker = target / _MARKER
    if marker.exists() or not sources:
        return None
    source = sources[0]
    if source.resolve() == target.resolve() or _content(source / "lifeos.db") <= _content(target / "lifeos.db"):
        marker.write_text("nothing to import\n", encoding="utf-8")
        return None
    backup = target / f"backup-before-import-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir()
    for name in ("lifeos.db", "lifeos.db-wal", "lifeos.db-shm", "secrets.bin"):
        if (target / name).exists():
            shutil.move(str(target / name), str(backup / name))
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.log", _MARKER, "backup-*"))
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
