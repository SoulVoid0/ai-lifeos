"""Append-only activity log of everything the assistant does or proposes."""
from __future__ import annotations

import json
from typing import Any

from . import db


def log(kind: str, summary: str, *, tool: str | None = None, details: Any = None,
        status: str = "ok", action_id: int | None = None) -> int:
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO activity(kind, tool, summary, details, status, action_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (kind, tool, summary, json.dumps(details, default=str) if details is not None else None,
             status, action_id, db.now()),
        )
        return int(cur.lastrowid)


def recent(limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
    with db.session() as conn:
        if kind:
            rows = conn.execute("SELECT * FROM activity WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return db.rows_to_dicts(rows, ("details",))
