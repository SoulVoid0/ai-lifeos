"""Auto-tidy: the Archivist keeps your Downloads folder sorted and renames files after their contents.

Every interval (default hourly) it looks at loose files at the top of the folder that have been there for a while
(default 10 minutes, so nothing is moved while it's still downloading), and plans:
  * a rename for files with meaningless names (scan001.pdf, IMG_2041.jpg, Document (3).docx) after their text,
  * a move into a category folder (Documents, Images, Installers, Archives, Invoices, ...).
"Ask first" turns the plan into one approval (in the app, the phone remote, or the email buttons); "Automatic" runs
it right away. Either way it's one undoable action: Undo in the Activity log puts every file back.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import actions, activity, common, files, permissions, renamer, settings

PARTIAL = {"crdownload", "part", "partial", "tmp", "download", "opdownload", "!ut", "aria2", "filepart"}
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}


def cfg() -> dict[str, Any]:
    return common.group("tidy")


def default_folder() -> str:
    for _, root in permissions.approved_roots():
        if root.name.lower() == "downloads":
            return str(root)
    return ""


def configure(changes: dict[str, Any]) -> dict[str, Any]:
    def check(c: dict[str, Any]) -> None:
        if c["mode"] not in ("ask", "auto"):
            raise ValueError("mode must be ask or auto")
        if not 5 <= c["interval_minutes"] <= 24 * 60:
            raise ValueError("interval_minutes must be between 5 and 1440")
        if not 0 <= c["min_age_minutes"] <= 24 * 60:
            raise ValueError("min_age_minutes must be between 0 and 1440")
        if c["folder"].strip():
            permissions.resolve_allowed(c["folder"].strip())  # must be an approved folder

    return common.save_group("tidy", changes, check)


def _folder(c: dict[str, Any]) -> str:
    folder = c["folder"].strip() or default_folder()
    if not folder:
        raise ValueError("Approve your Downloads folder in Settings → Approved folders first, or pick a folder for auto-tidy")
    return folder


def _settled(path: Path, min_age_minutes: int) -> bool:
    name = path.name.lower()
    if name in SKIP_NAMES or name.startswith(".") or name.startswith("~$"):
        return False
    if name.rsplit(".", 1)[-1] in PARTIAL:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    if (path.with_name(path.name + ".crdownload")).exists() or (path.with_name(path.name + ".part")).exists():
        return False
    return time.time() - st.st_mtime >= min_age_minutes * 60


def plan() -> dict[str, Any]:
    """The moves a tidy run would make right now (nothing is changed)."""
    c = cfg()
    folder = _folder(c)
    _, root = permissions.resolve_allowed(folder)
    org = files.plan_organization(str(root), strategy="category")
    renames = {}
    if c["rename"]:
        try:
            renames = {m["src"]: m["dest"] for m in renamer.plan(str(root))["moves"]}
        except Exception:  # noqa: BLE001 - renaming is a bonus; sorting still works
            renames = {}
    moves, taken = [], set()
    for m in org["moves"]:
        src = Path(m["src"])
        if not _settled(src, c["min_age_minutes"]):
            continue
        name = Path(renames[m["src"]]).name if m["src"] in renames else src.name
        dest = files._unique_dest(Path(m["dest"]).parent / name, taken)
        taken.add(dest)
        moves.append({**m, "dest": str(dest), "renamed": name != src.name})
    by_cat: dict[str, int] = {}
    for m in moves:
        by_cat[m["category"]] = by_cat.get(m["category"], 0) + 1
    return {"folder": str(root), "strategy": "category", "moves": moves, "by_category": by_cat,
            "renamed": sum(1 for m in moves if m["renamed"])}


def run(force: bool = False, source: str = "schedule") -> dict[str, Any]:
    """Plan and (depending on the mode) run a tidy. Returns what happened."""
    c = cfg()
    if not force and not c["enabled"]:
        return {"status": "off"}
    p = plan()
    settings_update = {"last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not p["moves"]:
        common.save_group("tidy", settings_update)
        return {"status": "clean", "plan": p}
    cats = ", ".join(f"{k} ({v})" for k, v in sorted(p["by_category"].items()))
    summary = (f"Tidy {Path(p['folder']).name}: sort {len(p['moves'])} file(s) into {cats}"
               + (f", renaming {p['renamed']} after their contents" if p["renamed"] else ""))
    # A newer tidy plan replaces an older one that is still waiting (same folder).
    action = actions.create("organize_files", {"folder": p["folder"], "strategy": "category"}, summary, p)
    common.save_group("tidy", settings_update)
    if c["mode"] == "auto" and not settings.get("automation_paused"):
        action = actions.approve(action["id"], auto=True)
        common.notify("Archivist tidied your Downloads", (action.get("result") or {}).get("summary") or summary,
                      kind="tidy", pref="workflows")
        return {"status": action["status"], "action": action, "plan": p}
    common.notify("Archivist wants to tidy your Downloads", summary + ". Approve it in Activity or from your phone.",
                  kind="tidy", pref="workflows")
    try:
        from . import mailer

        mailer.request_approval(action["id"], "Archivist wants to tidy your Downloads")
    except Exception:  # noqa: BLE001 - email is optional
        pass
    activity.log("tidy", summary, status="pending", action_id=action["id"])
    return {"status": "pending", "action": action, "plan": p}


def due() -> bool:
    c = cfg()
    if not c["enabled"]:
        return False
    last = c["last_run_at"]
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - when >= timedelta(minutes=c["interval_minutes"])


def scheduled() -> object:
    """Scheduler job: tidy when due, unless an earlier plan is still waiting for approval."""
    if not due():
        return None
    for a in actions.list_actions("pending", 50):
        if a["tool"] == "organize_files" and (a.get("preview") or {}).get("folder") == _safe_folder():
            return None
    try:
        return run()
    except (ValueError, permissions.PermissionDenied, FileNotFoundError) as exc:
        activity.log("tidy", f"Auto-tidy skipped: {exc}"[:300], status="error")
        common.save_group("tidy", {"last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return None


def _safe_folder() -> str:
    try:
        return str(permissions.resolve_allowed(_folder(cfg()))[1])
    except Exception:  # noqa: BLE001
        return ""


def status() -> dict[str, Any]:
    c = cfg()
    folder = c["folder"].strip() or default_folder()
    loose = 0
    if folder and Path(folder).is_dir():
        try:
            loose = sum(1 for p in Path(folder).iterdir() if p.is_file())
        except OSError:
            loose = 0
    return {"config": c, "folder": folder, "loose_files": loose, "default_folder": default_folder(),
            "os_downloads": str(Path.home() / "Downloads") if (Path.home() / "Downloads").is_dir() else None}

