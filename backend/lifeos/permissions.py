"""Folder authorization. Every file tool resolves paths through here."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import activity, db


class PermissionDenied(Exception):
    pass


# Folders that can never be approved, even if the user tries: approving them would
# hand the assistant the whole system.
def _forbidden_roots() -> list[Path]:
    roots = [Path("/"), Path.home()]
    for p in ("/etc", "/bin", "/sbin", "/usr", "/System", "/Library", "/proc", "/sys", "/dev",
              "C:\\", "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)"):
        roots.append(Path(p))
    return [r.resolve() for r in roots if r.exists()]


def _is_protected_system_path(path: Path) -> bool:
    for root in ("/etc", "/bin", "/sbin", "/usr", "/System", "/proc", "/sys", "/dev", "C:\\Windows"):
        r = Path(root)
        if r.exists() and (path == r.resolve() or r.resolve() in path.parents):
            return True
    return False


def list_folders() -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM files WHERE folder_id = f.id) AS file_count "
            "FROM approved_folders f ORDER BY f.path"
        ).fetchall()
    return db.rows_to_dicts(rows)


def add_folder(path: str, label: str | None = None) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"Not a directory: {resolved}")
    if resolved in _forbidden_roots() or _is_protected_system_path(resolved):
        raise PermissionDenied(f"{resolved} is too broad or a system folder; approve a specific subfolder instead")
    with db.session() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO approved_folders(path, label, created_at) VALUES (?,?,?)",
            (str(resolved), label or resolved.name, db.now()),
        )
        row = conn.execute("SELECT * FROM approved_folders WHERE path = ?", (str(resolved),)).fetchone()
    activity.log("permission", f"Approved folder {resolved}", details={"path": str(resolved)})
    return db.row_to_dict(row)  # type: ignore[return-value]


def remove_folder(folder_id: int) -> None:
    with db.session() as conn:
        row = conn.execute("SELECT path FROM approved_folders WHERE id = ?", (folder_id,)).fetchone()
        if not row:
            raise KeyError(folder_id)
        conn.execute("DELETE FROM files WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM approved_folders WHERE id = ?", (folder_id,))
    activity.log("permission", f"Revoked folder {row['path']}", details={"path": row["path"]})


def approved_roots() -> list[tuple[int, Path]]:
    with db.session() as conn:
        rows = conn.execute("SELECT id, path FROM approved_folders").fetchall()
    return [(r["id"], Path(r["path"])) for r in rows]


def resolve_allowed(path: str | Path, *, must_exist: bool = True) -> tuple[int, Path]:
    """Resolve a path (following symlinks) and confirm it lies inside an approved folder.

    Returns (folder_id, resolved_path). Raises PermissionDenied otherwise.
    """
    raw = Path(path).expanduser()
    roots = approved_roots()
    if not roots:
        raise PermissionDenied("No folders are approved yet. Add one in Settings → Permissions.")
    if not raw.is_absolute():
        # Relative paths are interpreted against the first approved folder whose name matches
        # the first component, e.g. "Downloads/report.pdf".
        for fid, root in roots:
            if raw.parts and raw.parts[0].lower() == root.name.lower():
                raw = root.joinpath(*raw.parts[1:])
                break
        else:
            raw = roots[0][1] / raw
    resolved = raw.resolve()
    if must_exist and not resolved.exists():
        raise FileNotFoundError(str(resolved))
    for fid, root in roots:
        if resolved == root or root in resolved.parents:
            return fid, resolved
    raise PermissionDenied(f"{resolved} is outside the approved folders")


def folder_by_name(name: str) -> tuple[int, Path] | None:
    """Match a spoken folder name like "downloads" or "my Documents" to an approved folder."""
    key = name.lower().strip().removeprefix("my ").strip().rstrip("/")
    roots = approved_roots()
    for fid, root in roots:
        if root.name.lower() == key or str(root).lower() == key:
            return fid, root
    with db.session() as conn:
        rows = conn.execute("SELECT id, path, label FROM approved_folders").fetchall()
    for r in rows:
        if (r["label"] or "").lower() == key:
            return r["id"], Path(r["path"])
    for fid, root in roots:
        if key and key in root.name.lower():
            return fid, root
    return None
