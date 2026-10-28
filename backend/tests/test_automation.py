"""Automation engine, project tracking, calendar/email/web integrations and the credential vault."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------- workflows

def test_spec_example_becomes_a_weekly_workflow(client):
    d = client.post("/api/workflows/draft", json={
        "text": "Every Friday, organize my downloads, summarize my completed tasks, and prepare a plan for next week."}).json()
    assert d["trigger"] == {"type": "weekly", "days": ["fri"], "time": "09:00"}
    kinds = [(s["type"], s.get("tool")) for s in d["steps"]]
    assert kinds[0] == ("tool", "organize_files")
    assert ("tool", "daily_summary") in kinds
    assert d["trigger_text"] == "Every Fri at 09:00"
    saved = client.post("/api/workflows", json={k: d[k] for k in ("name", "trigger", "conditions", "steps")}).json()
    assert saved["enabled"] and saved["next_run_at"]
    nxt = datetime.fromisoformat(saved["next_run_at"]).astimezone()
    assert nxt.weekday() == 4 and nxt.strftime("%H:%M") == "09:00"


@pytest.mark.parametrize("text,trigger", [
    ("every morning give me my daily summary", {"type": "daily", "time": "08:00"}),
    ("every 2 hours list my tasks", {"type": "interval", "minutes": 120}),
    ("every weekday at 7:30am plan my day", {"type": "weekly", "days": ["mon", "tue", "wed", "thu", "fri"], "time": "07:30"}),
    ("monthly find large files", {"type": "monthly", "day": 1, "time": "09:00"}),
    ("when LifeOS starts summarize today", {"type": "startup"}),
    ("find duplicate files", {"type": "manual"}),
])
def test_rule_triggers(client, text, trigger):
    assert client.post("/api/workflows/draft", json={"text": text}).json()["trigger"] == trigger


def test_unknown_clause_becomes_crew_mission(client):
    d = client.post("/api/workflows/draft", json={"text": "every sunday at 6pm write a reflection on my week"}).json()
    assert d["trigger"] == {"type": "weekly", "days": ["sun"], "time": "18:00"}
    assert d["steps"][0]["type"] == "mission" and "reflection" in d["steps"][0]["goal"]


def test_next_run_math():
    from lifeos import workflows

    base = datetime(2026, 10, 28, 10, 0).astimezone()  # a Wednesday
    nr = lambda t: datetime.fromisoformat(workflows.next_run(workflows.validate_trigger(t), base)).astimezone()
    assert nr({"type": "daily", "time": "09:00"}).day == 29
    assert nr({"type": "daily", "time": "11:00"}).day == 28
    assert nr({"type": "weekly", "days": ["wed"], "time": "09:00"}).day == 4  # next week
    assert nr({"type": "monthly", "day": 31, "time": "09:00"}).day == 31  # October has 31 days
    assert (nr({"type": "interval", "minutes": 90}) - base) == timedelta(minutes=90)
    assert workflows.next_run({"type": "manual"}) is None


def test_workflow_run_conditions_templates_retries_and_summary(client, approved):
    wf = client.post("/api/workflows", json={
        "name": "Test flow", "trigger": {"type": "manual"},
        "conditions": [{"metric": "loose_files", "folder": "Downloads", "op": ">=", "value": 3}],
        "steps": [
            {"id": "find", "type": "tool", "tool": "search_files", "args": {"extensions": ["pdf"]}},
            {"id": "bad", "type": "tool", "tool": "complete_task", "args": {"title": "no such task"}, "retries": 1,
             "retry_delay_s": 1, "on_error": "continue"},
            {"id": "skip", "type": "tool", "tool": "plan_day", "args": {},
             "condition": {"metric": "open_tasks", "op": ">", "value": 100}},
            {"id": "tell", "type": "notify", "title": "Report {{date}}", "message": "PDFs: {{steps.find.summary}}"},
        ]}).json()
    from lifeos import workflows

    run = workflows.execute(wf["id"])
    assert run["status"] == "done"
    by_step = {e["step"]: e for e in run["log"] if e.get("step") and e["status"] != "retry"}
    assert by_step["find"]["status"] == "ok"
    assert by_step["bad"]["status"] == "error" and by_step["bad"]["attempts"] == 2
    assert any(e["status"] == "retry" for e in run["log"])
    assert by_step["skip"]["status"] == "skipped"
    notes = client.get("/api/notifications").json()
    step_note = next(n for n in notes if n["title"].startswith("Report "))
    assert "invoice_march.pdf" in step_note["body"]
    assert any(n["title"] == "Workflow finished: Test flow" for n in notes)
    assert client.get(f"/api/workflows/{wf['id']}/runs").json()[0]["id"] == run["id"]

    client.patch(f"/api/workflows/{wf['id']}", json={"conditions": [
        {"metric": "loose_files", "folder": "Downloads", "op": ">", "value": 999}]})
    assert workflows.execute(wf["id"])["status"] == "skipped"


def test_on_error_stop_and_approval_steps(client, approved):
    from lifeos import workflows

    wf = client.post("/api/workflows", json={"name": "Stop", "trigger": {"type": "manual"}, "steps": [
        {"type": "tool", "tool": "organize_files", "args": {"folder": "Downloads"}},
        {"type": "tool", "tool": "complete_task", "args": {"title": "missing"}},
        {"type": "tool", "tool": "plan_day", "args": {}},
    ]}).json()
    run = workflows.execute(wf["id"])
    assert run["status"] == "failed"
    statuses = [e["status"] for e in run["log"]]
    assert statuses == ["pending", "error"]  # third step never ran
    assert client.get("/api/actions", params={"status": "pending"}).json()[0]["tool"] == "organize_files"
    assert (approved["downloads"] / "invoice_march.pdf").exists()


def test_scheduler_runs_due_workflows_and_respects_pause(client):
    from lifeos import db, workflows

    wf = client.post("/api/workflows", json={"name": "Due", "trigger": {"type": "interval", "minutes": 30},
                                             "steps": [{"type": "tool", "tool": "list_tasks", "args": {}}]}).json()
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    with db.session() as conn:
        conn.execute("UPDATE workflows SET next_run_at = ? WHERE id = ?", (past, wf["id"]))
    client.post("/api/settings/pause", json={"paused": True})
    assert workflows.run_due() == 0
    client.post("/api/settings/pause", json={"paused": False})
    assert workflows.run_due() == 1
    assert workflows.run_due() == 0  # schedule moved forward
    import time

    for _ in range(100):
        if workflows.runs(wf["id"]) and workflows.runs(wf["id"])[0]["status"] == "done":
            break
        time.sleep(0.05)
    assert workflows.runs(wf["id"])[0]["trigger"] == "schedule"


def test_workflow_validation_errors(client):
    bad = [
        {"name": "x", "trigger": {"type": "interval", "minutes": 1}, "steps": [{"type": "tool", "tool": "list_tasks"}]},
        {"name": "x", "trigger": {"type": "daily", "time": "25:00"}, "steps": [{"type": "tool", "tool": "list_tasks"}]},
        {"name": "x", "steps": [{"type": "tool", "tool": "run_shell"}]},
        {"name": "x", "steps": [{"type": "tool", "tool": "create_task", "args": {}}]},
        {"name": "x", "steps": []},
        {"name": "x", "conditions": [{"metric": "cpu", "op": ">", "value": 1}],
         "steps": [{"type": "tool", "tool": "list_tasks"}]},
    ]
    for body in bad:
        assert client.post("/api/workflows", json=body).status_code == 400, body


def test_templates_are_valid(client):
    from lifeos import workflows

    for t in client.get("/api/workflows/templates").json():
        workflows._fields(t, partial=False)


def test_mission_step_runs_the_crew(client, approved):
    from lifeos import workflows

    wf = client.post("/api/workflows", json={"name": "Crew", "trigger": {"type": "manual"}, "steps": [
        {"type": "mission", "goal": "Find duplicate files"}]}).json()
    run = workflows.execute(wf["id"])
    assert run["status"] == "done"
    mid = run["log"][0]["mission_id"]
    assert client.get(f"/api/missions/{mid}").json()["source"] == "workflow"


# ---------------------------------------------------------------- projects

def test_projects_are_detected_with_context(client, approved):
    proj = approved["downloads"] / "code" / "thesis"
    proj.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='thesis'\n")
    (proj / "README.md").write_text("Thesis about graph neural networks.")
    (proj / "train.py").write_text("print('hi')")
    (approved["downloads"] / "code" / "site").mkdir()
    (approved["downloads"] / "code" / "site" / "package.json").write_text("{}")
    client.post("/api/files/index")
    res = client.post("/api/projects/detect").json()
    assert res["added"] == 2
    items = {p["name"]: p for p in client.get("/api/projects").json()}
    assert items["thesis"]["kind"] == "python" and items["site"]["kind"] == "node"
    assert items["thesis"]["last_activity_at"]
    client.post("/api/tasks", json={"title": "Write chapter 2", "project": "thesis"})
    ctx = client.get(f"/api/projects/{items['thesis']['id']}").json()
    assert ctx["open_tasks"][0]["title"] == "Write chapter 2"
    assert "graph neural" in ctx["readme"]
    assert {f["name"] for f in ctx["recent_files"]} >= {"train.py", "README.md"}
    from lifeos import tools

    out = tools.invoke("project_context", {"name": "thesis"}, {})
    assert out["status"] == "done" and "1 open task" in out["summary"]
    sugg = client.get("/api/suggestions").json()
    assert any(s["title"] == "Continue thesis" for s in sugg)
    client.patch(f"/api/projects/{items['site']['id']}", json={"status": "done"})
    assert [p["name"] for p in client.get("/api/projects", params={"status": "active"}).json()] == ["thesis"]


def test_projects_must_be_inside_approved_folders(client, env):
    outside = env["tmp"] / "elsewhere"
    outside.mkdir()
    assert client.post("/api/projects", json={"path": str(outside)}).status_code == 403


# ---------------------------------------------------------------- calendar

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:W. Europe Standard Time
END:VTIMEZONE
BEGIN:VEVENT
UID:standup@example
SUMMARY:Standup
DTSTART;TZID=W. Europe Standard Time:{d1}T090000
DTEND;TZID=W. Europe Standard Time:{d1}T093000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4
END:VEVENT
BEGIN:VEVENT
UID:dentist@example
SUMMARY:Dentist\\, Dr. Who
LOCATION:Main St
DTSTART:{d2}T080000Z
DTEND:{d2}T090000Z
END:VEVENT
BEGIN:VEVENT
UID:clash@example
SUMMARY:Call with Sam
DTSTART:{d2}T083000Z
DTEND:{d2}T084500Z
END:VEVENT
BEGIN:VEVENT
UID:holiday@example
SUMMARY:Holiday
DTSTART;VALUE=DATE:{d3}
END:VEVENT
END:VCALENDAR
"""


def _ics(days: tuple[int, int, int]) -> str:
    today = datetime.now(timezone.utc).date()
    fmt = lambda n: (today + timedelta(days=n)).strftime("%Y%m%d")
    return ICS.format(d1=fmt(days[0]), d2=fmt(days[1]), d3=fmt(days[2]))


def test_ics_parsing_recurrence_and_timezones():
    from lifeos import integrations

    events = integrations.parse_ics(_ics((1, 2, 3)))
    standups = [e for e in events if e["title"] == "Standup"]
    assert len(standups) == 4
    assert all(datetime.fromisoformat(e["start_at"]).weekday() in (0, 2) for e in standups[1:])
    first = datetime.fromisoformat(standups[0]["start_at"])
    assert first.hour in (7, 8)  # 09:00 Berlin is 07:00 or 08:00 UTC
    dentist = next(e for e in events if e["title"].startswith("Dentist"))
    assert dentist["title"] == "Dentist, Dr. Who" and dentist["location"] == "Main St"
    assert next(e for e in events if e["title"] == "Holiday")["all_day"] == 1


def test_calendar_source_overlaps_and_export(client, approved):
    path = approved["downloads"] / "work.ics"
    path.write_text(_ics((1, 2, 3)))
    src = client.post("/api/calendar/sources", json={"location": str(path), "label": "Work"}).json()
    assert src["event_count"] >= 6 and src["last_error"] is None
    clashes = client.get("/api/calendar/overlaps").json()
    assert any({c["a"]["title"], c["b"]["title"]} == {"Dentist, Dr. Who", "Call with Sam"} for c in clashes)
    assert any("calendar conflict" in s["title"] for s in client.get("/api/suggestions").json())
    client.post("/api/events", json={"title": "Gym", "start": "tomorrow 6pm"})
    client.post("/api/tasks", json={"title": "Essay", "due_at": "friday 5pm"})
    ics = client.get("/api/calendar/export.ics").text
    assert "SUMMARY:Gym" in ics and "SUMMARY:Due: Essay" in ics and ics.startswith("BEGIN:VCALENDAR")
    from lifeos import tools

    assert "Standup" in tools.invoke("list_events", {"days_ahead": 7}, {})["summary"]
    outside = approved["tmp"] / "x.ics"
    outside.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR")
    assert client.post("/api/calendar/sources", json={"location": str(outside)}).status_code == 403


def test_email_requires_configuration(client):
    assert client.get("/api/email/status").json()["configured"] is False
    from lifeos import tools

    out = tools.invoke("check_email", {}, {})
    assert out["status"] == "error" and "No email account" in out["summary"]
    st = client.put("/api/email", json={"host": "imap.example.com", "username": "me", "password": "pw"}).json()
    assert st["configured"] is True and "pw" not in json.dumps(st)


# ---------------------------------------------------------------- web and vault

def test_web_tools_are_off_by_default_and_block_private_hosts(client):
    from lifeos import tools, web

    out = tools.invoke("web_search", {"query": "pytest"}, {})
    assert out["status"] == "error" and "turned off" in out["summary"]
    client.patch("/api/settings", json={"web_access": True})
    for url in ("http://127.0.0.1:8765/api/settings", "http://localhost/", "http://10.0.0.1/", "file:///etc/passwd"):
        with pytest.raises(web.WebError):
            web.fetch(url)
    assert tools.invoke("fetch_url", {"url": "http://127.0.0.1/"}, {})["status"] == "pending"  # needs approval first


def test_html_to_text():
    from lifeos import web

    title, text = web.html_to_text("<html><head><title>Hi &amp; bye</title><script>evil()</script></head>"
                                   "<body><h1>Head</h1><p>Para <b>one</b></p><style>x{}</style></body></html>")
    assert title == "Hi & bye" and "evil" not in text and "Para one" in text


def test_vault_file_fallback(env):
    from lifeos import config, vault

    vault.set_secret("provider:groq", "gsk-123")
    assert vault.get_secret("provider:groq") == "gsk-123"
    raw = (config.data_dir() / "secrets.bin").read_bytes()
    assert b"gsk-123" not in raw
    vault.delete_secret("provider:groq")
    assert vault.get_secret("provider:groq") is None


def test_write_file_and_outbox_tools(client, approved):
    from lifeos import tools

    out = tools.invoke("write_file", {"path": "Downloads/plan.md", "content": "# Plan"}, {})
    assert out["status"] == "pending"
    assert not (approved["downloads"] / "plan.md").exists()
    done = client.post(f"/api/actions/{out['action']['id']}/approve", json={}).json()
    assert done["status"] == "executed" and (approved["downloads"] / "plan.md").read_text() == "# Plan"
    client.post(f"/api/actions/{out['action']['id']}/undo")
    assert not (approved["downloads"] / "plan.md").exists()
    assert tools.invoke("write_file", {"path": str(approved["tmp"] / "x.md"), "content": "x"}, {})["status"] == "error"
    saved = tools.invoke("save_to_outbox", {"filename": "../../evil.md", "content": "hi"}, {})
    assert saved["status"] == "done"
    assert saved["result"]["path"].endswith("evil.md") and "/outbox/" in saved["result"]["path"].replace("\\", "/")
