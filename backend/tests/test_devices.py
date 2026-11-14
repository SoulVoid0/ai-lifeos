"""Wi-Fi, Bluetooth, displays, the desktop notification server and first-start setup."""
from __future__ import annotations

import json

import pytest


def test_parsers():
    from lifeos import devices

    nets = devices.parse_nmcli_wifi("no:Cafe\\: Free:40:--\nyes:Home:70:WPA2\nno:Home:55:WPA2\nno::30:WPA2\nno:Next door:82:WPA1 WPA2\n")
    assert [n["ssid"] for n in nets] == ["Home", "Next door", "Cafe: Free"]
    assert nets[0]["connected"] and nets[0]["signal"] == 70 and not nets[2]["secure"]

    win = devices.parse_netsh_networks(
        "SSID 1 : Home\n    Network type            : Infrastructure\n    Authentication          : WPA2-Personal\n"
        "    BSSID 1                 : aa:bb\n         Signal             : 81%\nSSID 2 : Guest\n    Authentication : Open\n"
        "         Signal             : 30%\n")
    assert win[0] == {"ssid": "Home", "signal": 81, "secure": True, "security": "WPA2-Personal", "connected": False}
    assert win[1]["secure"] is False

    bt = devices.parse_bt_devices("Device 11:22:33:44:55:66 WH-1000XM4\nDevice aa:bb:cc:dd:ee:ff \nController 00:00 x\n")
    assert bt == [{"address": "11:22:33:44:55:66", "name": "WH-1000XM4"},
                  {"address": "AA:BB:CC:DD:EE:FF", "name": "aa:bb:cc:dd:ee:ff"}]

    out = devices.parse_wlr_randr(json.dumps([{"name": "DP-1", "make": "Dell", "model": "U2720Q", "enabled": True, "scale": 1.5,
        "modes": [{"width": 3840, "height": 2160, "refresh": 59.997, "current": True},
                  {"width": 1920, "height": 1080, "refresh": 60.0}]}]))
    assert out[0]["label"] == "Dell U2720Q" and out[0]["width"] == 3840 and out[0]["refresh"] == 60
    assert out[0]["modes"] == ["3840x2160@60", "1920x1080@60"]
    assert "WPA2PSK" in devices._win_profile_xml("A&B", "p<w") and "A&amp;B" in devices._win_profile_xml("A&B", "")


def test_rules():
    from lifeos import rules

    def first(text):
        steps = rules.plan(text).steps
        return steps[0] if steps else None

    assert first("connect to wifi HomeNet password hunter22") == \
        {"tool": "wifi", "args": {"action": "connect", "ssid": "HomeNet", "password": "hunter22"}}
    assert first("show wifi networks")["args"] == {"action": "list"}
    assert first("turn off wifi")["args"] == {"action": "off"}
    assert first("am i connected to wifi")["args"] == {"action": "status"}
    assert first("connect my headphones via bluetooth") == \
        {"tool": "bluetooth", "args": {"action": "connect", "device": "headphones"}}
    assert first("turn on bluetooth")["args"] == {"action": "on"}
    assert first("set brightness to 40%") == {"tool": "set_brightness", "args": {"level": 40}}


def test_wifi_tool_uses_nmcli(monkeypatch):
    from lifeos import devices, tools

    calls = []

    def fake(argv, timeout=15, check=True):
        calls.append(argv)
        return "yes:Home:70:WPA2\n" if "list" in argv else ""

    monkeypatch.setattr(devices, "WIN", False)
    monkeypatch.setattr(devices, "_out", fake)
    monkeypatch.setattr(devices.shutil, "which", lambda c: "/usr/bin/" + c)
    out = tools.invoke("wifi", {"action": "list"}, {})
    assert "Home (70%, connected)" in out["result"]["summary"]
    tools.invoke("wifi", {"action": "connect", "ssid": "Cafe", "password": "pw"}, {})
    assert calls[-1] == ["nmcli", "dev", "wifi", "connect", "Cafe", "password", "pw"]


def test_app_notifications_land_in_the_bell(client):
    from lifeos import notify_bus, settings

    assert notify_bus.store("Firefox", "Download finished", "report.pdf")
    assert notify_bus.store("volume", "50%", "") is None
    rows = client.get("/api/notifications").json()
    assert rows[0]["title"] == "Firefox: Download finished" and rows[0]["kind"] == "app"
    settings.update({"notification_prefs": {**settings.get("notification_prefs"), "apps": False}})
    assert notify_bus.store("Steam", "Friend online", "") is None


def test_notify_bus_interface_builds():
    pytest.importorskip("dbus_next")
    from lifeos import notify_bus

    iface = notify_bus._build_interface()
    assert iface.name == "org.freedesktop.Notifications"


def test_setup_flow(client):
    s = client.get("/api/setup").json()
    assert not s["done"] and s["needed"] is False  # the test install already has an approved folder: not new
    client.post("/api/setup/done")
    after = client.get("/api/setup").json()
    assert after["done"] is True and after["needed"] is False
    assert "wifi" in client.get("/api/devices").json()
