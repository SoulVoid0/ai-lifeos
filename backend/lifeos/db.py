"""SQLite storage. One connection per call; WAL mode keeps the scheduler and API from blocking."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approved_folders (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    label TEXT,
    created_at TEXT NOT NULL,
    last_indexed_at TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    folder_id INTEGER NOT NULL REFERENCES approved_folders(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    accessed_at TEXT,
    category TEXT,
    excerpt TEXT,
    sha256 TEXT,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id);
CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext);

CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    name, path, excerpt, content='files', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, name, path, excerpt) VALUES (new.id, new.name, new.path, coalesce(new.excerpt, ''));
END;
CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, path, excerpt) VALUES ('delete', old.id, old.name, old.path, coalesce(old.excerpt, ''));
END;
CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, path, excerpt) VALUES ('delete', old.id, old.name, old.path, coalesce(old.excerpt, ''));
    INSERT INTO files_fts(rowid, name, path, excerpt) VALUES (new.id, new.name, new.path, coalesce(new.excerpt, ''));
END;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT,
    project TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'open',
    due_at TEXT,
    remind_at TEXT,
    reminded_at TEXT,
    estimate_minutes INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    source TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    embedding_model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, kind, content='memories', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, kind) VALUES (new.id, new.content, new.kind);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, kind) VALUES ('delete', old.id, old.content, old.kind);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content, kind ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, kind) VALUES ('delete', old.id, old.content, old.kind);
    INSERT INTO memories_fts(rowid, content, kind) VALUES (new.id, new.content, new.kind);
END;

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    data TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    summary TEXT NOT NULL,
    preview TEXT,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    conversation_id INTEGER,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    executed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    tool TEXT,
    summary TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    action_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT,
    task_id INTEGER,
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

# Writers wait this long for the lock instead of failing with "database is locked".
BUSY_TIMEOUT_S = 30

_init_lock = threading.Lock()
_initialized: set[str] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    path = str(config.db_path())
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_S * 1000}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    with _init_lock:
        if path not in _initialized:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.commit()
            _initialized.add(path)
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for field in json_fields:
        if out.get(field):
            out[field] = json.loads(out[field])
    out.pop("embedding", None)
    return out


def rows_to_dicts(rows: list[sqlite3.Row], json_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [row_to_dict(r, json_fields) for r in rows]  # type: ignore[misc]


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: quoted prefix terms joined by OR."""
    terms = [t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(t) > 1]
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "my", "me", "i", "is", "it",
            "that", "this", "find", "show", "about", "with", "what", "was", "from", "all", "file", "files"}
    terms = [t for t in terms if t not in stop] or terms
    return " OR ".join(f'"{t}"*' for t in terms[:12])
