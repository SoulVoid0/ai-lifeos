"""Installed apps: find them, rank them by use, and launch them by name.

This is the launcher half of LifeOS as a desktop shell. Every platform has its own source of truth:
- Windows: the Start menu (Get-StartApps lists classic programs and Store apps; .lnk shortcuts are the fallback).
- Linux: freedesktop .desktop entries (system, user and Flatpak/Snap exports), the same list a desktop shell shows.
- macOS: .app bundles in /Applications and ~/Applications.

Only entries found this way can be launched, so the assistant can't run an arbitrary command line by naming it.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import activity, db

_CACHE: dict[str, Any] = {"at": 0.0, "apps": []}
_LOCK = threading.Lock()
TTL = 600
_SKIP = re.compile(r"\b(uninstall|uninstaller|readme|read me|release notes|help|documentation|website|web site|"
                   r"license|changelog|support|manual|faq)\b", re.I)
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _hidden() -> dict[str, Any]:
    return {"creationflags": _NO_WINDOW} if sys.platform == "win32" else {}


# ---------------------------------------------------------------- discovery

def _windows_start_apps() -> list[dict[str, Any]]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20, **_hidden()).stdout
        rows = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if isinstance(rows, dict):
        rows = [rows]
    return [{"id": f"win:{r['AppID']}", "name": r["Name"], "source": "start-menu", "target": r["AppID"]}
            for r in rows if r.get("Name") and r.get("AppID")]


def _windows_shortcuts() -> list[dict[str, Any]]:
    roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
             Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft/Windows/Start Menu/Programs"]
    out = []
    for root in roots:
        if root.is_dir():
            for p in root.rglob("*"):
                if p.suffix.lower() in (".lnk", ".url", ".appref-ms"):
                    out.append({"id": f"lnk:{p}", "name": p.stem, "source": "shortcut", "target": str(p)})
    return out


def _desktop_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    dirs = [data_home] + [Path(d) for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    dirs += [Path("/var/lib/flatpak/exports/share"), Path.home() / ".local/share/flatpak/exports/share",
             Path("/var/lib/snapd/desktop")]
    return [d / "applications" for d in dirs]


def parse_desktop(path: Path) -> dict[str, Any] | None:
    """Read the [Desktop Entry] group of a .desktop file; None for hidden entries and non-apps."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields: dict[str, str] = {}
    group = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            group = line
            continue
        if group == "[Desktop Entry]" and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            fields.setdefault(k.strip(), v.strip())
    if fields.get("Type", "Application") != "Application" or not fields.get("Name") or not fields.get("Exec"):
        return None
    if fields.get("NoDisplay", "").lower() == "true" or fields.get("Hidden", "").lower() == "true":
        return None
    return {"id": f"desktop:{path.name}", "name": fields["Name"], "source": "desktop", "target": str(path),
            "exec": fields["Exec"], "icon": fields.get("Icon", ""), "comment": fields.get("Comment", ""),
            "terminal": fields.get("Terminal", "").lower() == "true"}


def _linux_apps() -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for d in _desktop_dirs():
        if d.is_dir():
            for p in sorted(d.glob("*.desktop")):
                if p.name not in seen:  # user entries shadow system ones
                    app = parse_desktop(p)
                    if app:
                        seen[p.name] = app
    return list(seen.values())


def _mac_apps() -> list[dict[str, Any]]:
    out = []
    for root in (Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications"):
        if root.is_dir():
            for p in root.glob("*.app"):
                out.append({"id": f"mac:{p}", "name": p.stem, "source": "applications", "target": str(p)})
    return out


def discover() -> list[dict[str, Any]]:
    if sys.platform == "win32":
        apps = _windows_start_apps() or _windows_shortcuts()
    elif sys.platform == "darwin":
        apps = _mac_apps()
    else:
        apps = _linux_apps()
    unique: dict[str, dict[str, Any]] = {}
    for a in apps:
        if not _SKIP.search(a["name"]):
            unique.setdefault(a["name"].lower(), a)
    return sorted(unique.values(), key=lambda a: a["name"].lower())


def list_apps(refresh: bool = False) -> list[dict[str, Any]]:
    with _LOCK:
        if refresh or not _CACHE["apps"] or time.time() - _CACHE["at"] > TTL:
            _CACHE["apps"] = discover()
            _CACHE["at"] = time.time()
        apps = list(_CACHE["apps"])
    usage = _usage()
    for a in apps:
        u = usage.get(a["id"])
        a["launches"] = u["n"] if u else 0
        a["last_launched_at"] = u["last"] if u else None
    return apps


def _usage() -> dict[str, dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute("SELECT app_id, COUNT(*) AS n, MAX(launched_at) AS last FROM app_launches "
                            "GROUP BY app_id").fetchall()
    return {r["app_id"]: {"n": r["n"], "last": r["last"]} for r in rows}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
    apps = list_apps()
    q = _norm(query)
    if not q:
        return sorted(apps, key=lambda a: (-a["launches"], a["name"].lower()))[:limit]
    scored = []
    for a in apps:
        n = _norm(a["name"])
        words = n.split()
        if n == q:
            s = 100
        elif n.startswith(q):
            s = 80
        elif any(w.startswith(q) for w in words):
            s = 60
        elif q in n:
            s = 40
        elif "".join(w[0] for w in words if w).startswith(q.replace(" ", "")):
            s = 30  # "vsc" -> Visual Studio Code
        else:
            continue
        scored.append((s + min(a["launches"], 20), a))
    return [a for _, a in sorted(scored, key=lambda x: (-x[0], x[1]["name"].lower()))[:limit]]


def find(name: str) -> dict[str, Any] | None:
    hits = search(name, 2)
    if not hits:
        return None
    if len(hits) == 1 or _norm(hits[0]["name"]) == _norm(name) or _norm(hits[0]["name"]).startswith(_norm(name)):
        return hits[0]
    return hits[0] if hits[0]["launches"] > hits[1]["launches"] else None


# ---------------------------------------------------------------- launching

def desktop_command(exec_line: str) -> list[str]:
    """The argv of a .desktop Exec line with its %f/%u/... field codes removed."""
    argv = shlex.split(exec_line)
    return [a.replace("%%", "%") for a in argv if not re.fullmatch(r"%[a-zA-Z]", a)]


def _start(app: dict[str, Any]) -> None:
    if app["source"] == "start-menu":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['target']}"], **_hidden())
    elif sys.platform == "win32":
        os.startfile(app["target"])  # type: ignore[attr-defined]
    elif app["source"] == "applications":
        subprocess.Popen(["open", app["target"]])
    else:
        argv = desktop_command(app["exec"])
        if app.get("terminal"):
            argv = ["x-terminal-emulator", "-e", *argv]
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def launch(app_id: str | None = None, name: str | None = None, by: str = "user") -> dict[str, Any]:
    app = next((a for a in list_apps() if a["id"] == app_id), None) if app_id else (find(name) if name else None)
    if not app:
        close = ", ".join(a["name"] for a in search(name or "", 5)) if name else ""
        raise ValueError(f"No installed app called {name!r}" + (f". Closest: {close}" if close else ""))
    try:
        _start(app)
    except OSError as exc:
        raise ValueError(f"Couldn't start {app['name']}: {exc}") from exc
    with db.session() as conn:
        conn.execute("INSERT INTO app_launches(app_id, name, launched_at, by) VALUES (?,?,?,?)",
                     (app["id"], app["name"], db.now(), by))
    activity.log("app", f"Opened {app['name']}", details={"by": by})
    return {"opened": app["name"], "id": app["id"]}
