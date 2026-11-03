"""Tasks, deadlines and reminders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, timeparse

PRIORITIES = {"low": 1, "normal": 2, "medium": 2, "high": 3, "urgent": 4}
UPDATABLE = {"title", "notes", "project", "priority", "status", "due_at", "remind_at", "estimate_minutes"}


# Words small models use for "no date"; treated as no date instead of an error.
NO_DATE = {"null", "none", "never", "no", "n/a", "na", "tbd", "unknown", "anytime", "whenever", "no deadline", "nil",
           "not set", "undefined", "false", "-"}


def _norm_time(value: Any) -> str | None:
    if value in (None, "") or str(value).strip().lower() in NO_DATE:
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
           due: Any = None, remind: Any = None, estimate_minutes: int | None = None,
           parent_id: int | None = None) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("Task title is required")
    due_at, remind_at = _norm_time(due), _norm_time(remind)
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(title, notes, project, priority, due_at, remind_at, estimate_minutes, created_at, parent_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (title, notes, project, _norm_priority(priority), due_at, remind_at, estimate_minutes, db.now(), parent_id),
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


# ---------------------------------------------------------------- breaking work down

# Used when no model is available: steps that fit most assignments and projects.
_GENERIC_STEPS = [("Gather material and outline", 0.2), ("First draft / main work", 0.45), ("Review and fix", 0.2),
                  ("Finish and hand in", 0.15)]


def _model_steps(task: dict[str, Any], parts: int | None) -> list[dict[str, Any]]:
    from . import llm, settings

    cfg = settings.get_all()
    model = cfg.get("fast_model") or cfg["chat_model"]
    if cfg["planner"] == "rules" or not llm.model_available(model):
        return []
    schema = {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "minutes": {"type": "integer"}}, "required": ["title", "minutes"]}}},
        "required": ["steps"]}
    prompt = (f"Break this task into {parts or '3 to 7'} concrete steps someone can do one at a time, in order, each "
              f"with a realistic time estimate in minutes.\nTask: {task['title']}\n"
              + (f"Notes: {task['notes'][:800]}\n" if task.get("notes") else ""))
    try:
        data = llm.chat_json([{"role": "user", "content": prompt}], schema, model=model, timeout=300)
    except Exception:  # noqa: BLE001 - fall back to the generic steps
        return []
    steps = [{"title": str(s.get("title") or "").strip()[:120], "minutes": max(5, min(int(s.get("minutes") or 30), 600))}
             for s in data.get("steps") or [] if isinstance(s, dict) and str(s.get("title") or "").strip()]
    return steps[:10]


def break_down(task_id: int, parts: int | None = None) -> dict[str, Any]:
    """Split a task into ordered subtasks with estimates, their due dates spread out before the task's own."""
    task = get(task_id)
    steps = _model_steps(task, parts)
    if not steps:
        total = task.get("estimate_minutes") or 240
        steps = [{"title": f"{name}: {task['title']}", "minutes": max(15, round(total * share / 15) * 15)}
                 for name, share in _GENERIC_STEPS]
    now = datetime.now(timezone.utc)
    due = datetime.fromisoformat(task["due_at"]) if task.get("due_at") else None
    created = []
    for i, step in enumerate(steps, 1):
        step_due = None
        if due and due > now:
            step_due = timeparse.iso(now + (due - now) * i / len(steps))
        created.append(create(step["title"], project=task.get("project"), priority=task["priority"],
                              due=step_due, estimate_minutes=step["minutes"], parent_id=task_id,
                              notes=f"Step {i} of {len(steps)} for: {task['title']}"))
    total = sum(s["minutes"] for s in steps)
    if not task.get("estimate_minutes"):
        update(task_id, {"estimate_minutes": total})
    return {"task": get(task_id), "subtasks": created, "total_minutes": total}


def behind_schedule() -> list[dict[str, Any]]:
    """Open subtasks whose own due date passed while their parent is still due later: the plan needs updating."""
    now = timeparse.iso(datetime.now(timezone.utc))
    with db.session() as conn:
        rows = conn.execute(
            "SELECT s.* FROM tasks s JOIN tasks p ON p.id = s.parent_id WHERE s.status = 'open' AND p.status = 'open' "
            "AND s.due_at IS NOT NULL AND s.due_at < ? AND (p.due_at IS NULL OR p.due_at > ?)", (now, now)).fetchall()
    return db.rows_to_dicts(rows)


def replan(parent_id: int) -> dict[str, Any]:
    """Spread the open steps of a task over the time left before its due date (or the next days)."""
    parent = get(parent_id)
    with db.session() as conn:
        rows = db.rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE parent_id = ? AND status = 'open' ORDER BY id",
                                             (parent_id,)).fetchall())
    if not rows:
        return {"task": parent, "moved": []}
    now = datetime.now(timezone.utc)
    end = datetime.fromisoformat(parent["due_at"]) if parent.get("due_at") else now + timedelta(days=len(rows))
    if end <= now:
        end = now + timedelta(days=len(rows))
    moved = []
    for i, t in enumerate(rows, 1):
        new_due = timeparse.iso(now + (end - now) * i / len(rows))
        moved.append(update(t["id"], {"due_at": new_due}))
    return {"task": parent, "moved": moved}
