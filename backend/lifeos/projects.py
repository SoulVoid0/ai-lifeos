"""Project context tracking (spec 3, Phase 3).

Projects are detected automatically inside approved folders by their marker files (a .git
folder, package.json, pyproject.toml, Cargo.toml, ...) and can also be added by hand. Each
project's context is its recent files, open tasks (matched by project name) and memories, so
the assistant and the crew can offer to continue where the user left off.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import activity, db, files, memory, permissions, tasks

MARKERS: dict[str, str] = {
    ".git": "git", "package.json": "node", "pyproject.toml": "python", "setup.py": "python",
    "requirements.txt": "python", "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java",
    "build.gradle": "java", "CMakeLists.txt": "cpp", "composer.json": "php", "Gemfile": "ruby",
    "pubspec.yaml": "dart", ".project": "eclipse", "project.godot": "godot", "main.tex": "latex",
}
SLN_SUFFIXES = (".sln", ".csproj", ".xcodeproj", ".uproject", ".unity")
MAX_DEPTH = 4
STATUSES = ("active", "paused", "done")


def _kind_of(path: Path) -> str | None:
    try:
        names = set(os.listdir(path))
    except OSError:
        return None
    for marker, kind in MARKERS.items():
        if marker in names:
            if marker == ".git" and len(names & set(MARKERS)) > 1:
                continue  # prefer the language marker; .git alone still counts below
            return kind
    if ".git" in names:
        return "git"
    if any(n.endswith(SLN_SUFFIXES) for n in names):
        return "dotnet" if any(n.endswith((".sln", ".csproj")) for n in names) else "app"
    return None


def detect() -> dict[str, int]:
    """Find project folders inside approved folders. Never descends into a found project."""
    found: dict[str, str] = {}
    for _, root in permissions.approved_roots():
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, _ in os.walk(root, followlinks=False):
            here = Path(dirpath)
            if here != root:
                kind = _kind_of(here)
                if kind:
                    found[str(here)] = kind
                    dirnames[:] = []
                    continue
            if len(here.parts) - base_depth >= MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in files.SKIP_DIRS and not d.startswith(".")]
    added = 0
    with db.session() as conn:
        known = {r["path"] for r in conn.execute("SELECT path FROM projects")}
        for path, kind in found.items():
            if path not in known:
                conn.execute("INSERT INTO projects(name, path, kind, created_at) VALUES (?,?,?,?)",
                             (Path(path).name, path, kind, db.now()))
                added += 1
        # Auto-detected projects whose folder vanished are removed; manual ones are kept.
        gone = [r["id"] for r in conn.execute("SELECT id, path FROM projects WHERE auto = 1") if r["path"] not in found
                and not Path(r["path"]).is_dir()]
        conn.executemany("DELETE FROM projects WHERE id = ?", [(i,) for i in gone])
    refresh_activity()
    if added:
        activity.log("project", f"Detected {added} new project(s)", details={"paths": list(found)[:20]})
    return {"found": len(found), "added": added, "removed": len(gone)}


def refresh_activity() -> None:
    with db.session() as conn:
        for p in conn.execute("SELECT id, path FROM projects").fetchall():
            prefix = p["path"].rstrip("/\\") + os.sep
            row = conn.execute("SELECT MAX(modified_at) AS m FROM files WHERE path LIKE ? ESCAPE '\\'",
                               (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)).fetchone()
            if row["m"]:
                conn.execute("UPDATE projects SET last_activity_at = ? WHERE id = ?", (row["m"], p["id"]))


def _with_counts(p: dict[str, Any]) -> dict[str, Any]:
    open_tasks = [t for t in tasks.list_tasks("open") if t.get("project") and t["project"].lower() == p["name"].lower()]
    p["open_tasks"] = len(open_tasks)
    return p


def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    with db.session() as conn:
        sql = "SELECT * FROM projects" + (" WHERE status = ?" if status else "") + \
              " ORDER BY pinned DESC, COALESCE(last_activity_at, created_at) DESC"
        rows = db.rows_to_dicts(conn.execute(sql, (status,) if status else ()).fetchall())
    return [_with_counts(p) for p in rows]


def get(project_id: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise KeyError(f"Project {project_id} not found")
    return _with_counts(db.row_to_dict(row))  # type: ignore[arg-type]


def find(name: str) -> dict[str, Any] | None:
    key = name.lower().strip()
    items = list_projects()
    for p in items:
        if p["name"].lower() == key:
            return p
    for p in items:
        if key and (key in p["name"].lower() or key in p["path"].lower()):
            return p
    return None


def add(path: str, name: str | None = None, description: str = "") -> dict[str, Any]:
    _, resolved = permissions.resolve_allowed(path)
    if not resolved.is_dir():
        raise ValueError("A project must be a folder")
    with db.session() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE path = ?", (str(resolved),)).fetchone()
        if existing:
            return get(existing["id"])
        cur = conn.execute("INSERT INTO projects(name, path, kind, description, auto, created_at) VALUES (?,?,?,?,0,?)",
                           (name or resolved.name, str(resolved), _kind_of(resolved) or "folder", description, db.now()))
        pid = int(cur.lastrowid)
    refresh_activity()
    activity.log("project", f"Added project {name or resolved.name}")
    return get(pid)


def update(project_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    get(project_id)
    fields = {k: v for k, v in changes.items() if k in ("name", "description", "status", "pinned") and v is not None}
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError("status must be active, paused or done")
    if "pinned" in fields:
        fields["pinned"] = int(bool(fields["pinned"]))
    if fields:
        with db.session() as conn:
            conn.execute(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), project_id))
    return get(project_id)


def delete(project_id: int) -> dict[str, Any]:
    p = get(project_id)
    with db.session() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return p


def context(project_id: int, file_limit: int = 12) -> dict[str, Any]:
    p = get(project_id)
    prefix = p["path"].rstrip("/\\") + os.sep
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db.session() as conn:
        recent = db.rows_to_dicts(conn.execute(
            "SELECT id, path, name, ext, size, modified_at, category FROM files WHERE path LIKE ? ESCAPE '\\' "
            "ORDER BY modified_at DESC LIMIT ?", (like, file_limit)).fetchall())
        n_files = conn.execute("SELECT COUNT(*) AS n FROM files WHERE path LIKE ? ESCAPE '\\'", (like,)).fetchone()["n"]
    open_tasks = [t for t in tasks.list_tasks("open") if t.get("project") and t["project"].lower() == p["name"].lower()]
    mems = [m for m in memory.search(p["name"], 10) if p["name"].lower() in m["content"].lower()]
    readme = None
    for candidate in ("README.md", "readme.md", "README.txt", "README"):
        f = Path(p["path"]) / candidate
        if f.is_file():
            readme = files.extract_text(f, 1500)
            break
    return {"project": p, "recent_files": recent, "file_count": n_files, "open_tasks": open_tasks,
            "memories": mems, "readme": readme}


def resume_candidates(days: int = 14) -> list[dict[str, Any]]:
    """Active projects touched recently, most recent first: 'continue where you left off'."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return [p for p in list_projects("active") if p.get("last_activity_at") and p["last_activity_at"] >= since][:5]
