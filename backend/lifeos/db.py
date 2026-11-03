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

-- ---------------------------------------------------------------- crew (multi-agent)

CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    persona TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    tools TEXT NOT NULL DEFAULT '[]',
    color TEXT NOT NULL DEFAULT 'violet',
    icon TEXT NOT NULL DEFAULT 'bot',
    max_steps INTEGER NOT NULL DEFAULT 6,
    enabled INTEGER NOT NULL DEFAULT 1,
    builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    lead TEXT NOT NULL,
    target_agent TEXT,
    source TEXT NOT NULL DEFAULT 'user',
    plan TEXT,
    result TEXT,
    outbox_path TEXT,
    error TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);

CREATE TABLE IF NOT EXISTS mission_tasks (
    id INTEGER PRIMARY KEY,
    mission_id INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    agent TEXT NOT NULL,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    depends_on TEXT NOT NULL DEFAULT '[]',
    preset TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mtasks_mission ON mission_tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_mtasks_status ON mission_tasks(status);
CREATE INDEX IF NOT EXISTS idx_mtasks_agent ON mission_tasks(agent, id);

-- Generated images and videos.
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    prompt TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    path TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    seconds REAL,
    mission_id INTEGER,
    agent TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

-- What the crew has learned: short rules an agent (or every agent, agent = '*') reads before working.
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'review',
    mission_id INTEGER,
    task_id INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lessons_agent ON lessons(agent);

CREATE TABLE IF NOT EXISTS mission_events (
    id INTEGER PRIMARY KEY,
    mission_id INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    task_id INTEGER,
    agent TEXT,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    data TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mevents_mission ON mission_events(mission_id);
CREATE INDEX IF NOT EXISTS idx_mevents_agent ON mission_events(agent, id);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    mission_id INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    task_id INTEGER,
    agent TEXT NOT NULL,
    question TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '[]',
    answer TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    mission_id INTEGER,
    agent TEXT,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_created ON ledger(created_at);

-- ---------------------------------------------------------------- automation engine

CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    trigger TEXT NOT NULL,
    conditions TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    source_text TEXT,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER PRIMARY KEY,
    workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    log TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wruns_workflow ON workflow_runs(workflow_id);

-- ---------------------------------------------------------------- projects and integrations

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'folder',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    pinned INTEGER NOT NULL DEFAULT 0,
    auto INTEGER NOT NULL DEFAULT 1,
    last_activity_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    location TEXT NOT NULL UNIQUE,
    label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_synced_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES calendar_sources(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    title TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    description TEXT,
    UNIQUE(source_id, uid, start_at)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_at);

-- A named set of files, folders, web pages and apps opened together ("my study workspace").
CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    items TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_opens (
    id INTEGER PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    opened_at TEXT NOT NULL,
    by TEXT NOT NULL DEFAULT 'user'
);

-- Tools the user defines without code: a web request (webhook) or a set of things to open.
CREATE TABLE IF NOT EXISTS custom_tools (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Columns added after the first release. SQLite has no ADD COLUMN IF NOT EXISTS.
MIGRATIONS = [
    "ALTER TABLE notifications ADD COLUMN kind TEXT NOT NULL DEFAULT 'reminder'",
    "ALTER TABLE actions ADD COLUMN mission_id INTEGER",
    "ALTER TABLE agents ADD COLUMN candidates TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE mission_tasks ADD COLUMN model TEXT",
    "ALTER TABLE mission_tasks ADD COLUMN score REAL",
    "ALTER TABLE mission_tasks ADD COLUMN review TEXT",
    "ALTER TABLE mission_tasks ADD COLUMN user_rating INTEGER",
    "ALTER TABLE mission_tasks ADD COLUMN transcript TEXT",
    "ALTER TABLE agents ADD COLUMN voice TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE agents ADD COLUMN quirks TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE agents ADD COLUMN catchphrases TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN idle_lines TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE tasks ADD COLUMN parent_id INTEGER",
]

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
            for stmt in MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise
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
