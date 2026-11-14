"""Auto-backup to USB: plug in a drive you've allowed and LifeOS backs up to it, then tells you it's safe to unplug.

A new drive is never written to on its own: the first time one shows up, the bell asks whether to use it (Settings →
USB backup → Allow). Drives are recognised by their volume serial (Windows) or filesystem UUID (Linux), not by drive
letter, so a different stick that gets E: isn't mistaken for yours. Backups go in "AI-LifeOS backups" on the drive,
keeping the newest few; one backup per plug-in, and not more often than every `min_hours`.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil

from . import activity, backup, common

FOLDER = "AI-LifeOS backups"
_present: dict[str, dict[str, Any]] = {}
_state: dict[str, Any] = {"running": None, "last": None, "error": ""}
_lock = threading.Lock()


def cfg() -> dict[str, Any]:
    return common.group("usb_backup")


def _win_drives() -> list[dict[str, Any]]:
    import ctypes

    out = []
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        if k32.GetDriveTypeW(root) != 2:  # DRIVE_REMOVABLE (USB sticks; most USB hard drives report as fixed, see below)
            continue
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong()
        if not k32.GetVolumeInformationW(root, name, 261, ctypes.byref(serial), None, None, fs, 261):
            continue  # card reader slot with no card
        out.append({"id": f"{serial.value:08X}", "mount": root, "label": name.value or f"USB drive ({root[:2]})",
                    "fs": fs.value})
    # USB hard drives and SSDs: "fixed" but on the USB bus. Asked only when the set of drive letters changes
    # (PowerShell is slow to start).
    if _usb_cache.get("mask") != mask:
        _usb_cache.update(mask=mask, rows=_usb_volumes())
    for d in _usb_cache["rows"]:
        letter = d.get("DriveLetter")
        if not letter or any(x["mount"].startswith(str(letter)) for x in out):
            continue
        root = f"{letter}:\\"
        serial = ctypes.c_ulong()
        k32.GetVolumeInformationW(root, None, 0, ctypes.byref(serial), None, None, None, 0)
        out.append({"id": f"{serial.value:08X}", "mount": root, "label": d.get("FileSystemLabel") or f"USB drive ({letter}:)",
                    "fs": d.get("FileSystem") or ""})
    return out


_usb_cache: dict[str, Any] = {}


def _usb_volumes() -> list[dict[str, Any]]:
    try:
        from . import doctor

        return doctor._ps("Get-Disk | Where-Object BusType -eq 'USB' | Get-Partition | Get-Volume | "
                            "Select-Object DriveLetter,FileSystemLabel,FileSystem,UniqueId", timeout=15) or []
    except Exception:  # noqa: BLE001 - PowerShell storage module missing: sticks still work
        return []


def _linux_drives() -> list[dict[str, Any]]:
    by_uuid = Path("/dev/disk/by-uuid")
    uuids = {}
    if by_uuid.is_dir():
        for link in by_uuid.iterdir():
            try:
                uuids[str(link.resolve())] = link.name
            except OSError:
                continue
    out = []
    for p in psutil.disk_partitions(all=False):
        if not p.mountpoint.startswith(("/run/media/", "/media/", "/mnt/")):
            continue
        dev = os.path.realpath(p.device)
        name = Path(dev).name.rstrip("0123456789")
        removable = Path(f"/sys/block/{name}/removable")
        try:
            usb = "usb" in os.path.realpath(f"/sys/block/{name}")
        except OSError:
            usb = False
        if not usb and not (removable.is_file() and removable.read_text().strip() == "1"):
            continue
        out.append({"id": uuids.get(dev, dev), "mount": p.mountpoint, "label": Path(p.mountpoint).name, "fs": p.fstype})
    return out


def drives() -> list[dict[str, Any]]:
    try:
        found = _win_drives() if sys.platform == "win32" else _linux_drives()
    except Exception:  # noqa: BLE001
        found = []
    for d in found:
        try:
            u = psutil.disk_usage(d["mount"])
            d["free"], d["total"] = u.free, u.total
        except OSError:
            d["free"] = d["total"] = 0
    return found


def _known() -> dict[str, Any]:
    return dict(cfg()["drives"])


def _save_drive(did: str, **fields: Any) -> None:
    known = _known()
    known[did] = {**known.get(did, {}), **fields}
    common.save_group("usb_backup", {"drives": known})


def allow(did: str, allowed: bool = True) -> dict[str, Any]:
    known = _known()
    seen = {d["id"]: d for d in drives()}
    if did not in known and did not in seen:
        raise KeyError(f"Drive {did} not found")
    _save_drive(did, allowed=bool(allowed), label=(seen.get(did) or known.get(did) or {}).get("label", did))
    if allowed and did in seen:
        _present.pop(did, None)  # back up right away on the next tick
    return status()


def forget(did: str) -> dict[str, Any]:
    known = _known()
    known.pop(did, None)
    common.save_group("usb_backup", {"drives": known})
    return status()


def _due(info: dict[str, Any], min_hours: float) -> bool:
    last = info.get("last_backup_at")
    if not last:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(hours=min_hours)
    except ValueError:
        return True


def run(did: str, force: bool = False) -> dict[str, Any]:
    c = cfg()
    d = next((x for x in drives() if x["id"] == did), None)
    if not d:
        raise ValueError("That drive isn't plugged in")
    info = c["drives"].get(did) or {}
    if not info.get("allowed"):
        raise ValueError("Allow this drive first")
    if not _lock.acquire(blocking=False):
        raise ValueError("A USB backup is already running")
    try:
        _state.update(running=did, error="")
        started = time.monotonic()
        out = backup.create(include_media=c["include_media"], target=Path(d["mount"]) / FOLDER, keep=c["keep"])
        secs = round(time.monotonic() - started, 1)
        _save_drive(did, last_backup_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), last_bytes=out["bytes"],
                    label=d["label"])
        _state["last"] = {**out, "drive": d["label"], "seconds": secs}
        common.notify(f"Backed up to {d['label']}", f"{out['bytes'] / 1e6:.0f} MB in {secs:.0f} s. Safe to unplug.", kind="backup")
        activity.log("backup", f"USB backup to {d['label']} ({out['bytes'] / 1e6:.0f} MB)")
        return out
    except Exception as exc:
        _state["error"] = f"{type(exc).__name__}: {exc}"[:300]
        common.notify(f"USB backup to {d['label']} failed", _state["error"], kind="backup")
        raise
    finally:
        _state["running"] = None
        _lock.release()


def tick() -> list[str]:
    """Called every few seconds by the scheduler: react to drives being plugged in."""
    c = cfg()
    now = {d["id"]: d for d in drives()}
    arrived = [did for did in now if did not in _present]
    for did in list(_present):
        if did not in now:
            _present.pop(did)
    started = []
    for did in arrived:
        d = now[did]
        _present[did] = d
        info = c["drives"].get(did)
        if info is None:
            if c["ask_new"]:
                _save_drive(did, allowed=False, label=d["label"], first_seen=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                common.notify(f"USB drive {d['label']} plugged in",
                              "Back up LifeOS to it automatically? Settings → USB backup → Allow.", kind="backup")
            continue
        if c["enabled"] and info.get("allowed") and _due(info, c["min_hours"]):
            threading.Thread(target=lambda i=did: _safe_run(i), daemon=True, name=f"lifeos-usb-{did}").start()
            started.append(did)
    return started


def _safe_run(did: str) -> None:
    try:
        run(did)
    except Exception:  # noqa: BLE001 - reported through the bell and status
        pass


def status() -> dict[str, Any]:
    c = cfg()
    plugged = {d["id"]: d for d in drives()}
    rows = []
    for did, info in c["drives"].items():
        rows.append({"id": did, **info, "present": did in plugged, **({k: plugged[did][k] for k in ("mount", "free", "total")}
                                                                    if did in plugged else {})})
    for did, d in plugged.items():
        if did not in c["drives"]:
            rows.append({"id": did, "label": d["label"], "allowed": False, "present": True, "mount": d["mount"],
                         "free": d["free"], "total": d["total"]})
    return {"config": {k: v for k, v in c.items() if k != "drives"}, "drives": rows, "running": _state["running"],
            "last": _state["last"], "error": _state["error"]}
