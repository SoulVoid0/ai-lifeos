"""Workspaces: a named set of files, folders, web pages and apps opened in one go ("Prepare my workspace for studying").

Only the user creates or edits a workspace; the assistant and workflows can only open one by name, so they can't
launch anything the user didn't list. Apps are limited to programs and shortcuts (.exe, .lnk, .app) and app links
such as spotify: or ms-settings:, never scripts. Openings are recorded so LifeOS can notice a routine, such as
the same workspace every weekday morning, and offer to open it automatically.
"""
from __future__ import annotations

import json
import os
import re
import sys
import webbrowser
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import activity, db, files

KINDS = ("file", "folder", "url", "app")
APP_SUFFIXES = {".exe", ".lnk", ".app", ".appref-ms", ".url"}
_BLOCKED_SCHEMES = {"file", "javascript", "data", "vbscript", "about", "http", "https", "ftp"}
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]{1,30}):", re.I)
MAX_ITEMS = 20


def _clean_item(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("Each item needs a kind and a target")
    kind, target = str(raw.get("kind") or ""), str(raw.get("target") or "").strip().strip('"')
    if kind not in KINDS:
        raise ValueError(f"Item kind must be one of {', '.join(KINDS)}")
    if not target:
        raise ValueError("Item target is empty")
    if kind == "url":
        if not re.match(r"^https?://[^\s]+$", target, re.I):
            raise ValueError(f"Web pages must start with http:// or https:// ({target})")
    elif kind == "app":
        m = _SCHEME.match(target)
        if m and len(m.group(1)) > 1:  # an app link like spotify: (one letter is a Windows drive)
            if m.group(1).lower() in _BLOCKED_SCHEMES:
                raise ValueError(f"{target} isn't an app link")
        else:
            p = Path(target).expanduser()
            if p.suffix.lower() not in APP_SUFFIXES:
                raise ValueError(f"Apps must be a program or shortcut ({', '.join(sorted(APP_SUFFIXES))}), not {p.name}")
            if not p.exists():
                raise ValueError(f"Not found: {p}")
            target = str(p)
    else:
        p = Path(target).expanduser()
        if not p.exists():
            raise ValueError(f"Not found: {p}")
        if kind == "folder" and not p.is_dir():
            raise ValueError(f"Not a folder: {p}")
        if kind == "file" and not p.is_file():
            raise ValueError(f"Not a file: {p}")
        target = str(p)
    return {"kind": kind, "target": target, "label": str(raw.get("label") or "")[:80]}


def _out(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["items"] = json.loads(d["items"])
    return d


def list_all() -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT w.*, (SELECT MAX(opened_at) FROM workspace_opens o WHERE o.workspace_id = w.id) AS last_opened_at, "
            "(SELECT COUNT(*) FROM workspace_opens o WHERE o.workspace_id = w.id) AS open_count "
            "FROM workspaces w ORDER BY name").fetchall()
    return [_out(r) for r in rows]


def get(wid: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (wid,)).fetchone()
    if not row:
        raise KeyError(f"workspace {wid}")
    return _out(row)


def find(name: str) -> dict[str, Any] | None:
    """By name, ignoring case and a trailing 'workspace'; falls back to the one whose name appears in the text."""
    want = re.sub(r"\s+workspace$", "", name.strip(), flags=re.I).lower()
    spaces = list_all()
    for w in spaces:
        if w["name"].lower() == want:
            return w
    hits = [w for w in spaces if w["name"].lower() in want or want in w["name"].lower()]
    return hits[0] if len(hits) == 1 else None


def save(data: dict[str, Any], wid: int | None = None) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("Give the workspace a name")
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValueError("Add at least one thing to open")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"A workspace can open at most {MAX_ITEMS} things")
    clean = [_clean_item(i) for i in items]
    with db.session() as conn:
        if wid is None:
            cur = conn.execute("INSERT INTO workspaces(name, items, created_at, updated_at) VALUES (?,?,?,?)",
                               (name, json.dumps(clean), db.now(), db.now()))
            wid = int(cur.lastrowid)
        else:
            conn.execute("UPDATE workspaces SET name = ?, items = ?, updated_at = ? WHERE id = ?",
                         (name, json.dumps(clean), db.now(), wid))
    activity.log("workspace", f"Saved workspace \"{name}\"", details={"items": len(clean)})
    return get(wid)


def delete(wid: int) -> dict[str, Any]:
    w = get(wid)
    with db.session() as conn:
        conn.execute("DELETE FROM workspaces WHERE id = ?", (wid,))
    activity.log("workspace", f"Deleted workspace \"{w['name']}\"")
    return w


def _launch(item: dict[str, str]) -> None:
    if item["kind"] == "url":
        webbrowser.open(item["target"], new=2)
    elif item["kind"] == "app" and _SCHEME.match(item["target"]) and sys.platform != "win32":
        import subprocess

        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", item["target"]])
    elif sys.platform == "win32":
        os.startfile(item["target"])  # type: ignore[attr-defined]
    else:
        files.launch(Path(item["target"]))


def open_workspace(wid: int, by: str = "user") -> dict[str, Any]:
    w = get(wid)
    opened, failed = [], []
    for item in w["items"]:
        try:
            _launch(item)
            opened.append(item["label"] or Path(item["target"]).name or item["target"])
        except OSError as exc:
            failed.append({"target": item["target"], "error": str(exc)})
    with db.session() as conn:
        conn.execute("INSERT INTO workspace_opens(workspace_id, opened_at, by) VALUES (?,?,?)", (wid, db.now(), by))
    activity.log("workspace", f"Opened workspace \"{w['name']}\" ({len(opened)} item(s))",
                 details={"by": by, "failed": failed}, status="error" if failed and not opened else "ok")
    return {"workspace": w["name"], "opened": opened, "failed": failed}


# ---------------------------------------------------------------- routines

def routine(wid: int, days: int = 21) -> dict[str, Any] | None:
    """If the user opens this workspace on 3+ different mornings, the usual time and weekdays."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.session() as conn:
        rows = conn.execute("SELECT opened_at FROM workspace_opens WHERE workspace_id = ? AND by != 'workflow' "
                            "AND opened_at >= ?", (wid, since)).fetchall()
    local = [datetime.fromisoformat(r["opened_at"]).astimezone() for r in rows]
    mornings = {d.date(): d for d in sorted(local) if 5 <= d.hour < 12}
    if len(mornings) < 3:
        return None
    minutes = sorted(d.hour * 60 + d.minute for d in mornings.values())
    usual = minutes[len(minutes) // 2] // 15 * 15
    weekdays = Counter(d.strftime("%a").lower()[:3] for d in mornings.values())
    week = ["mon", "tue", "wed", "thu", "fri"]
    days_list = week if set(weekdays) <= set(week) else sorted(weekdays, key=["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index)
    return {"time": f"{usual // 60:02d}:{usual % 60:02d}", "days": days_list, "mornings": len(mornings)}


def automate(wid: int, time: str, days: list[str]) -> dict[str, Any]:
    from . import workflows

    w = get(wid)
    return workflows.create({"name": f"Open {w['name']}", "description": f"Opens the {w['name']} workspace.",
                             "trigger": {"type": "weekly", "days": days, "time": time},
                             "steps": [{"type": "tool", "tool": "open_workspace", "args": {"name": w["name"]},
                                        "label": f"Open {w['name']}"}]})


def suggestions() -> list[dict[str, Any]]:
    from . import workflows

    automated = {str((s.get("args") or {}).get("name", "")).lower() for f in workflows.list_workflows()
                 for s in f["steps"] if s.get("tool") == "open_workspace"}
    out = []
    for w in list_all():
        if w["name"].lower() in automated:
            continue
        r = routine(w["id"])
        if r:
            when = "weekday" if r["days"] == ["mon", "tue", "wed", "thu", "fri"] else ", ".join(r["days"])
            out.append({"id": f"routine-ws-{w['id']}", "kind": "workflow", "priority": 2,
                        "title": f"Open \"{w['name']}\" automatically?",
                        "detail": f"You opened it on {r['mornings']} mornings lately. LifeOS can open it every "
                                  f"{when} at {r['time']}.",
                        "action": {"label": f"Every {when} at {r['time']}", "method": "POST",
                                   "path": f"/api/workspaces/{w['id']}/automate",
                                   "body": {"time": r["time"], "days": r["days"]}}})
    return out
