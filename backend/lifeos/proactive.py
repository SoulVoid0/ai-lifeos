"""Proactive suggestions, the daily summary and the dashboard payload."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import actions, activity, db, files, llm, memory, permissions, settings, tasks


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _local_day_bounds() -> tuple[str, str]:
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc(start), _utc(start + timedelta(days=1))


def suggestions() -> list[dict[str, Any]]:
    cfg = settings.get_all()
    level = cfg["proactive_level"]
    dismissed = set(cfg["dismissed_suggestions"])
    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    if not permissions.approved_roots():
        out.append({"id": "setup-folders", "kind": "setup", "priority": 3, "title": "Approve a folder",
                    "detail": "Choose folders LifeOS may index (for example Downloads and Documents).",
                    "link": "/settings"})
    if cfg["planner"] != "rules" and not llm.status()["ok"]:
        out.append({"id": "setup-ollama", "kind": "setup", "priority": 1, "title": "Connect a local model",
                    "detail": f"Ollama isn't reachable at {cfg['ollama_url']}. Install Ollama and run "
                              f"`ollama pull {cfg['chat_model']}` for free-form requests.", "link": "/settings"})
    if level == "off":
        return [s for s in out if s["id"] not in dismissed]

    open_tasks = tasks.list_tasks("open")
    overdue = [t for t in open_tasks if t["due_at"] and t["due_at"] < _utc(now)]
    if overdue:
        out.append({"id": f"overdue-{len(overdue)}-{overdue[0]['id']}", "kind": "deadline", "priority": 5,
                    "title": f"{len(overdue)} overdue task{'s' if len(overdue) > 1 else ''}",
                    "detail": ", ".join(t["title"] for t in overdue[:3]),
                    "command": "Plan my day"})
    for t in open_tasks:
        if t["due_at"] and _utc(now) <= t["due_at"] < _utc(now + timedelta(hours=48)):
            hours = int((datetime.fromisoformat(t["due_at"]) - now).total_seconds() // 3600)
            out.append({"id": f"due-{t['id']}", "kind": "deadline", "priority": 4,
                        "title": f"Start \"{t['title']}\"", "detail": f"Due in {hours} hour{'s' if hours != 1 else ''}.",
                        "task_id": t["id"]})
    if level == "low":
        return sorted([s for s in out if s["id"] not in dismissed], key=lambda s: -s["priority"])

    threshold = cfg["downloads_clutter_threshold"]
    with db.session() as conn:
        for fid, root in permissions.approved_roots():
            rows = conn.execute("SELECT path FROM files WHERE folder_id = ?", (fid,)).fetchall()
            loose = [r for r in rows if Path(r["path"]).parent == root]
            if len(loose) >= threshold:
                out.append({"id": f"clutter-{fid}-{len(loose) // 10}", "kind": "files", "priority": 2,
                            "title": f"Organize {root.name}", "detail": f"{len(loose)} loose files have accumulated.",
                            "command": f"Organize my {root.name} folder"})
        dupes = conn.execute(
            "SELECT COALESCE(SUM(size * (n - 1)), 0) AS waste FROM (SELECT sha256, size, COUNT(*) AS n FROM files "
            "WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING n > 1)").fetchone()["waste"]
    if dupes > 50 * 1024 * 1024:
        out.append({"id": f"dupes-{dupes // (50 * 1024 * 1024)}", "kind": "files", "priority": 2,
                    "title": "Duplicate files", "detail": f"About {dupes / 1024 ** 2:.0f} MB is used by duplicates.",
                    "command": "Find duplicate files"})
    big_old = files.large_files(cfg["large_file_mb"], cfg["stale_days"], limit=20)
    if big_old:
        size = sum(f["size"] for f in big_old) / 1024 ** 3
        out.append({"id": f"large-{len(big_old)}", "kind": "files", "priority": 1,
                    "title": "Large unused files", "detail": f"{len(big_old)} files ({size:.1f} GB) untouched for "
                                                            f"{cfg['stale_days']}+ days could be archived.",
                    "command": "Find large files I haven't used recently"})
    projects = memory.list_all("project")
    for p in projects[:3]:
        open_in_project = [t for t in open_tasks if t.get("project") and t["project"].lower() in p["content"].lower()]
        if open_in_project:
            out.append({"id": f"project-{p['id']}-{len(open_in_project)}", "kind": "project", "priority": 2,
                        "title": "Continue where you left off", "detail": f"{p['content']} — {len(open_in_project)} open task(s).",
                        "command": f"Show my tasks for {open_in_project[0]['project']}"})
    return sorted([s for s in out if s["id"] not in dismissed], key=lambda s: -s["priority"])


def dismiss(suggestion_id: str) -> None:
    current = settings.get("dismissed_suggestions")
    if suggestion_id not in current:
        settings.update({"dismissed_suggestions": (current + [suggestion_id])[-500:]})


def daily_summary() -> dict[str, Any]:
    start, end = _local_day_bounds()
    now = _utc(datetime.now())
    open_tasks = tasks.list_tasks("open")
    due_today = [t for t in open_tasks if t["due_at"] and start <= t["due_at"] < end]
    overdue = [t for t in open_tasks if t["due_at"] and t["due_at"] < now and t not in due_today]
    reminders_today = [t for t in open_tasks if t["remind_at"] and start <= t["remind_at"] < end]
    week_end = _utc(datetime.now() + timedelta(days=7))
    upcoming = [t for t in open_tasks if t["due_at"] and end <= t["due_at"] < week_end]
    with db.session() as conn:
        done_today = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE completed_at >= ?", (start,)).fetchone()["n"]
        actions_today = conn.execute("SELECT COUNT(*) AS n FROM actions WHERE status = 'executed' AND executed_at >= ?",
                                     (start,)).fetchone()["n"]
    pending = actions.list_actions("pending")
    sugg = suggestions()

    lines = [f"Summary for {datetime.now().astimezone().strftime('%A, %B %d')}:"]
    if overdue:
        lines.append(f"• Overdue: {', '.join(t['title'] for t in overdue[:5])}.")
    lines.append(f"• Due today: {', '.join(t['title'] for t in due_today) if due_today else 'nothing'}.")
    if reminders_today:
        lines.append(f"• Reminders today: {', '.join(t['title'] for t in reminders_today[:5])}.")
    if upcoming:
        lines.append(f"• This week: {', '.join(t['title'] for t in upcoming[:5])}.")
    lines.append(f"• Completed today: {done_today} task(s); {actions_today} action(s) carried out.")
    if pending:
        lines.append(f"• {len(pending)} action(s) waiting for your approval.")
    useful = [s for s in sugg if s["kind"] != "setup"]
    if useful:
        lines.append("• Suggested: " + "; ".join(s["title"] for s in useful[:3]) + ".")
    return {"text": "\n".join(lines), "due_today": due_today, "overdue": overdue, "upcoming": upcoming,
            "reminders_today": reminders_today, "completed_today": done_today, "pending_actions": len(pending)}


def dashboard() -> dict[str, Any]:
    cfg = settings.get_all()
    summary = daily_summary()
    with db.session() as conn:
        recent_files = db.rows_to_dicts(conn.execute(
            "SELECT id, path, name, ext, size, modified_at, category FROM files ORDER BY modified_at DESC LIMIT 8").fetchall())
        counts = {
            "files": conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"],
            "memories": conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"],
            "open_tasks": conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status = 'open'").fetchone()["n"],
            "folders": conn.execute("SELECT COUNT(*) AS n FROM approved_folders").fetchone()["n"],
        }
    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    if cfg["user_name"]:
        greeting += f", {cfg['user_name']}"
    return {
        "greeting": greeting,
        "summary": summary,
        "priorities": tasks.prioritized(8),
        "suggestions": suggestions(),
        "pending_actions": actions.list_actions("pending", 20),
        "recent_files": recent_files,
        "recent_activity": activity.recent(10),
        "counts": counts,
        "automation_paused": cfg["automation_paused"],
        "llm": llm.status(),
    }
