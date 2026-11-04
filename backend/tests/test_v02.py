"""0.2 features: voice, screen awareness, arena, auto-tidy, phone remote, game mode, self-upgrade, memory search."""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _fake_models(monkeypatch, models=("llama3.1:8b", "qwen2.5vl:7b", "qwen3:14b")):
    from lifeos import llm

    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": list(models), "url": "x"})
    monkeypatch.setattr(llm, "model_available", lambda ref=None: True)


# ---------------------------------------------------------------- voice

def test_wake_word_and_routing(client):
    from lifeos import voice

    assert voice.is_wake("hey life os", "hey life os")
    assert voice.is_wake("hey life o s", "hey life os")
    assert voice.is_wake("um hey life", "hey life os")
    assert not voice.is_wake("[unk]", "hey life os") and not voice.is_wake("", "hey life os")
    assert voice.is_wake("computer", "computer") and not voice.is_wake("compute her", "computer")
    assert "[unk]" in voice.wake_grammar("hey life os")

    r = voice.route
    assert r("Forge, how is the build going?") == {"kind": "talk", "agent": "coder", "text": "how is the build going?"}
    assert r("ask Chrono to plan my week")["kind"] == "task" and r("ask Chrono to plan my week")["agent"] == "planner"
    assert r("what's this error")["kind"] == "screen"
    assert r("what was that site I looked at Tuesday about GPUs")["kind"] == "recall"
    assert r("stop everything")["kind"] == "stop"
    assert r("be quiet")["kind"] == "hush"
    assert r("game mode on") == {"kind": "game", "on": True, "text": "game mode on"}
    assert r("game mode off")["on"] is False
    assert r("crew, write a report on my week")["kind"] == "mission"
    assert r("remind me to call mom at 5")["kind"] == "assistant"


def test_voice_command_runs_and_speaks(client, monkeypatch):
    from lifeos import gamemode, voice

    said = []
    monkeypatch.setattr(voice, "say", lambda text, agent=None: said.append((text, agent)) or {"queued": True})
    out = client.post("/api/voice/command", json={"text": "remind me to water the plants tomorrow at 9"}).json()
    assert out["route"]["kind"] == "assistant" and said and said[-1][1] == "overseer"
    out = client.post("/api/voice/command", json={"text": "game mode on"}).json()
    assert gamemode.active() and "Game mode on" in out["reply"]
    client.post("/api/voice/command", json={"text": "game mode off"})
    assert not gamemode.active()
    gamemode.set_manual(None)
    st = client.get("/api/voice").json()
    assert st["history"][-1]["heard"] == "game mode off"
    assert any(v["id"] == "en_US-ryan-high" for v in st["catalog"])


def test_voice_text_and_per_agent_voice(client):
    from lifeos import voice

    t = voice.clean_text("**Done!** See [the report](https://x.y/z) and ```code``` at https://a.b")
    assert "*" not in t and "the report" in t and "https" not in t and "code omitted" in t
    assert len(voice.clean_text("word. " * 400)) <= 700
    assert voice.voice_for("coder") == ("en_US-ryan-high", 1.05)
    assert voice.voice_url("en_GB-alan-medium").endswith("/en/en_GB/alan/medium/en_GB-alan-medium.onnx")
    r = client.patch("/api/crew/agents/coder", json={"tts_voice": "piper:en_GB-cori-high", "tts_rate": 1.3})
    assert r.status_code == 200 and r.json()["tts_voice"] == "piper:en_GB-cori-high"
    assert voice.voice_for("coder") == ("piper:en_GB-cori-high", 1.3)
    assert client.patch("/api/crew/agents/coder", json={"tts_voice": "bad;rm -rf"}).status_code == 400
    with pytest.raises(ValueError):
        voice.install(["voice:not-a-voice"])
    assert client.patch("/api/voice", json={"wake_phrase": "one two three four five"}).status_code == 400
    assert client.patch("/api/voice", json={"wake_phrase": "Hey, Computer!"}).json()["config"]["wake_phrase"] == "hey computer"
    presets = client.get("/api/crew/presets").json()
    assert {"butler", "pirate", "zen"} <= {p["id"] for p in presets}
    assert all(p["tts_voice"].split(":")[1] in voice.CATALOG for p in presets)


def test_voice_uses_piper_then_system(client, monkeypatch, tmp_path):
    from lifeos import voice

    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        out = Path(argv[argv.index("--output_file") + 1])
        out.write_bytes(b"RIFF....WAVE")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    exe = tmp_path / "piper"
    exe.write_text("")
    monkeypatch.setattr(voice, "piper_binary", lambda: exe)
    model = voice.voice_file("en_US-ryan-high")
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"x")
    model.with_suffix(".onnx.json").write_text("{}")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    wav = voice.synthesize("Hot iron, clean code.", "coder")
    assert wav.is_file() and "--length_scale" in calls[-1] and calls[-1][calls[-1].index("--model") + 1] == str(model)
    # Agent whose voice isn't installed: another Piper voice is used rather than none.
    wav = voice.synthesize("Tick tock.", "planner")
    assert wav.is_file() and str(model) in calls[-1]


# ---------------------------------------------------------------- screen awareness

def test_screen_ask_sends_the_screenshot_to_a_local_vision_model(client, monkeypatch):
    from PIL import Image

    from lifeos import llm, screen

    _fake_models(monkeypatch)
    big = Image.new("RGB", (3840, 2160), "white")
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **kw: big)
    seen = {}

    def chat_text(msgs, model=None, timeout=180):
        seen.update(model=model, images=msgs[-1].get("images"), q=msgs[-1]["content"])
        return "That's a Python ImportError: install the missing package."

    monkeypatch.setattr(llm, "chat_text", chat_text)
    shot = client.post("/api/screen/capture").json()
    assert max(shot["width"], shot["height"]) == screen.MAX_SIDE and shot["original"] == [3840, 2160]
    out = client.post("/api/screen/ask", json={"question": "what's this error?"}).json()
    assert "ImportError" in out["answer"] and seen["model"] == "qwen2.5vl:7b" and len(seen["images"]) == 1
    assert client.get("/api/screen/last.png").status_code == 200
    # Cloud models are refused so screenshots stay local.
    assert client.post("/api/screen/ask", json={"question": "x"}).status_code == 200
    with pytest.raises(ValueError):
        screen.ask("x", model="openai:gpt-4o")
    # Agents can look too.
    from lifeos import tools

    r = tools.invoke("look_at_screen", {"question": "what is open?"}, {})
    assert r["status"] == "done" and "ImportError" in r["summary"]
    # The voice route uses it.
    monkeypatch.setattr("lifeos.voice.say", lambda *a, **k: None)
    assert "ImportError" in client.post("/api/voice/command", json={"text": "what's this error", "speak": False}).json()["reply"]


# ---------------------------------------------------------------- arena

def test_arena_round_vote_and_lessons(client, monkeypatch):
    from lifeos import arena, learning, llm

    _fake_models(monkeypatch)
    monkeypatch.setattr(llm, "chat_text", lambda msgs, model=None, timeout=180:
                        f"Answer from {msgs[0]['content'].split(',')[0]} on {model}")
    monkeypatch.setattr(llm, "chat_json", lambda msgs, schema, model=None, timeout=180:
                        {"winner_lesson": "Give a short checklist first, then details.",
                         "loser_lesson": "Lead with a concise checklist instead of long prose."})
    assert client.post("/api/arena", json={"prompt": "x", "a": {"agent": "research"}, "b": {"agent": "research"}}).status_code == 400
    m = client.post("/api/arena", json={"prompt": "How do I back up my photos?", "a": {"agent": "research"},
                                        "b": {"agent": "memory", "model": "qwen3:14b"}}).json()
    assert m["a_name"] is None  # blind
    for _ in range(100):
        m = client.get(f"/api/arena/{m['id']}").json()
        if m["status"] != "running":
            break
        time.sleep(0.05)
    assert m["status"] == "ready" and m["a_result"] and m["b_result"]
    winner_side = "a" if m["a_agent"] == "research" else "b"
    out = client.post(f"/api/arena/{m['id']}/vote", json={"winner": winner_side, "reason": "clearer steps"}).json()
    assert out["status"] == "decided" and out["a_name"] and len(out["new_lessons"]) == 2
    assert any("checklist" in l["text"] for l in learning.list_lessons("memory"))
    board = client.get("/api/arena/leaderboard").json()
    assert board["agents"][0]["key"] == "research" and board["agents"][0]["win"] == 1
    assert client.post(f"/api/arena/{m['id']}/vote", json={"winner": "a"}).status_code == 400
    assert arena.delete(m["id"])["id"] == m["id"]


# ---------------------------------------------------------------- auto-tidy

def test_tidy_sorts_and_renames_downloads_with_undo(client, env, monkeypatch):
    from lifeos import actions, tidy

    monkeypatch.setattr("lifeos.renamer._model_titles", lambda rows: {})
    d = env["downloads"]
    (d / "big.iso.crdownload").write_bytes(b"x" * 10)
    old = time.time() - 3600
    for p in d.iterdir():
        os.utime(p, (old, old))
    fresh = d / "fresh.txt"
    fresh.write_text("just arrived, still downloading maybe")
    st = client.get("/api/tidy").json()
    assert st["default_folder"] == str(d.resolve())
    client.patch("/api/tidy", json={"enabled": True, "mode": "ask"})
    preview = client.get("/api/tidy/preview").json()
    srcs = {Path(m["src"]).name for m in preview["moves"]}
    assert "fresh.txt" not in srcs and "big.iso.crdownload" not in srcs and "invoice_march.pdf" in srcs
    scan = next(m for m in preview["moves"] if Path(m["src"]).name == "scan001.pdf")
    assert scan["renamed"] and "Hello world" in Path(scan["dest"]).name
    out = client.post("/api/tidy/run").json()
    assert out["status"] == "pending"
    action = out["action"]
    done = actions.approve(action["id"])
    assert done["status"] == "executed" and not (d / "scan001.pdf").exists()
    assert fresh.exists() and (d / "big.iso.crdownload").exists()
    actions.undo(action["id"])
    assert (d / "scan001.pdf").exists() and (d / "invoice_march.pdf").exists()
    # Automatic mode runs it right away; nothing due right after.
    client.patch("/api/tidy", json={"mode": "auto", "min_age_minutes": 0})
    out = tidy.run()
    assert out["status"] == "executed"
    assert tidy.due() is False
    assert client.patch("/api/tidy", json={"folder": str(env["tmp"])}).status_code == 403


# ---------------------------------------------------------------- game mode

def test_game_mode_detects_games_and_holds_ai(client, monkeypatch):
    from lifeos import gamemode, llm

    class P:
        def __init__(self, pid, name, exe):
            self.info = {"pid": pid, "name": name, "exe": exe}

    procs = [P(1, "steam.exe", r"C:\Program Files (x86)\Steam\steam.exe"),
             P(2, "explorer.exe", r"C:\Windows\explorer.exe")]
    monkeypatch.setattr(gamemode.psutil, "process_iter", lambda attrs=None: list(procs))
    monkeypatch.setattr(gamemode, "_fullscreen_app", lambda: None)
    unloaded = []
    monkeypatch.setattr(llm, "unload_local_models", lambda: unloaded.append(1) or 1)
    monkeypatch.setattr(gamemode, "_warm_up", lambda: None)
    assert gamemode.detect() == (None, None)
    procs.append(P(3, "eldenring.exe", r"D:\SteamLibrary\steamapps\common\ELDEN RING\Game\eldenring.exe"))
    assert gamemode.detect() == ("eldenring", "library")
    gamemode.tick()
    assert gamemode.active() and gamemode.status()["game"] == "eldenring"
    for _ in range(50):
        if unloaded:
            break
        time.sleep(0.02)
    assert unloaded
    # A model request waits while the game runs.
    released = threading.Event()

    def worker():
        gamemode.wait_until_clear()
        released.set()

    threading.Thread(target=worker, daemon=True).start()
    time.sleep(0.2)
    assert not released.is_set()
    procs.pop()
    gamemode._state["last_seen"] = time.time() - gamemode.GRACE_S - 1
    gamemode.tick()
    assert not gamemode.active() and released.wait(2)
    # Your own list, and an abort while waiting.
    client.patch("/api/gamemode", json={"games": ["MyIndieGame.exe"]})
    procs.append(P(4, "MyIndieGame.exe", r"E:\Stuff\MyIndieGame.exe"))
    assert gamemode.detect() == ("MyIndieGame", "your list")
    gamemode.set_manual(True)
    stop = threading.Event()
    stop.set()
    gamemode.wait_until_clear(stop)  # returns at once
    assert client.post("/api/gamemode", json={"on": None}).json()["manual"] is None
    gamemode._leave()


# ---------------------------------------------------------------- memory search

def _chrome_history(path: Path, visits: list[tuple[str, str, datetime]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    con.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    for i, (url, title, when) in enumerate(visits, 1):
        con.execute("INSERT INTO urls VALUES (?,?,?)", (i, url, title))
        con.execute("INSERT INTO visits(url, visit_time) VALUES (?,?)", (i, int((when - epoch).total_seconds() * 1e6)))
    con.commit()
    con.close()


def test_time_words():
    from lifeos import recall

    wed = datetime(2026, 11, 4, 15, 0).astimezone()  # a Wednesday
    s, e, label = recall.parse_when("that site I looked at Tuesday about GPUs", wed)
    assert s.date().isoformat() == "2026-11-03" and (e - s) == timedelta(days=1) and label.startswith("Tuesday")
    s, _, _ = recall.parse_when("on wednesday", wed)
    assert s.date().isoformat() == "2026-10-28"  # the one before today
    s, _, label = recall.parse_when("yesterday", wed)
    assert s.date().isoformat() == "2026-11-03" and label == "yesterday"
    s, e, _ = recall.parse_when("last week", wed)
    assert s.date().isoformat() == "2026-10-26" and e.date().isoformat() == "2026-11-02"
    s, _, _ = recall.parse_when("3 days ago", wed)
    assert s.date().isoformat() == "2026-11-01"
    s, e, _ = recall.parse_when("in october", wed)
    assert s.date().isoformat() == "2026-10-01" and e.date().isoformat() == "2026-11-01"
    assert recall.parse_when("about gpus", wed)[0] is None
    assert recall.keywords("What was that site I looked at Tuesday about GPUs?") == ["gpus"]


def test_recall_finds_browser_history_by_day(client, monkeypatch, tmp_path):
    from lifeos import conversations, recall

    now = datetime.now().astimezone()
    two_days = now - timedelta(days=2)
    hist = tmp_path / "chrome" / "Default" / "History"
    _chrome_history(hist, [
        ("https://www.tomshardware.com/best-gpus", "Best Graphics Cards for Gaming 2026", two_days),
        ("https://news.example.com/", "Morning news", two_days),
        ("https://www.amd.com/en/products/graphics/radeon-rx-6800-xt", "AMD Radeon RX 6800 XT GPU", now - timedelta(days=20)),
        ("chrome://settings", "Settings", two_days),
        ("https://x.com/login?token=abc", "Log in", two_days),
    ])
    monkeypatch.setattr(recall, "_browser_dbs", lambda: [("Chrome", "chromium", hist)])
    assert client.patch("/api/recall", json={"browser_history": True}).status_code == 200
    out = client.post("/api/recall/sync").json()  # turning it on also syncs once in the background
    assert out["browsers"] == ["Chrome"]
    time.sleep(0.3)
    assert client.get("/api/recall").json()["visits"] == 3
    assert client.post("/api/recall/sync").json()["added"] == 0  # only new visits
    conv = conversations.create("Graphics cards")
    conversations.add_message(conv["id"], "user", "Which graphics card fits a 650W power supply?")

    day = recall.WEEKDAYS[two_days.weekday()]
    res = client.post("/api/recall/search", json={"question": f"what was that site I looked at {day} about graphics cards"}).json()
    top = res["results"][0]
    assert top["source"] == "web" and "tomshardware" in top["url"] and res["window"]["label"].lower().startswith(day)
    assert "Best Graphics Cards" in res["answer"]
    # Nothing that day about the RX 6800 XT: it widens to any time and says so.
    res = recall.search(f"the radeon page from {day}")
    assert res["widened"] == "any time" and "amd.com" in res["results"][0]["url"]
    # "What did I look at yesterday?" without keywords lists that day's pages.
    res = recall.search(f"what did I look at {day}")
    assert {r["title"] for r in res["results"]} >= {"Morning news"}
    # Agents get it as a tool.
    from lifeos import tools

    assert "Best Graphics Cards" in tools.invoke("recall", {"question": f"gpu site {day}"}, {})["summary"]
    assert client.delete("/api/recall/history").json()["deleted"] == 3
    assert client.get("/api/recall").json()["visits"] == 0


# ---------------------------------------------------------------- phone remote

def test_remote_pairing_and_controls(client, monkeypatch):
    from fastapi.testclient import TestClient

    from lifeos import actions, remote

    monkeypatch.setattr(remote, "start", lambda: None)
    st = client.patch("/api/remote", json={"enabled": True}).json()
    assert st["enabled"] and len(st["pin"]) == 6
    assert client.get("/api/remote/qr.svg").status_code in (200, 400)  # 400 without a network interface
    lan = TestClient(remote.app, client=("192.168.1.20", 50000))
    assert lan.get("/r/state").status_code == 401
    assert "Pair this phone" in lan.get("/").text
    for _ in range(remote.MAX_FAILS):
        assert lan.post("/r/pair", json={"code": "000000" if st["pin"] != "000000" else "111111"}).status_code == 403
    locked = lan.post("/r/pair", json={"code": remote.pin()})
    assert locked.status_code == 403 and "Too many" in locked.json()["detail"]
    remote._fails.clear()
    paired = lan.post("/r/pair", json={"code": remote.pin(), "name": "Pixel"}).json()
    token = paired["token"]
    assert paired["device"]["name"] == "Pixel" and "hash" not in paired["device"]
    assert all(token not in str(d) for d in remote.cfg()["devices"])  # stored as a hash only
    auth = {"Authorization": f"Bearer {token}"}
    state = lan.get("/r/state", headers=auth).json()
    assert any(a["slug"] == "overseer" for a in state["agents"])
    # Approve something from the phone.
    a = actions.create("create_folder", {"path": "x"}, "Create folder X", {"path": "/tmp/nowhere-x"})
    r = lan.post(f"/r/actions/{a['id']}/reject", headers=auth).json()
    assert r["status"] == "rejected"
    m = lan.post("/r/missions", headers=auth, json={"goal": "what's on my calendar today?"}).json()
    assert m["id"] and lan.post(f"/r/missions/{m['id']}/cancel", headers=auth).status_code == 200
    # Internet addresses are refused even with a key; a removed phone is logged out.
    assert TestClient(remote.app, client=("8.8.8.8", 1)).get("/r/state", headers=auth).status_code == 403
    client.delete(f"/api/remote/devices/{paired['device']['id']}")
    assert lan.get("/r/state", headers=auth).status_code == 401
    assert client.patch("/api/remote", json={"port": 8765}).status_code == 400
    client.patch("/api/remote", json={"enabled": False})


# ---------------------------------------------------------------- self-upgrade

@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_self_upgrade_proposes_applies_on_a_branch_and_undoes(client, monkeypatch, tmp_path):
    from lifeos import actions, llm, mailer, upgrade

    repo = tmp_path / "ai-lifeos"
    (repo / "backend" / "lifeos").mkdir(parents=True)
    (repo / "frontend" / "src").mkdir(parents=True)
    target = repo / "backend" / "lifeos" / "greet.py"
    target.write_text('def greet(name):\n    return "Hello " + name\n')
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-qm", "init"], check=True)
    client.patch("/api/upgrade", json={"repo_path": str(repo), "run_tests": False})
    assert client.patch("/api/upgrade", json={"repo_path": str(tmp_path)}).status_code == 400

    _fake_models(monkeypatch)
    replies = iter([
        {"title": "Greet handles empty names", "why": "greet('') says 'Hello '", "files": ["backend/lifeos/greet.py"]},
        {"summary": "Fall back to 'there'.", "edits": [{"file": "backend/lifeos/greet.py",
                                                        "find": '    return "Hello " + name\n',
                                                        "replace": '    return "Hello " + (name or "there")\n'}]},
    ])
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: next(replies))
    mailed = []
    monkeypatch.setattr(mailer, "request_approval", lambda aid, heading, details="": mailed.append((aid, details)))
    action = upgrade.propose()
    assert action["status"] == "pending" and action["tool"] == "apply_self_upgrade"
    assert "+    return \"Hello \" + (name or \"there\")" in action["preview"]["diff"]
    assert mailed and mailed[0][0] == action["id"] and "name or" in mailed[0][1]
    done = actions.approve(action["id"])
    assert done["status"] == "executed", done["error"]
    branch = done["result"]["branch"]
    assert "there" in subprocess.run(["git", "-C", str(repo), "show", f"{branch}:backend/lifeos/greet.py"],
                                     capture_output=True, text=True).stdout
    assert "there" not in target.read_text()  # your own checkout is untouched
    actions.undo(action["id"])
    assert branch not in subprocess.run(["git", "-C", str(repo), "branch"], capture_output=True, text=True).stdout

    # Unsafe edits are refused.
    with pytest.raises(ValueError, match="exactly one"):
        upgrade.validate_edits(repo, [{"file": "backend/lifeos/greet.py", "find": "nope", "replace": "x"}])
    with pytest.raises(ValueError, match="Not an allowed"):
        upgrade.validate_edits(repo, [{"file": "../../etc/passwd", "find": "", "replace": "x"}])
    with pytest.raises(ValueError, match="breaks"):
        upgrade.validate_edits(repo, [{"file": "backend/lifeos/greet.py", "find": "def greet(name):", "replace": "def greet(:"}])
    # The assistant can't create one.
    from lifeos import tools

    assert tools.invoke("apply_self_upgrade", {"title": "x"}, {})["status"] == "error"


def test_single_approval_email_has_buttons(client, monkeypatch):
    from lifeos import actions, mailer

    monkeypatch.setattr(mailer, "_inbox_cfg", lambda: {"host": "imap.x", "port": 993, "username": "me@x.com", "password": "p"})
    monkeypatch.setattr(mailer, "_cfg", lambda: {**mailer.settings.get("email_alerts"), "enabled": True, "to": "me@x.com"})
    sent = []
    monkeypatch.setattr(mailer, "_deliver_and_poll", lambda s, t, h: sent.append((s, t, h)))
    a = actions.create("create_folder", {"path": "x"}, "Tidy Downloads: sort 4 files", {"path": "/tmp/x"})
    assert mailer.request_approval(a["id"], "Archivist wants to tidy your Downloads", "diff --git a/x b/x")
    for _ in range(50):
        if sent:
            break
        time.sleep(0.02)
    subject, text, html_body = sent[0]
    assert "Approve?" in subject and mailer.code("A", a["id"], "approve") in html_body and "diff --git" in text
    assert not mailer.request_approval(a["id"], "again")  # one email per approval
