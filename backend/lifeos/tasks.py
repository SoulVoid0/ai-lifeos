"""Tasks, deadlines and reminders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, timeparse

PRIORITIES = {"low": 1, "normal": 2, "medium": 2, "high": 3, "urgent": 4}
UPDATABLE = {"title", "notes", "project", "priority", "status", "due_at", "remind_at", "estimate_minutes"}


def _norm_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    dt = timeparse.parse(str(value))
    if dt is None:
        raise ValueError(f"Could not understand the date '{value}'")
    return timeparse.iso(dt)


def _norm_priority(value: Any) -> int:
    if isinstance(value, int) and 1 <= value <= 4:
        return value
    return PRIORITIES.get(str(value).lower(), 2)


def get(task_id: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError(f"Task {task_id} not found")
    return db.row_to_dict(row)  # type: ignore[return-value]


def create(title: str, *, notes: str | None = None, project: str | None = None, priority: Any = 2,
           due: Any = None, remind: Any = None, estimate_minutes: int | None = None) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("Task title is required")
    due_at, remind_at = _norm_time(due), _norm_time(remind)
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(title, notes, project, priority, due_at, remind_at, estimate_minutes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, notes, project, _norm_priority(priority), due_at, remind_at, estimate_minutes, db.now()),
        )
        task_id = int(cur.lastrowid)
    return get(task_id)


def update(task_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    get(task_id)
    fields = {k: v for k, v in changes.items() if k in UPDATABLE}
    if "due_at" in fields:
        fields["due_at"] = _norm_time(fields["due_at"])
    if "remind_at" in fields:
        fields["remind_at"] = _norm_time(fields["remind_at"])
        fields["reminded_at"] = None
    if "priority" in fields:
        fields["priority"] = _norm_priority(fields["priority"])
    if "status" in fields:
        if fields["status"] not in ("open", "done"):
            raise ValueError("status must be open or done")
        fields["completed_at"] = db.now() if fields["status"] == "done" else None
    if fields:
        with db.session() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         [*fields.values(), task_id])
    return get(task_id)


def delete(task_id: int) -> dict[str, Any]:
    task = get(task_id)
    with db.session() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return task


def list_tasks(status: str | None = "open", *, due_before: str | None = None, project: str | None = None,
               limit: int = 200) -> list[dict[str, Any]]:
    where, params = [], []
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    if due_before:
        where.append("due_at IS NOT NULL AND due_at < ?")
        params.append(due_before)
    if project:
        where.append("lower(project) = lower(?)")
        params.append(project)
    sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY status = 'done', due_at IS NULL, due_at, priority DESC, id LIMIT ?"
    with db.session() as conn:
        rows = conn.execute(sql, [*params, limit]).fetchall()
    return db.rows_to_dicts(rows)


def find(text: str) -> dict[str, Any] | None:
    """Find the open task whose title best matches `text`."""
    q = db.fts_query(text)
    words = [w.strip('"*') for w in q.split(" OR ")] if q else []
    best, best_score = None, 0
    for t in list_tasks("open"):
        title = t["title"].lower()
        score = sum(1 for w in words if w and w in title)
        if score > best_score:
            best, best_score = t, score
    return best


def score(task: dict[str, Any], now: datetime | None = None) -> float:
    """Urgency x importance used to rank the dashboard and daily plan."""
    now = now or datetime.now(timezone.utc)
    s = task["priority"] * 10.0
    if task.get("due_at"):
        hours = (datetime.fromisoformat(task["due_at"]) - now).total_seconds() / 3600
        if hours < 0:
            s += 100
        elif hours < 24:
            s += 60
        elif hours < 72:
            s += 30
        elif hours < 24 * 7:
            s += 10
    return s


def prioritized(limit: int = 20) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    items = list_tasks("open")
    for t in items:
        t["score"] = score(t, now)
    items.sort(key=lambda t: t["score"], reverse=True)
    return items[:limit]


def plan_day(available_minutes: int = 360) -> dict[str, Any]:
    """Greedy schedule of the highest-scoring open tasks, starting next hour and ending by 22:00.

    Late in the evening the plan is made for tomorrow from 09:00 instead.
    """
    now = datetime.now().astimezone()
    cursor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    day = "today"
    if cursor.hour >= 21 or cursor.date() != now.date():
        cursor = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        day = "tomorrow"
    day_end = cursor.replace(hour=22, minute=0)
    blocks, used = [], 0
    for t in prioritized(50):
        minutes = t.get("estimate_minutes") or 45
        if used + minutes > available_minutes or cursor + timedelta(minutes=minutes) > day_end:
            continue
        blocks.append({"task_id": t["id"], "title": t["title"], "start": timeparse.iso(cursor),
                       "end": timeparse.iso(cursor + timedelta(minutes=minutes)), "minutes": minutes,
                       "due_at": t.get("due_at")})
        cursor += timedelta(minutes=minutes + 10)
        used += minutes
    return {"day": day, "blocks": blocks, "planned_minutes": used, "available_minutes": available_minutes}


def due_reminders() -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'open' AND remind_at IS NOT NULL AND remind_at <= ? AND reminded_at IS NULL",
            (db.now(),),
        ).fetchall()
    return db.rows_to_dicts(rows)


def mark_reminded(task_id: int) -> None:
    with db.session() as conn:
        conn.execute("UPDATE tasks SET reminded_at = ? WHERE id = ?", (db.now(), task_id))
