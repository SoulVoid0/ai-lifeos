"""Persistent chat history."""
from __future__ import annotations

import json
from typing import Any

from . import db


def create(title: str = "New conversation") -> dict[str, Any]:
    with db.session() as conn:
        cur = conn.execute("INSERT INTO conversations(title, created_at, updated_at) VALUES (?,?,?)",
                           (title[:80], db.now(), db.now()))
        cid = int(cur.lastrowid)
    return get(cid)


def get(conversation_id: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not row:
        raise KeyError(f"Conversation {conversation_id} not found")
    return db.row_to_dict(row)  # type: ignore[return-value]


def list_all(limit: int = 100) -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return db.rows_to_dicts(rows)


def delete(conversation_id: int) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def add_message(conversation_id: int, role: str, content: str, data: Any = None) -> dict[str, Any]:
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO messages(conversation_id, role, content, data, created_at) VALUES (?,?,?,?,?)",
            (conversation_id, role, content, json.dumps(data, default=str) if data is not None else None, db.now()),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (db.now(), conversation_id))
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return db.row_to_dict(row, ("data",))  # type: ignore[return-value]


def messages(conversation_id: int, limit: int = 500) -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id LIMIT ?",
                            (conversation_id, limit)).fetchall()
    return db.rows_to_dicts(rows, ("data",))


def rename(conversation_id: int, title: str) -> dict[str, Any]:
    with db.session() as conn:
        conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title[:80], conversation_id))
    return get(conversation_id)
