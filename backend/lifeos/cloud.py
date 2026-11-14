"""Cloud storage: OneDrive, Google Drive, Dropbox and iCloud Drive through their desktop sync folders.

Their apps keep a normal folder in sync, so LifeOS works with cloud files the same way as local ones once that
folder is approved: no account sign-in and no extra credentials. Files kept online only (Windows "Files On-Demand"
placeholders) are listed and searchable by name, but never read or hashed by LifeOS on its own, because reading one
makes the sync app download it. Opening one, or reading it when you ask for that file, still works.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from . import permissions

# Windows attributes of cloud placeholders whose data isn't on this PC.
_RECALL_ON_DATA_ACCESS = 0x00400000
_RECALL_ON_OPEN = 0x00040000
_OFFLINE = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)


def is_cloud_only(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & (_RECALL_ON_DATA_ACCESS | _RECALL_ON_OPEN | _OFFLINE))


def _dropbox(home: Path) -> list[tuple[str, Path]]:
    out = []
    infos = [Path(os.environ[v]) / "Dropbox" / "info.json" for v in ("APPDATA", "LOCALAPPDATA") if os.environ.get(v)]
    for info in infos + [home / ".dropbox" / "info.json"]:
        if info.is_file():
            try:
                data = json.loads(info.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for kind, entry in data.items():
                if isinstance(entry, dict) and entry.get("path"):
                    out.append((f"Dropbox ({kind})" if kind != "personal" else "Dropbox", Path(entry["path"])))
    out.append(("Dropbox", home / "Dropbox"))
    return out


def _candidates() -> list[tuple[str, Path]]:
    home = Path.home()
    found: list[tuple[str, Path]] = []
    for var, label in (("OneDriveConsumer", "OneDrive"), ("OneDriveCommercial", "OneDrive (work or school)"),
                       ("OneDrive", "OneDrive")):
        if os.environ.get(var):
            found.append((label, Path(os.environ[var])))
    found.append(("OneDrive", home / "OneDrive"))
    found += _dropbox(home)
    found += [("Google Drive", home / "Google Drive"), ("Google Drive", home / "My Drive"),
              ("iCloud Drive", home / "iCloudDrive")]
    if os.name == "nt":
        # Google Drive for desktop mounts a virtual drive (G: by default) with "My Drive" inside.
        found += [("Google Drive", Path(f"{d}:\\My Drive")) for d in "DEFGHIJKLMNOPQRSTUVWXYZ"]
    if sys.platform == "darwin":
        cloud = home / "Library" / "CloudStorage"
        if cloud.is_dir():
            for p in sorted(cloud.iterdir()):
                label = p.name.split("-")[0].replace("GoogleDrive", "Google Drive").replace("OneDrive", "OneDrive")
                found.append((label, p))
        found.append(("iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"))
    return found


def folders() -> list[dict[str, Any]]:
    """Cloud sync folders on this computer and whether each is already approved."""
    approved = {root.resolve() for _, root in permissions.approved_roots()}
    out, seen = [], set()
    for provider, path in _candidates():
        try:
            if not path.is_dir():
                continue
            real = path.resolve()
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        out.append({"provider": provider, "path": str(real), "approved": real in approved
                    or any(a in real.parents for a in approved)})
    return out
