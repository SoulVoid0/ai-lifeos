"""Static configuration. User-editable settings live in the database (see settings.py)."""
from __future__ import annotations

import os
from pathlib import Path


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
