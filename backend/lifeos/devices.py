"""Wi-Fi, Bluetooth, displays and screen brightness: the settings a LifeOS desktop has to own.

Same pattern as system.py: one function per action, with a Linux implementation (NetworkManager's nmcli, BlueZ's
bluetoothctl, wlr-randr, brightnessctl: what the LifeOS image ships) and a Windows one where Windows allows it
(netsh, WMI). Anything Windows has no command line for opens the matching Windows Settings page instead.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

from . import activity

WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


class DeviceError(ValueError):
    pass


def _out(argv: list[str], timeout: int = 15, check: bool = True) -> str:
    flags = {"creationflags": 0x08000000} if WIN else {}
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, errors="replace", **flags)
    except FileNotFoundError as exc:
        raise DeviceError(f"{argv[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeviceError(f"{argv[0]} took too long") from exc
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise DeviceError(msg[-1] if msg else f"{argv[0]} failed ({r.returncode})")
    return r.stdout


def _need(cmd: str, what: str) -> None:
    if not shutil.which(cmd):
        raise DeviceError(f"{what} needs {cmd}, which isn't installed on this computer")


def open_settings(page: Literal["wifi", "bluetooth", "display", "sound"]) -> dict[str, Any]:
    """Open the operating system's own settings page (used where there's no command line for an action)."""
    if WIN:
        uri = {"wifi": "ms-settings:network-wifi", "bluetooth": "ms-settings:bluetooth", "display": "ms-settings:display",
               "sound": "ms-settings:sound"}[page]
        subprocess.Popen(["cmd", "/c", "start", "", uri], creationflags=0x08000000)
    elif MAC:
        subprocess.Popen(["open", "x-apple.systempreferences:"])
    else:
        raise DeviceError("Use the LifeOS settings on this computer")
    return {"opened": page}


# ---------------------------------------------------------------- Wi-Fi

def _nmcli_split(line: str) -> list[str]:
    """nmcli -t separates fields with ':' and escapes ':' inside values as '\\:'."""
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in re.split(r"(?<!\\):", line)]


def parse_nmcli_wifi(text: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        parts = _nmcli_split(line)
        if len(parts) < 4 or not parts[1]:
            continue
        active, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        net = {"ssid": ssid, "signal": int(signal) if signal.isdigit() else 0, "secure": security not in ("", "--"),
               "security": security if security not in ("", "--") else "open", "connected": active == "yes"}
        old = seen.get(ssid)
        if not old or net["connected"] or (net["signal"] > old["signal"] and not old["connected"]):
            seen[ssid] = net
    return sorted(seen.values(), key=lambda n: (not n["connected"], -n["signal"]))


def parse_netsh_networks(text: str) -> list[dict[str, Any]]:
    nets: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^SSID \d+\s*:\s*(.*)$", line)
        if m:
            cur = {"ssid": m.group(1), "signal": 0, "secure": True, "security": "", "connected": False}
            if cur["ssid"]:
                nets.append(cur)
            continue
        if cur is None or ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        k = key.lower()
        if k == "authentication":
            cur["security"] = val
            cur["secure"] = val.lower() != "open"
        elif k == "signal" and val.rstrip("%").isdigit():
            cur["signal"] = max(cur["signal"], int(val.rstrip("%")))
    return nets


def _win_connected_ssid() -> str:
    text = _out(["netsh", "wlan", "show", "interfaces"], check=False)
    for line in text.splitlines():
        m = re.match(r"^\s*SSID\s*:\s*(.+)$", line)
        if m:
            return m.group(1).strip()
    return ""


def wifi_status() -> dict[str, Any]:
    if WIN:
        text = _out(["netsh", "wlan", "show", "interfaces"], check=False)
        if "There is no wireless interface" in text or not text.strip():
            return {"available": False, "enabled": False, "connected": ""}
        return {"available": True, "enabled": "radio status" not in text.lower() or "off" not in text.lower(),
                "connected": _win_connected_ssid()}
    if MAC:
        return {"available": False, "enabled": False, "connected": ""}
    if not shutil.which("nmcli"):
        return {"available": False, "enabled": False, "connected": ""}
    enabled = _out(["nmcli", "radio", "wifi"], check=False).strip() == "enabled"
    connected = ""
    for line in _out(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], check=False).splitlines():
        parts = _nmcli_split(line)
        if parts[0] == "yes" and len(parts) > 1:
            connected = parts[1]
    return {"available": True, "enabled": enabled, "connected": connected}


def wifi_networks(rescan: bool = False) -> list[dict[str, Any]]:
    if WIN:
        text = _out(["netsh", "wlan", "show", "networks", "mode=bssid"], check=False)
        nets = parse_netsh_networks(text)
        now = _win_connected_ssid()
        for n in nets:
            n["connected"] = n["ssid"] == now
        return sorted(nets, key=lambda n: (not n["connected"], -n["signal"]))
    _need("nmcli", "Wi-Fi")
    text = _out(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                 "--rescan", "yes" if rescan else "auto"], timeout=30)
    return parse_nmcli_wifi(text)


def _win_profile_xml(ssid: str, password: str) -> str:
    s = escape(ssid)
    if not password:
        security = "<authEncryption><authentication>open</authentication><encryption>none</encryption>" \
                   "<useOneX>false</useOneX></authEncryption>"
    else:
        security = ("<authEncryption><authentication>WPA2PSK</authentication><encryption>AES</encryption>"
                    "<useOneX>false</useOneX></authEncryption><sharedKey><keyType>passPhrase</keyType>"
                    f"<protected>false</protected><keyMaterial>{escape(password)}</keyMaterial></sharedKey>")
    return ('<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
            f"<name>{s}</name><SSIDConfig><SSID><name>{s}</name></SSID></SSIDConfig><connectionType>ESS</connectionType>"
            f"<connectionMode>auto</connectionMode><MSM><security>{security}</security></MSM></WLANProfile>")


def wifi_connect(ssid: str, password: str = "") -> dict[str, Any]:
    ssid = ssid.strip()
    if not ssid:
        raise DeviceError("Which Wi-Fi network?")
    if WIN:
        if password:
            # A new network needs a saved profile first; the file holds the password only for a moment.
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "lifeos-wifi.xml"
                path.write_text(_win_profile_xml(ssid, password), encoding="utf-8")
                _out(["netsh", "wlan", "add", "profile", f"filename={path}", "user=current"])
        _out(["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"])
    else:
        _need("nmcli", "Wi-Fi")
        argv = ["nmcli", "dev", "wifi", "connect", ssid] + (["password", password] if password else [])
        try:
            _out(argv, timeout=45)
        except DeviceError as exc:
            if not password and "Secrets were required" in str(exc):
                raise DeviceError(f"{ssid} needs a password") from exc
            raise
    activity.log("system", f"Connected to Wi-Fi {ssid}")
    return {"connected": ssid}


def wifi_disconnect() -> dict[str, Any]:
    if WIN:
        _out(["netsh", "wlan", "disconnect"])
    else:
        _need("nmcli", "Wi-Fi")
        dev = ""
        for line in _out(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev"]).splitlines():
            p = _nmcli_split(line)
            if len(p) >= 3 and p[1] == "wifi" and p[2] == "connected":
                dev = p[0]
        if not dev:
            return {"disconnected": False}
        _out(["nmcli", "dev", "disconnect", dev])
    activity.log("system", "Disconnected from Wi-Fi")
    return {"disconnected": True}


def wifi_power(on: bool) -> dict[str, Any]:
    if WIN:
        open_settings("wifi")
        return {"enabled": on, "opened_settings": True}
    _need("nmcli", "Wi-Fi")
    _out(["nmcli", "radio", "wifi", "on" if on else "off"])
    activity.log("system", f"Wi-Fi turned {'on' if on else 'off'}")
    return {"enabled": on}


# ---------------------------------------------------------------- Bluetooth

def parse_bt_devices(text: str) -> list[dict[str, str]]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^Device\s+([0-9A-Fa-f:]{17})(?:\s+(.*))?$", line.strip())
        if m:
            out.append({"address": m.group(1).upper(), "name": (m.group(2) or "").strip() or m.group(1)})
    return out


def _bt_info(addr: str) -> dict[str, Any]:
    text = _out(["bluetoothctl", "info", addr], check=False)
    get = lambda k: bool(re.search(rf"^\s*{k}:\s*yes", text, re.M))  # noqa: E731
    icon = re.search(r"^\s*Icon:\s*(\S+)", text, re.M)
    batt = re.search(r"Battery Percentage:.*\((\d+)\)", text)
    return {"paired": get("Paired"), "connected": get("Connected"), "trusted": get("Trusted"),
            "kind": icon.group(1) if icon else "", "battery": int(batt.group(1)) if batt else None}


def bluetooth_status() -> dict[str, Any]:
    if WIN:
        text = _out(["powershell", "-NoProfile", "-Command",
                     "(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Measure-Object).Count"], check=False)
        return {"available": text.strip() not in ("", "0"), "powered": None, "settings_only": True}
    if MAC or not shutil.which("bluetoothctl"):
        return {"available": False, "powered": False}
    text = _out(["bluetoothctl", "show"], check=False)
    if "No default controller" in text or not text.strip():
        return {"available": False, "powered": False}
    return {"available": True, "powered": bool(re.search(r"^\s*Powered:\s*yes", text, re.M))}


def bluetooth_devices(scan_seconds: int = 0) -> list[dict[str, Any]]:
    if WIN:
        text = _out(["powershell", "-NoProfile", "-Command",
                     "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object FriendlyName "
                     "-notmatch 'Enumerator|Radio|Adapter|Transport|Wireless Bluetooth|Generic|Service' | "
                     "Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json -Compress"], check=False, timeout=20)
        try:
            rows = json.loads(text or "[]")
        except ValueError:
            rows = []
        rows = [rows] if isinstance(rows, dict) else rows
        return [{"address": r.get("InstanceId", ""), "name": r.get("FriendlyName", ""), "paired": True,
                 "connected": r.get("Status") == "OK", "kind": "", "battery": None} for r in rows]
    _need("bluetoothctl", "Bluetooth")
    if scan_seconds:
        _out(["bluetoothctl", "--timeout", str(max(3, min(20, scan_seconds))), "scan", "on"], timeout=30, check=False)
    found = parse_bt_devices(_out(["bluetoothctl", "devices"], check=False))
    rows = [{**d, **_bt_info(d["address"])} for d in found[:40]]
    return sorted(rows, key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))


def _bt_addr(device: str) -> str:
    device = device.strip()
    if _MAC_RE.match(device):
        return device.upper()
    low = device.lower()
    for d in parse_bt_devices(_out(["bluetoothctl", "devices"], check=False)):
        if d["name"].lower() == low or low in d["name"].lower():
            return d["address"]
    raise DeviceError(f"No Bluetooth device called {device!r}. Scan first, and put it in pairing mode.")


def bluetooth_action(action: Literal["connect", "disconnect", "pair", "forget", "power_on", "power_off"],
                     device: str = "") -> dict[str, Any]:
    if WIN:
        open_settings("bluetooth")
        return {"action": action, "opened_settings": True,
                "summary": "Windows only lets Bluetooth devices be paired in Settings, so I opened it."}
    _need("bluetoothctl", "Bluetooth")
    if action in ("power_on", "power_off"):
        _out(["bluetoothctl", "power", "on" if action == "power_on" else "off"])
        activity.log("system", f"Bluetooth turned {'on' if action == 'power_on' else 'off'}")
        return {"action": action, "summary": f"Bluetooth is {'on' if action == 'power_on' else 'off'}."}
    addr = _bt_addr(device)
    if action == "pair":
        _out(["bluetoothctl", "pair", addr], timeout=40)
        _out(["bluetoothctl", "trust", addr], check=False)
        _out(["bluetoothctl", "connect", addr], timeout=30, check=False)
    elif action == "forget":
        _out(["bluetoothctl", "remove", addr])
    else:
        _out(["bluetoothctl", action, addr], timeout=30)
    verb = {"connect": "Connected", "disconnect": "Disconnected", "pair": "Paired", "forget": "Forgot"}[action]
    activity.log("system", f"Bluetooth: {verb.lower()} {device or addr}")
    return {"action": action, "address": addr, "summary": f"{verb} {device or addr}."}


# ---------------------------------------------------------------- displays and brightness

def parse_wlr_randr(text: str) -> list[dict[str, Any]]:
    out = []
    for o in json.loads(text or "[]"):
        cur = next((m for m in o.get("modes", []) if m.get("current")), None)
        modes = sorted({(m["width"], m["height"], round(m.get("refresh", 0))) for m in o.get("modes", [])}, reverse=True)
        out.append({"name": o.get("name", ""), "label": " ".join(x for x in (o.get("make"), o.get("model")) if x) or o.get("name", ""),
                    "enabled": o.get("enabled", True), "scale": o.get("scale", 1.0),
                    "width": cur["width"] if cur else None, "height": cur["height"] if cur else None,
                    "refresh": round(cur["refresh"]) if cur else None,
                    "modes": [f"{w}x{h}@{r}" for w, h, r in modes[:20]]})
    return out


def displays() -> list[dict[str, Any]]:
    if WIN:
        text = _out(["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_VideoController | Select-Object Name,CurrentHorizontalResolution,"
                     "CurrentVerticalResolution,CurrentRefreshRate | ConvertTo-Json -Compress"], check=False, timeout=20)
        try:
            rows = json.loads(text or "[]")
        except ValueError:
            rows = []
        rows = [rows] if isinstance(rows, dict) else rows
        return [{"name": r.get("Name", ""), "label": r.get("Name", ""), "enabled": True, "scale": None,
                 "width": r.get("CurrentHorizontalResolution"), "height": r.get("CurrentVerticalResolution"),
                 "refresh": r.get("CurrentRefreshRate"), "modes": [], "settings_only": True}
                for r in rows if r.get("CurrentHorizontalResolution")]
    if shutil.which("wlr-randr"):
        return parse_wlr_randr(_out(["wlr-randr", "--json"], check=False) or "[]")
    return []


def set_display(name: str, mode: str = "", scale: float | None = None, enabled: bool | None = None) -> dict[str, Any]:
    if WIN:
        open_settings("display")
        return {"opened_settings": True, "summary": "Opened Windows display settings."}
    _need("wlr-randr", "Display settings")
    argv = ["wlr-randr", "--output", name]
    if mode:
        if not re.match(r"^\d{3,5}x\d{3,5}(@\d{2,3}(\.\d+)?)?$", mode):
            raise DeviceError("Mode looks like 1920x1080@60")
        argv += ["--mode", mode if "@" not in mode else mode + ("" if mode.endswith("Hz") else "Hz")]
    if scale is not None:
        if not 0.5 <= scale <= 3:
            raise DeviceError("Scale must be between 0.5 and 3")
        argv += ["--scale", f"{scale:g}"]
    if enabled is not None:
        argv.append("--on" if enabled else "--off")
    _out(argv)
    activity.log("system", f"Display {name}: " + " ".join(argv[3:]))
    return {"name": name, "summary": f"Updated {name}."}


def brightness() -> int | None:
    if WIN:
        text = _out(["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue)"
                     ".CurrentBrightness"], check=False)
        return int(text.split()[0]) if text.strip().split() and text.split()[0].isdigit() else None
    if shutil.which("brightnessctl"):
        cur = _out(["brightnessctl", "g"], check=False).strip()
        mx = _out(["brightnessctl", "m"], check=False).strip()
        if cur.isdigit() and mx.isdigit() and int(mx):
            return round(int(cur) * 100 / int(mx))
    return None


def set_brightness(level: int) -> dict[str, Any]:
    level = max(1, min(100, int(level)))
    if WIN:
        _out(["powershell", "-NoProfile", "-Command",
              f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop)"
              f" | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness={level}}}"])
    else:
        _need("brightnessctl", "Screen brightness")
        _out(["brightnessctl", "s", f"{level}%"])
    activity.log("system", f"Brightness set to {level}%")
    return {"brightness": level}


def overview() -> dict[str, Any]:
    """Everything the Computer page's settings panel shows, each part independent of the others failing."""
    out: dict[str, Any] = {"platform": "windows" if WIN else "macos" if MAC else "linux"}
    for key, fn in (("wifi", wifi_status), ("bluetooth", bluetooth_status), ("displays", displays),
                    ("brightness", brightness)):
        try:
            out[key] = fn()
        except (DeviceError, OSError, ValueError) as exc:
            out[key] = {"error": str(exc)}
    return out
