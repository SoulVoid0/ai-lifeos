"""Workspaces and routines, custom tools, rename by content, task breakdown, backups and cloud folders."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest


def test_workspace_open_routine_and_automate(client, env, monkeypatch):
    from lifeos import db, tools, workspaces

    launched = []
    monkeypatch.setattr(workspaces, "_launch", lambda item: launched.append(item["target"]))
    notes = env["downloads"] / "notes.txt"
    bad = client.post("/api/workspaces", json={"name": "Study", "items": [{"kind": "app", "target": str(notes)}]})
    assert bad.status_code == 400 and "program or shortcut" in bad.json()["detail"]
    assert client.post("/api/workspaces", json={"name": "x", "items": [{"kind": "url", "target": "javascript:1"}]}).status_code == 400
    w = client.post("/api/workspaces", json={"name": "Study", "items": [
        {"kind": "file", "target": str(notes)}, {"kind": "folder", "target": str(env["downloads"])},
        {"kind": "url", "target": "https://example.com/course"}, {"kind": "app", "target": "spotify:"}]}).json()
    assert len(w["items"]) == 4

    # "Prepare my workspace for studying" finds "Study".
    r = client.post("/api/chat", json={"message": "Prepare my workspace for studying"}).json()
    assert "Opened the Study workspace" in r["message"]["content"]
    assert launched == [str(notes), str(env["downloads"]), "https://example.com/course", "spotify:"]
    assert "Study" in tools.TOOLS["open_workspace"].text()

    # Opened on three mornings -> a routine suggestion with a one-click workflow.
    with db.session() as conn:
        for days_ago in (1, 2, 3):
            d = (datetime.now().astimezone().replace(hour=8, minute=5) - timedelta(days=days_ago)).astimezone(timezone.utc)
            conn.execute("INSERT INTO workspace_opens(workspace_id, opened_at, by) VALUES (?,?, 'user')",
                         (w["id"], d.isoformat(timespec="seconds")))
    r = workspaces.routine(w["id"])
    assert r and r["time"] == "08:00" and r["mornings"] >= 3
    sugg = [s for s in client.get("/api/suggestions").json() if s["id"] == f"routine-ws-{w['id']}"]
    assert sugg and sugg[0]["action"]["path"] == f"/api/workspaces/{w['id']}/automate"
    flow = client.post(sugg[0]["action"]["path"], json=sugg[0]["action"]["body"]).json()
    assert flow["steps"][0]["tool"] == "open_workspace" and flow["trigger"]["type"] == "weekly"
    assert not [s for s in client.get("/api/suggestions").json() if s["id"].startswith("routine-ws-")]


def test_custom_webhook_needs_approval_and_prompt_tool(client, monkeypatch):
    from lifeos import custom_tools, llm, tools

    bad = client.post("/api/custom-tools", json={"name": "Lights", "kind": "webhook", "config": {"url": "ftp://x"}})
    assert bad.status_code == 400
    t = client.post("/api/custom-tools", json={
        "name": "Lights", "kind": "webhook", "description": "Turn lights on or off",
        "config": {"url": "https://ha.example/api/hook?scene={{input}}", "method": "POST", "body": "{\"say\": \"{{input}}\"}",
                   "secret_header": "Authorization"}, "secret": "Bearer abc"}).json()
    assert t["has_secret"] and "abc" not in str(t)
    sent = []

    def fake_request(method, url, json=None, headers=None, **kw):
        sent.append((method, url, json, headers))
        return httpx.Response(200, text="ok", request=httpx.Request(method, url))

    monkeypatch.setattr(custom_tools.httpx, "request", fake_request)
    out = tools.invoke("custom_webhook", {"name": "lights", "input": 'movie "night"'}, {})
    assert out["status"] == "pending" and not sent
    assert "scene=movie%20%22night%22" in out["action"]["preview"]["request"]["url"]
    done = client.post(f"/api/actions/{out['action']['id']}/approve", json={}).json()
    assert done["status"] == "executed"
    assert sent[0][2] == {"say": 'movie "night"'} and sent[0][3]["Authorization"] == "Bearer abc"

    exported = client.get(f"/api/custom-tools/{t['id']}/export").json()
    assert "secret" not in str(exported).lower() or "secret_header" in str(exported)
    assert "abc" not in str(exported)

    p = client.post("/api/custom-tools", json={"name": "Weekly report", "kind": "prompt",
                                               "config": {"template": "Rewrite as a weekly report: {{input}}"}}).json()
    monkeypatch.setattr(llm, "chat_text", lambda messages, **kw: "REPORT: " + messages[0]["content"])
    out = tools.invoke("custom_prompt", {"name": "Weekly report", "input": "did stuff"}, {})
    assert out["status"] == "done" and "weekly report: did stuff" in out["summary"]
    assert client.post("/api/custom-tools", json={"name": "Bad", "kind": "prompt", "config": {"template": "no slot"}}).status_code == 400
    client.delete(f"/api/custom-tools/{p['id']}")


def test_rename_by_content_preview_approve_undo(client, env):
    from lifeos import tools

    out = tools.invoke("rename_by_content", {"folder": "Downloads"}, {})
    assert out["status"] == "pending"
    moves = out["action"]["preview"]["moves"]
    names = {Path(m["src"]).name: Path(m["dest"]).name for m in moves}
    assert "scan001.pdf" in names and names["scan001.pdf"].endswith(".pdf")
    assert "invoice_march.pdf" not in names  # already has a real name
    client.post(f"/api/actions/{out['action']['id']}/approve", json={})
    assert not (env["downloads"] / "scan001.pdf").exists()
    client.post(f"/api/actions/{out['action']['id']}/undo")
    assert (env["downloads"] / "scan001.pdf").exists()


def test_break_down_replan_and_behind(client):
    from lifeos import db, tasks, tools

    due = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat(timespec="seconds")
    t = tasks.create("History essay", due=due, estimate_minutes=480)
    out = tools.invoke("break_down_task", {"title": "History essay"}, {})
    assert out["status"] == "done"
    subs = out["result"]["subtasks"]
    assert len(subs) == 4 and all(s["parent_id"] == t["id"] for s in subs)
    assert subs[-1]["due_at"] <= tasks.get(t["id"])["due_at"]
    assert sum(s["estimate_minutes"] for s in subs) == pytest.approx(480, abs=30)

    with db.session() as conn:  # the first step slipped
        conn.execute("UPDATE tasks SET due_at = ? WHERE id = ?",
                     ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"), subs[0]["id"]))
    assert [b["id"] for b in tasks.behind_schedule()] == [subs[0]["id"]]
    s = [x for x in client.get("/api/suggestions").json() if x["id"].startswith("behind-")]
    assert s and s[0]["action"]["path"] == f"/api/tasks/{t['id']}/replan"
    client.post(s[0]["action"]["path"], json={})
    assert tasks.behind_schedule() == []

    undo = tools.TOOLS["break_down_task"].undo({"subtasks": subs})
    assert "Removed 4" in undo["summary"]


def test_backup_and_restore(client, env, tmp_path, monkeypatch):
    from lifeos import backup, config, settings, tasks

    tasks.create("Keep me")
    assert client.post("/api/backups", json={}).status_code == 400  # no folder yet
    settings.update({"backup_folder": str(tmp_path / "OneDrive" / "LifeOS")})
    made = client.post("/api/backups", json={}).json()
    assert Path(made["path"]).name.startswith(backup.PREFIX)
    listed = client.get("/api/backups").json()
    assert listed["backups"][0]["path"] == made["path"]

    tasks.create("Made after the backup")
    client.post("/api/backups/restore", json={"path": made["path"]})
    assert client.get("/api/backups").json()["pending_restore"] == made["path"]
    # Next start: the backup replaces the database (tests use LIFEOS_DB inside the data folder under another name).
    monkeypatch.setattr(config, "db_path", lambda: config.data_dir() / "lifeos.db")
    assert backup.restore_pending() == Path(made["path"])
    con = sqlite3.connect(config.data_dir() / "lifeos.db")
    titles = {r[0] for r in con.execute("SELECT title FROM tasks")}
    con.close()
    assert "Keep me" in titles and "Made after the backup" not in titles
    assert list(config.data_dir().glob("backup-before-restore-*"))

    assert client.post("/api/backups/restore", json={"path": str(env["downloads"] / "notes.txt")}).status_code == 400


def test_cloud_folders_and_online_only_files(env, tmp_path, monkeypatch):
    from lifeos import cloud, permissions

    od = tmp_path / "OneDrive"
    od.mkdir()
    monkeypatch.setenv("OneDrive", str(od))
    found = {f["path"]: f for f in cloud.folders()}
    assert found[str(od.resolve())]["provider"] == "OneDrive" and not found[str(od.resolve())]["approved"]
    permissions.add_folder(str(od))
    assert next(f for f in cloud.folders() if f["path"] == str(od.resolve()))["approved"]

    class St:
        st_file_attributes = 0x00400000

    assert cloud.is_cloud_only(St()) and not cloud.is_cloud_only(os.stat(od))
