"""0.3 features: council, XP, mood, panic, PC doctor, learn mode, story mode, code-to-app, USB backup."""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest


def _fake_models(monkeypatch):
    from lifeos import llm

    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": ["llama3.1:8b", "qwen3:14b"], "url": "x"})
    monkeypatch.setattr(llm, "model_available", lambda ref=None: True)


def _wait(fn, cond, n=200):
    out = fn()
    for _ in range(n):
        if cond(out):
            return out
        time.sleep(0.03)
        out = fn()
    return out


# ---------------------------------------------------------------- council

def test_council_debates_votes_and_decides(client, monkeypatch):
    from lifeos import council, llm

    _fake_models(monkeypatch)
    monkeypatch.setattr(llm, "chat_text", lambda msgs, model=None, timeout=180: "I think we should buy the desktop.")
    calls = {"n": 0}

    def fake_json(msgs, schema, model=None, timeout=180):
        if "options" in schema["properties"]:
            return {"options": ["Buy the desktop", "Buy the laptop"], "summary": "They mostly prefer the desktop."}
        calls["n"] += 1
        return {"choice": 0 if calls["n"] != 2 else 1, "reason": "More power for the money."}

    monkeypatch.setattr(llm, "chat_json", fake_json)
    assert client.post("/api/council", json={"question": "Laptop or desktop?", "members": ["research"]}).status_code == 400
    s = client.post("/api/council", json={"question": "Should I buy a laptop or a desktop?",
                                          "members": ["research", "coder", "planner"], "rounds": 2}).json()
    s = _wait(lambda: client.get(f"/api/council/{s['id']}").json(), lambda x: x["status"] not in ("running", "voting"))
    assert s["status"] == "decided", s.get("error")
    assert len(s["transcript"]) == 6 and s["tally"] == [2, 1]
    assert s["decision"] == "Buy the desktop"
    assert client.post(f"/api/council/{s['id']}/choose", json={"option": 1}).json()["user_choice"] == "Buy the laptop"
    assert council.tally_winner([{"choice": 0}, {"choice": 1}], 2, tiebreak=1) == (1, True)


# ---------------------------------------------------------------- XP

def test_xp_levels_and_board(client):
    from lifeos import db, xp

    assert xp.level_for(0)[0] == 1 and xp.level_for(99)[0] == 1 and xp.level_for(100)[0] == 2 and xp.level_for(300)[0] == 3
    assert xp.rank_for(1) == "Cadet" and xp.rank_for(30) == "Fleet Admiral"
    with db.session() as conn:
        cur = conn.execute("INSERT INTO missions(goal, lead, status, created_at) VALUES ('x', 'overseer', 'done', ?)", (db.now(),))
        mid = cur.lastrowid
        for i in range(12):
            conn.execute("INSERT INTO mission_tasks(mission_id, key, agent, title, status, score, user_rating, started_at, finished_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (mid, f"t{i}", "research", "t", "done", 4.5, 5 if i < 2 else None,
                                                       "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"))
    assert xp.check_levels() == []  # first run only records the levels
    b = client.get("/api/xp").json()
    top = b["agents"][0]
    assert top["slug"] == "research" and top["xp"] == 12 * 15 + 2 * 15 and top["level"] == 2 and top["tasks_done"] == 12
    assert top["avg_score"] == 4.5 and top["work_seconds"] == 12 * 60
    with db.session() as conn:
        for i in range(10):
            conn.execute("INSERT INTO mission_tasks(mission_id, key, agent, title, status, score) VALUES (?,?,?,?,?,?)",
                         (mid, f"u{i}", "research", "t", "done", 5))
    ups = xp.check_levels()
    assert ups and ups[0]["slug"] == "research" and ups[0]["level"] == 3
    notes = client.get("/api/notifications").json()
    assert any("Level up" in n["title"] for n in notes)


# ---------------------------------------------------------------- mood

def test_mood_follows_time_and_activity(client):
    from datetime import datetime

    from lifeos import db, gamemode, mood

    assert mood.phase(datetime(2026, 1, 1, 23)) == "night" and mood.phase(datetime(2026, 1, 1, 6)) == "dawn"
    assert mood.phase(datetime(2026, 1, 1, 12)) == "day" and mood.phase(datetime(2026, 1, 1, 19)) == "dusk"
    m = client.get("/api/mood").json()
    assert m["activity"] == "idle" and m["palette"]["accent"].startswith("#")
    with db.session() as conn:
        conn.execute("INSERT INTO actions(tool, args, summary, status, created_at) VALUES ('x', '{}', 's', 'pending', ?)", (db.now(),))
    assert client.get("/api/mood").json()["activity"] == "alert"
    gamemode.set_manual(True)
    try:
        m = client.get("/api/mood").json()
        assert m["activity"] == "gaming" and m["music"]["on"] is False
    finally:
        gamemode.set_manual(None)
    m = client.patch("/api/mood", json={"music": True, "volume": 3}).json()
    assert m["config"]["music"] and m["config"]["volume"] == 1.0


# ---------------------------------------------------------------- panic

def test_panic_stops_everything_and_clears(client, monkeypatch):
    from lifeos import missions, settings, system

    stopped, vol = [], []
    monkeypatch.setattr(missions, "stop_all", lambda: stopped.append(1) or 3)
    monkeypatch.setattr(system, "set_volume", lambda level=None, change=None: vol.append(level))
    out = client.post("/api/panic").json()
    assert out["active"] and stopped and vol == [0] and out["hide_windows"]
    assert settings.get("automation_paused") is True and "paused automation" in out["done"]
    out = client.post("/api/panic/clear").json()
    assert not out["active"] and settings.get("automation_paused") is False
    # already paused by the user: all clear leaves it paused
    settings.update({"automation_paused": True})
    client.post("/api/panic")
    client.post("/api/panic/clear")
    assert settings.get("automation_paused") is True
    from lifeos import voice

    assert voice.route("panic")["kind"] == "panic"


# ---------------------------------------------------------------- PC doctor

def test_doctor_checkup_and_temp_cleanup(client, monkeypatch, tmp_path):
    import os
    import tempfile

    from lifeos import doctor

    r = client.post("/api/doctor/run").json()
    assert r["findings"] and {f["area"] for f in r["findings"]} >= {"disk", "memory", "temps", "crashes"}
    assert r["worst"] in ("ok", "info", "warn", "bad") and r["summary"]
    assert client.get("/api/doctor").json()["report"]["at"] == r["at"]
    tmp = tmp_path / "tmpdir" / "a" / "b"
    tmp.mkdir(parents=True)
    old, new = tmp / "old.log", tmp / "new.log"
    old.write_bytes(b"x" * 1000)
    new.write_bytes(b"y")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "tmpdir"))
    out = doctor.clean_temp(3)
    assert out["removed"] == 1 and out["freed"] == 1000 and not old.exists() and new.exists()


# ---------------------------------------------------------------- learn mode

def test_flashcards_make_review_and_check(client, monkeypatch):
    from lifeos import learn, llm

    _fake_models(monkeypatch)

    def fake_json(msgs, schema, model=None, timeout=180):
        if "cards" in schema["properties"]:
            return {"title": "Capitals", "cards": [{"front": "Capital of France?", "back": "Paris"},
                                                    {"front": "Capital of Japan?", "back": "Tokyo"},
                                                    {"front": "capital of france?", "back": "dupe"}]}
        return {"correct": True, "feedback": "Right.", "suggested_grade": 2}

    monkeypatch.setattr(llm, "chat_json", fake_json)
    d = client.post("/api/learn/decks", json={"topic": "world capitals", "count": 5}).json()
    d = _wait(lambda: client.get(f"/api/learn/decks/{d['id']}").json(), lambda x: x["status"] != "making")
    assert d["status"] == "ready" and d["title"] == "Capitals" and d["cards"] == 2
    q = client.get("/api/learn/queue").json()
    assert len(q) == 2
    assert client.post(f"/api/learn/cards/{q[0]['id']}/check", json={"answer": "paris"}).json()["suggested_grade"] == 3
    assert client.post(f"/api/learn/cards/{q[0]['id']}/check", json={"answer": "the city of Paris"}).json()["correct"]
    c = client.post(f"/api/learn/cards/{q[0]['id']}/review", json={"grade": 2}).json()
    assert c["reps"] == 1 and c["interval_days"] == 1.0
    c = client.post(f"/api/learn/cards/{q[1]['id']}/review", json={"grade": 0}).json()
    assert c["reps"] == 0
    assert learn.sm2(2.5, 6, 2, 2)[1] == pytest.approx(15.0)
    assert learn.sm2(2.5, 6, 2, 0) == (2.3, 0.0, 0)
    st = client.get("/api/learn").json()["stats"]
    assert st["cards"] == 2 and st["streak"] == 1 and st["reviews_week"] == 2
    tsv = client.get(f"/api/learn/decks/{d['id']}/export").text
    assert "Capital of France?\tParis" in tsv
    assert client.post(f"/api/learn/decks/{d['id']}/import", json={"text": "Q1\tA1\nQ2\tA2"}).json()["added"] == 2


# ---------------------------------------------------------------- story mode

def test_story_scenes_choices_and_pictures(client, monkeypatch):
    from lifeos import llm, media

    _fake_models(monkeypatch)
    n = {"i": 0}

    def fake_json(msgs, schema, model=None, timeout=180):
        n["i"] += 1
        return {"title": "The Lighthouse", "text": f"Scene {n['i']}: The fog rolls in over the old lighthouse. " * 3,
                "image_prompt": "a lighthouse in fog at night", "choices": ["Climb the stairs", "Knock", "Leave"],
                "ending": n["i"] >= 3}

    monkeypatch.setattr(llm, "chat_json", fake_json)
    pics = []
    monkeypatch.setattr(media, "generate", lambda kind, prompt, **kw: pics.append(prompt) or {"id": 999})
    s = client.post("/api/stories", json={"premise": "A keeper finds a message in a bottle", "style": "noir"}).json()
    s = _wait(lambda: client.get(f"/api/stories/{s['id']}").json(), lambda x: x["status"] != "writing")
    assert s["status"] == "ready" and s["title"] == "The Lighthouse" and len(s["scenes"][0]["choices"]) == 3
    assert pics and "film noir" in pics[0]
    client.post(f"/api/stories/{s['id']}/choose", json={"choice": "Climb the stairs"})
    s = _wait(lambda: client.get(f"/api/stories/{s['id']}").json(), lambda x: x["status"] != "writing")
    assert len(s["scenes"]) == 2 and s["scenes"][0]["chosen"] == "Climb the stairs"
    client.post(f"/api/stories/{s['id']}/choose", json={"choice": "Open the door"})
    s = _wait(lambda: client.get(f"/api/stories/{s['id']}").json(), lambda x: x["status"] != "writing")
    assert s["status"] == "ended" and s["scenes"][-1]["choices"] == []
    assert client.post(f"/api/stories/{s['id']}/choose", json={"choice": "more"}).status_code == 400
    md = client.get(f"/api/stories/{s['id']}/markdown").text
    assert md.startswith("# The Lighthouse") and "You chose:** Climb the stairs" in md


# ---------------------------------------------------------------- code-to-app

def test_mini_app_build_sandbox_store_and_undo(client, monkeypatch):
    from lifeos import llm

    _fake_models(monkeypatch)
    v = {"n": 0}

    def fake_text(msgs, model=None, timeout=180):
        v["n"] += 1
        return (f"Here you go:\n```html\n<!doctype html><html><head><title>Timer</title>"
                f"<script src=\"https://cdn.example.com/x.js\"></script></head><body><h1>Timer v{v['n']}</h1>"
                "<script>lifeosStore.ready.then(()=>{})</script></body></html>\n```")

    monkeypatch.setattr(llm, "chat_text", fake_text)
    monkeypatch.setattr(llm, "chat_json", lambda msgs, schema, model=None, timeout=180: {"name": "Pomodoro", "icon": "timer"})
    a = client.post("/api/mini-apps", json={"description": "A pomodoro timer with a task list"}).json()
    a = _wait(lambda: client.get(f"/api/mini-apps/{a['id']}").json(), lambda x: not x["busy"] and x["status"] != "building")
    assert a["status"] == "ready" and a["name"] == "Pomodoro" and a["icon"] == "timer" and a["version"] == 1
    page = client.get(f"/miniapp/{a['id']}")  # no LifeOS header needed: it's framed by the app
    csp = page.headers["content-security-policy"]
    assert page.status_code == 200 and csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp and "cdn.example.com" not in page.text and "lifeosStore" in page.text
    assert client.post(f"/api/mini-apps/{a['id']}/store", json={"key": "tasks", "value": ["a"]}).json() == {"tasks": ["a"]}
    assert client.get(f"/api/mini-apps/{a['id']}/store").json() == {"tasks": ["a"]}
    client.post(f"/api/mini-apps/{a['id']}/revise", json={"change": "make it blue"})
    a = _wait(lambda: client.get(f"/api/mini-apps/{a['id']}").json(), lambda x: not x["busy"] and x["status"] != "building")
    assert a["version"] == 2 and "Timer v2" in client.get(f"/miniapp/{a['id']}").text
    client.post(f"/api/mini-apps/{a['id']}/undo")
    assert "Timer v1" in client.get(f"/miniapp/{a['id']}").text
    assert client.post(f"/api/mini-apps/{a['id']}/undo").status_code == 400


# ---------------------------------------------------------------- USB backup

def test_usb_backup_asks_then_backs_up(client, monkeypatch, tmp_path):
    from lifeos import usbbackup

    stick = tmp_path / "stick"
    stick.mkdir()
    plugged: list[dict] = []
    monkeypatch.setattr(usbbackup, "drives", lambda: [dict(d) for d in plugged])
    usbbackup._present.clear()
    assert usbbackup.tick() == []
    plugged.append({"id": "ABCD1234", "mount": str(stick), "label": "KINGSTON", "fs": "exFAT", "free": 10**10, "total": 10**11})
    assert usbbackup.tick() == []  # new drive: ask, don't write
    st = client.get("/api/usb-backup").json()
    assert st["drives"][0]["id"] == "ABCD1234" and st["drives"][0]["allowed"] is False
    assert any("KINGSTON" in n["title"] for n in client.get("/api/notifications").json())
    assert client.post("/api/usb-backup/ABCD1234/run").status_code == 400
    client.patch("/api/usb-backup", json={"enabled": True})
    client.post("/api/usb-backup/ABCD1234/allow", json={"allowed": True})
    started = usbbackup.tick()
    assert started == ["ABCD1234"]
    for _ in range(200):
        if list((stick / usbbackup.FOLDER).glob("*.zip")) and not usbbackup._state["running"]:
            break
        time.sleep(0.03)
    zips = list((stick / usbbackup.FOLDER).glob("*.zip"))
    assert len(zips) == 1 and "lifeos.db" in zipfile.ZipFile(zips[0]).namelist()
    plugged.clear()
    usbbackup.tick()
    plugged.append({"id": "ABCD1234", "mount": str(stick), "label": "KINGSTON", "fs": "exFAT", "free": 1, "total": 2})
    assert usbbackup.tick() == []  # backed up less than min_hours ago
    st = client.get("/api/usb-backup").json()
    assert st["drives"][0]["last_backup_at"] and st["drives"][0]["present"]
    assert json.dumps(st)  # serializable
    assert Path(zips[0]).stat().st_size > 0
