"""Files, tasks, reminders, memory, search."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone


def test_file_search_by_content(client):
    r = client.post("/api/chat", json={"message": "Find the PDF about JavaScript testing that I downloaded a few months ago"}).json()
    files = r["message"]["data"]["steps"][0]["result"]["files"]
    assert files[0]["name"] == "js-testing-guide.pdf"


def test_large_old_and_duplicate_files(client, approved):
    d = approved["downloads"]
    big = d / "movie.mkv"
    with open(big, "wb") as fh:
        fh.truncate(3 * 1024 * 1024)
    old = time.time() - 400 * 86400
    os.utime(big, (old, old))
    (d / "copy.txt").write_text((d / "notes.txt").read_text())
    client.post("/api/files/index")

    large = client.get("/api/files/large", params={"min_mb": 1, "older_than_days": 90}).json()
    assert [f["name"] for f in large] == ["movie.mkv"]
    dupes = client.get("/api/files/duplicates").json()
    assert {f["name"] for f in dupes[0]["files"]} == {"notes.txt", "copy.txt"}
    stale = client.get("/api/files/stale", params={"days": 365}).json()
    assert [f["name"] for f in stale] == ["movie.mkv"]


def test_tasks_and_reminders(client):
    from lifeos import scheduler

    t = client.post("/api/tasks", json={"title": "Pay rent", "remind_at": "in 1 minute"}).json()
    assert t["remind_at"]
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    client.patch(f"/api/tasks/{t['id']}", json={"remind_at": past})
    assert scheduler.fire_reminders() == 1
    assert scheduler.fire_reminders() == 0
    notes = client.get("/api/notifications").json()
    assert notes[0]["title"] == "Reminder: Pay rent"

    r = client.post("/api/chat", json={"message": "mark pay rent as done"}).json()
    assert r["message"]["data"]["steps"][0]["status"] == "done"
    assert client.get("/api/tasks", params={"status": "done"}).json()[0]["title"] == "Pay rent"


def test_plan_and_summary(client):
    client.post("/api/tasks", json={"title": "Math assignment", "due_at": "tomorrow 5pm", "estimate_minutes": 90})
    client.post("/api/tasks", json={"title": "Read chapter 3", "estimate_minutes": 30, "priority": "low"})
    r = client.post("/api/chat", json={"message": "Create a schedule for completing my assignments"}).json()
    plan = r["message"]["data"]["steps"][0]["result"]["plan"]
    assert plan["blocks"][0]["title"] == "Math assignment"
    r = client.post("/api/chat", json={"message": "Summarize everything important from today"}).json()
    assert "Summary for" in r["message"]["content"]


def test_memory_crud_and_recall(client):
    r = client.post("/api/chat", json={"message": "Remember that my thesis project lives in ~/code/thesis"}).json()
    assert r["message"]["data"]["steps"][0]["result"]["memory"]["kind"] == "project"
    mems = client.get("/api/memories", params={"q": "thesis"}).json()
    assert len(mems) == 1
    mid = mems[0]["id"]
    client.patch(f"/api/memories/{mid}", json={"content": "Thesis project is in ~/code/thesis-v2", "pinned": True})
    assert client.get("/api/memories").json()[0]["pinned"] == 1
    assert client.get("/api/memories", params={"q": "v2"}).json()[0]["id"] == mid
    client.delete(f"/api/memories/{mid}")
    assert client.get("/api/memories").json() == []


def test_universal_search(client):
    client.post("/api/chat", json={"message": "Remember that the fix for the pytest import error was PYTHONPATH"})
    r = client.post("/api/chat", json={"message": "What was the solution I found for my pytest error?"}).json()
    res = r["message"]["data"]["steps"][0]["result"]["results"]
    assert res["memories"] and "PYTHONPATH" in res["memories"][0]["content"]
    assert any(f["name"] == "notes.txt" for f in res["files"])
    assert res["messages"]


def test_unknown_request_without_model(client):
    r = client.post("/api/chat", json={"message": "Prepare my workspace for studying"}).json()
    assert "I can find files" in r["message"]["content"]


def test_settings_validation(client):
    assert client.patch("/api/settings", json={"planner": "magic"}).status_code == 400
    assert client.patch("/api/settings", json={"nope": 1}).status_code == 400
    assert client.patch("/api/settings", json={"proactive_level": "low"}).json()["proactive_level"] == "low"


def test_timeparse():
    from lifeos.timeparse import parse

    now = datetime(2026, 10, 27, 22, 0).astimezone()
    assert parse("tomorrow at 5pm", now).hour == 17
    assert parse("next friday", now).weekday() == 4
    assert parse("in 2 hours", now) == now + timedelta(hours=2)
    assert parse("nov 3", now).month == 11
    assert parse("nothing here", now) is None
