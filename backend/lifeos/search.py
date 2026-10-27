"""Universal search across files, memories, tasks and past conversations."""
from __future__ import annotations

from typing import Any

from . import db, files, memory


def everything(query: str, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    fts = db.fts_query(query)
    out: dict[str, list[dict[str, Any]]] = {"memories": [], "files": [], "tasks": [], "messages": []}
    if not fts:
        return out
    out["memories"] = memory.search(query, limit)
    out["files"] = files.search(query, limit=limit)
    words = [w.strip('"*') for w in fts.split(" OR ")]
    with db.session() as conn:
        rows = conn.execute(
            "SELECT m.id, m.conversation_id, m.role, m.content, m.created_at, c.title AS conversation_title, "
            "snippet(messages_fts, 0, '[', ']', '…', 16) AS snippet "
            "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts) LIMIT ?",
            (fts, limit),
        ).fetchall()
        out["messages"] = db.rows_to_dicts(rows)
        task_rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 2000").fetchall()
    scored = []
    for t in db.rows_to_dicts(task_rows):
        text = f"{t['title']} {t.get('notes') or ''} {t.get('project') or ''}".lower()
        s = sum(1 for w in words if w and w in text)
        if s:
            scored.append((s, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    out["tasks"] = [t for _, t in scored[:limit]]
    return out
