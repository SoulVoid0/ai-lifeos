"""The computer as a shell: installed apps, status, programs, volume and power."""
from __future__ import annotations

import sys
from pathlib import Path

import psutil
import pytest


@pytest.fixture
def fake_apps(monkeypatch):
    from lifeos import apps

    found = [
        {"id": "desktop:spotify.desktop", "name": "Spotify", "source": "desktop", "target": "/x/spotify.desktop", "exec": "spotify %U"},
        {"id": "desktop:code.desktop", "name": "Visual Studio Code", "source": "desktop", "target": "/x/code.desktop",
         "exec": "code --new-window %F"},
        {"id": "desktop:steam.desktop", "name": "Steam", "source": "desktop", "target": "/x/steam.desktop", "exec": "steam"},
    ]
    started = []
    monkeypatch.setattr(apps, "discover", lambda: [dict(a) for a in found])
    monkeypatch.setattr(apps, "_start", lambda app: started.append(app["name"]))
    apps._CACHE.update({"at": 0.0, "apps": []})
    return started


def test_desktop_entries(tmp_path):
    from lifeos import apps

    good = tmp_path / "gimp.desktop"
    good.write_text("[Desktop Entry]\nType=Application\nName=GIMP\nExec=gimp-2.10 %U\nIcon=gimp\n"
                    "[Desktop Action new]\nName=New window\nExec=gimp --new\n")
    hidden = tmp_path / "helper.desktop"
    hidden.write_text("[Desktop Entry]\nType=Application\nName=Helper\nExec=helper\nNoDisplay=true\n")
    app = apps.parse_desktop(good)
    assert app and app["name"] == "GIMP" and app["exec"] == "gimp-2.10 %U"
    assert apps.parse_desktop(hidden) is None
    assert apps.desktop_command('env FOO=1 "my app" --x %f') == ["env", "FOO=1", "my app", "--x"]


def test_search_launch_and_ranking(client, fake_apps):
    from lifeos import apps

    assert [a["name"] for a in apps.search("vsc")] == ["Visual Studio Code"]
    assert apps.search("stu")[0]["name"] == "Visual Studio Code"
    r = client.post("/api/chat", json={"message": "open spotify"}).json()
    assert "Opened Spotify" in r["message"]["content"] and fake_apps == ["Spotify"]
    # Used apps rank first with an empty query.
    assert client.get("/api/apps?q=").json()[0]["name"] == "Spotify"
    out = client.post("/api/apps/launch", json={"id": "desktop:steam.desktop"}).json()
    assert out["opened"] == "Steam"
    assert client.post("/api/apps/launch", json={"id": "nope"}).status_code == 400
    # "open my downloads folder" is not an app launch.
    from lifeos import rules

    assert not any(s["tool"] == "launch_app" for s in rules.plan("open my downloads folder").steps)


def test_status_and_programs(client):
    s = client.get("/api/system/status").json()
    assert s["memory"]["total_gb"] > 0 and 0 <= s["cpu_percent"] <= 100 * s["cpu_count"]
    rows = client.get("/api/system/programs").json()
    assert rows and {"name", "memory_mb", "count", "protected"} <= set(rows[0])
    r = client.post("/api/chat", json={"message": "what's using my RAM?"}).json()
    assert "Heaviest running programs" in r["message"]["content"]
    r = client.post("/api/chat", json={"message": "how's my pc doing"}).json()
    assert "CPU" in r["message"]["content"]


def test_power_and_end_program_need_approval(client, monkeypatch):
    from lifeos import system, tools

    ran = []
    monkeypatch.setattr(system, "_run", lambda argv, timeout=10: ran.append(argv))
    out = tools.invoke("power_action", {"action": "lock"}, {})
    assert out["status"] == "pending" and not ran
    assert "Lock the screen" in str(out["action"])
    client.post(f"/api/actions/{out['action']['id']}/approve", json={})
    assert ran and ran[0] == system._power_argv("lock")

    from lifeos import rules

    assert rules.plan("lock my pc").steps[0] == {"tool": "power_action", "args": {"action": "lock"}}
    assert rules.plan("restart the computer").steps[0]["args"]["action"] == "restart"
    assert rules.plan("set the volume to 30").steps[0] == {"tool": "set_volume", "args": {"level": 30}}
    assert rules.plan("mute").steps[0]["args"] == {"change": "mute"}

    with pytest.raises(ValueError, match="can't be ended"):
        system.end_program(psutil.Process().name())  # this test process's own program is protected (it's LifeOS)
    out = tools.invoke("end_program", {"name": "definitely-not-running-xyz"}, {})
    assert out["status"] == "pending"
