"""PC doctor: a checkup of temperatures, disks, memory, crashes and startup clutter, explained in plain words.

Every check is read-only and local. Findings have a severity (ok, info, warn, bad), what was seen, what it means and
what to do about it. With a model available, a crew member writes a short plain-language summary on top. The only
fix LifeOS does itself is clearing old files from your own temp folder, and only when you press the button.

Sources: psutil everywhere; Windows: the event log (Get-WinEvent: blue screens, unexpected shutdowns, app crashes,
GPU driver resets, hardware errors), Get-PhysicalDisk health, ACPI thermal zones; Linux: lm-sensors through psutil,
journalctl errors, coredumpctl crashes and smartctl when it's installed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil

from . import activity, common, crew, llm

_last: dict[str, Any] = {"report": None, "busy": False}
_lock = threading.Lock()


def _f(sev: str, area: str, title: str, detail: str, fix: str = "", data: Any = None) -> dict[str, Any]:
    return {"severity": sev, "area": area, "title": title, "detail": detail, "fix": fix, "data": data}


def _ps(script: str, timeout: int = 40) -> Any:
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                              script + " | ConvertTo-Json -Depth 4 -Compress"],
                             capture_output=True, text=True, timeout=timeout, **common.hidden())
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else [data]


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


# ---------------------------------------------------------------- checks

def check_disks() -> list[dict[str, Any]]:
    out = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen or "cdrom" in part.opts or part.fstype in ("squashfs", "tmpfs", "overlay"):
            continue
        seen.add(part.mountpoint)
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        if u.total < 2e9:
            continue
        free_pct = 100 - u.percent
        name = part.mountpoint
        if free_pct < 5 or u.free < 5e9:
            out.append(_f("bad", "disk", f"{name} is almost full", f"Only {_gb(u.free)} free of {_gb(u.total)} ({free_pct:.0f}%).",
                          "Windows slows down and updates fail below ~10% free. Empty the Recycle Bin, run Disk Cleanup "
                          "(Start → 'Disk Cleanup' → 'Clean up system files'), or move big folders (games, videos, AI models) "
                          "to another drive. Files → 'Large files' in LifeOS shows what's biggest.", {"mount": name}))
        elif free_pct < 12:
            out.append(_f("warn", "disk", f"{name} is getting full", f"{_gb(u.free)} free of {_gb(u.total)} ({free_pct:.0f}%).",
                          "Clear some space soon: Files → 'Large files' and 'Duplicates' in LifeOS are a good start.", {"mount": name}))
        else:
            out.append(_f("ok", "disk", f"{name} has room", f"{_gb(u.free)} free of {_gb(u.total)}.", data={"mount": name}))
    if sys.platform == "win32":
        for d in _ps("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,Size") or []:
            health = str(d.get("HealthStatus") or "")
            name = d.get("FriendlyName") or "A disk"
            if health and health.lower() not in ("healthy", "0"):
                out.append(_f("bad", "disk", f"{name} reports it is {health.lower()}",
                              f"Windows says the drive's health is '{health}' ({d.get('OperationalStatus')}).",
                              "Back up anything important on it now (Settings → Backup, or a USB drive), then plan to replace "
                              "the drive. Failing drives often get worse quickly."))
            elif health:
                out.append(_f("ok", "disk", f"{name} is healthy", f"{d.get('MediaType') or 'Drive'}, health: {health}."))
    elif shutil.which("smartctl"):
        for dev in sorted(Path("/dev").glob("sd?")) + sorted(Path("/dev").glob("nvme?n1")):
            try:
                r = subprocess.run(["smartctl", "-H", str(dev)], capture_output=True, text=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                continue
            if "PASSED" in r.stdout or "OK" in r.stdout:
                out.append(_f("ok", "disk", f"{dev.name} passes its self-check", "SMART overall health: passed."))
            elif "FAILED" in r.stdout:
                out.append(_f("bad", "disk", f"{dev.name} fails its self-check", "SMART overall health: FAILED.",
                              "Back up now and replace the drive."))
    return out


def check_memory() -> list[dict[str, Any]]:
    m = psutil.virtual_memory()
    swap = psutil.swap_memory()
    top = sorted(((p.info["memory_info"].rss if p.info.get("memory_info") else 0, p.info.get("name") or "?")
                  for p in psutil.process_iter(["name", "memory_info"])), reverse=True)[:3]
    hogs = ", ".join(f"{n} ({_gb(b)})" for b, n in top if b)
    if m.percent >= 90:
        return [_f("bad", "memory", "Memory is nearly full", f"{m.percent:.0f}% of {_gb(m.total)} in use; swap {swap.percent:.0f}%. Biggest: {hogs}.",
                   "Close programs you aren't using (Computer → Programs in LifeOS can end them). If this happens often, "
                   "use smaller AI models (Settings → Low-resource mode) or add RAM.")]
    if m.percent >= 80:
        return [_f("warn", "memory", "Memory is busy", f"{m.percent:.0f}% of {_gb(m.total)} in use. Biggest: {hogs}.",
                   "Fine for now; close big programs before starting large AI models or games.")]
    return [_f("ok", "memory", "Memory is fine", f"{m.percent:.0f}% of {_gb(m.total)} in use.")]


def check_temps() -> list[dict[str, Any]]:
    readings: list[tuple[str, float, float | None]] = []
    if hasattr(psutil, "sensors_temperatures"):
        try:
            for chip, entries in (psutil.sensors_temperatures() or {}).items():
                for e in entries:
                    if e.current and 5 < e.current < 130:
                        readings.append((f"{chip} {e.label}".strip(), float(e.current), float(e.critical) if e.critical else None))
        except Exception:  # noqa: BLE001 - sensor drivers can misbehave
            pass
    if sys.platform == "win32" and not readings:
        for z in _ps("Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue "
                     "| Select-Object InstanceName,CurrentTemperature", timeout=20) or []:
            try:
                c = float(z["CurrentTemperature"]) / 10 - 273.15
            except (KeyError, TypeError, ValueError):
                continue
            if 5 < c < 130:
                readings.append((str(z.get("InstanceName") or "Thermal zone").split("\\")[-1], c, None))
        for s in _ps("Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction SilentlyContinue "
                     "| Where-Object SensorType -eq 'Temperature' | Select-Object Name,Value,Parent", timeout=20) or []:
            try:
                readings.append((f"{s.get('Name')}", float(s["Value"]), None))
            except (KeyError, TypeError, ValueError):
                continue
    if not readings:
        return [_f("info", "temps", "Temperatures aren't readable", "This PC doesn't share its sensor readings without extra software.",
                   "For CPU and GPU temperatures on Windows, install the free LibreHardwareMonitor and leave it running; "
                   "LifeOS reads it automatically. On AMD GPUs, the Radeon software overlay (Alt+R) shows the GPU temperature.")]
    hottest = max(readings, key=lambda r: r[1])
    detail = ", ".join(f"{n}: {t:.0f}°C" for n, t, _ in sorted(readings, key=lambda r: -r[1])[:6])
    limit = hottest[2] or 95
    if hottest[1] >= min(limit, 95):
        return [_f("bad", "temps", "Something is running very hot", detail,
                   "Check the fans spin and the vents aren't blocked; clean the dust out (compressed air, PC off). If it's the "
                   "GPU during AI work, lower its power limit in the Radeon software or use smaller models.", readings)]
    if hottest[1] >= 85:
        return [_f("warn", "temps", "Running warm", detail,
                   "OK under heavy load (games, AI). If it's this hot when idle, clean the dust and check the fans.", readings)]
    return [_f("ok", "temps", "Temperatures are fine", detail, data=readings)]


WIN_EVENTS = [  # (log, provider regex, id or None, severity, title, what it means, fix)
    ("System", r"BugCheck", 1001, "bad", "Blue screen crash",
     "Windows crashed with a blue screen (stop error).",
     "Update the graphics and chipset drivers (AMD Software: Adrenalin; your motherboard maker's site), run "
     "Windows Update, and check memory with 'Windows Memory Diagnostic'. If you overclocked anything, undo it."),
    ("System", r"Kernel-Power", 41, "warn", "Unexpected shutdown",
     "The PC turned off or restarted without shutting down properly (power loss, a hard freeze, or holding the power button).",
     "If you didn't cause it: check the power cable and PSU, and watch temperatures. Repeated ones under load often mean "
     "an overheating or underpowered PC."),
    ("System", r"Display|amdkmdag|atikmdag", 4101, "warn", "Graphics driver reset",
     "The GPU stopped responding and Windows restarted its driver (the screen goes black for a moment).",
     "Update AMD Software: Adrenalin (or do a clean install with its 'Factory Reset' option). Lower any GPU overclock or "
     "undervolt. Big AI models plus a game at once can also trigger this; game mode in LifeOS helps."),
    ("System", r"WHEA-Logger", None, "bad", "Hardware error",
     "The processor, memory or a PCIe device reported a hardware error.",
     "Undo any overclock/XMP tweaks, update the BIOS, and test memory. Repeated ones point to failing hardware."),
    ("System", r"disk|Ntfs|stornvme|storahci", None, "bad", "Disk errors",
     "Windows had trouble reading or writing a drive.",
     "Back up now. Then run 'chkdsk /scan' in an admin terminal, and check the drive's health (above)."),
    ("Application", r"Application Error|Windows Error Reporting", 1000, "info", "App crashes",
     "Programs closed unexpectedly.",
     "If it's always the same program, update or reinstall it. If many different programs crash, check memory and drivers."),
]


def check_crashes(days: int = 7) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if sys.platform == "win32":
        events = _ps(f"Get-WinEvent -FilterHashtable @{{LogName='System','Application'; Level=1,2,3; "
                     f"StartTime=(Get-Date).AddDays(-{days})}} -MaxEvents 1500 -ErrorAction SilentlyContinue | "
                     "Select-Object Id,ProviderName,LogName,TimeCreated,@{n='Msg';e={($_.Message -split \"`n\")[0]}}",
                     timeout=60) or []
        for log, prov, eid, sev, title, means, fix in WIN_EVENTS:
            hits = [e for e in events if e.get("LogName") == log and re.search(prov, str(e.get("ProviderName") or ""), re.I)
                    and (eid is None or e.get("Id") == eid)]
            if not hits:
                continue
            detail = f"{len(hits)} in the last {days} days. {means}"
            if title == "App crashes":
                apps: dict[str, int] = {}
                for e in hits:
                    m = re.search(r"application name: ([^,]+)", str(e.get("Msg") or ""), re.I)
                    apps[m.group(1).strip() if m else "unknown"] = apps.get(m.group(1).strip() if m else "unknown", 0) + 1
                detail += " Most: " + ", ".join(f"{k} ({v})" for k, v in sorted(apps.items(), key=lambda x: -x[1])[:4])
                if len(hits) < 3:
                    sev = "info"
            out.append(_f(sev, "crashes", title, detail, fix, {"count": len(hits)}))
        if not out:
            out.append(_f("ok", "crashes", "No crashes", f"No blue screens, driver resets or unexpected shutdowns in {days} days."))
        return out
    if shutil.which("coredumpctl"):
        try:
            r = subprocess.run(["coredumpctl", "list", "--no-pager", "--json=short", "--since", f"-{days}d"],
                               capture_output=True, text=True, timeout=20)
            dumps = json.loads(r.stdout or "[]") if r.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            dumps = []
        if dumps:
            apps: dict[str, int] = {}
            for d in dumps:
                name = Path(str(d.get("exe") or "?")).name
                apps[name] = apps.get(name, 0) + 1
            out.append(_f("warn" if len(dumps) > 3 else "info", "crashes", "App crashes",
                          f"{len(dumps)} in {days} days: " + ", ".join(f"{k} ({v})" for k, v in sorted(apps.items(), key=lambda x: -x[1])[:5]),
                          "Update the app (sudo pacman -Syu). If one keeps crashing, run it from a terminal to see why."))
    if shutil.which("journalctl"):
        try:
            r = subprocess.run(["journalctl", "-p", "0..2", "--since", f"-{days}d", "--no-pager", "-o", "cat", "-q"],
                               capture_output=True, text=True, timeout=20)
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            lines = []
        if lines:
            gpu = [ln for ln in lines if re.search(r"amdgpu|ring .* timeout|GPU reset", ln, re.I)]
            if gpu:
                out.append(_f("warn", "crashes", "Graphics driver resets", f"{len(gpu)} GPU errors, e.g. {gpu[-1][:160]}",
                              "Update the system (sudo pacman -Syu) and lower any GPU overclock."))
            out.append(_f("info", "crashes", "Critical system messages", f"{len(lines)} in {days} days; latest: {lines[-1][:200]}",
                          "Usually harmless one-offs. If the same message repeats, ask Forge or Sage about it."))
    if not out:
        out.append(_f("ok", "crashes", "No crashes found", f"Nothing alarming in the last {days} days."))
    return out


def check_startup() -> list[dict[str, Any]]:
    up_h = (time.time() - psutil.boot_time()) / 3600
    out = []
    if up_h > 24 * 14:
        out.append(_f("info", "uptime", "Not restarted in a while", f"Up for {up_h / 24:.0f} days.",
                      "Restart once in a while: it finishes updates and clears leaks that slow things down."))
    if sys.platform == "win32":
        items = _ps("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location", timeout=30) or []
        if len(items) > 12:
            out.append(_f("warn", "startup", f"{len(items)} programs start with Windows",
                          ", ".join(str(i.get("Name")) for i in items[:12]) + "…",
                          "Turn off the ones you don't need right away: Task Manager (Ctrl+Shift+Esc) → Startup apps → Disable."))
        elif items:
            out.append(_f("ok", "startup", f"{len(items)} startup programs", ", ".join(str(i.get("Name")) for i in items)))
    temp = Path(tempfile.gettempdir())
    size = _temp_size(temp, days=3)
    if size > 2e9:
        out.append(_f("warn", "temp", "Lots of old temporary files", f"{_gb(size)} in {temp} older than 3 days.",
                      "Press 'Clear old temp files' below: it only deletes files in your own temp folder older than 3 days."))
    elif size > 3e8:
        out.append(_f("info", "temp", "Some old temporary files", f"{_gb(size)} in {temp} older than 3 days.",
                      "Optional: 'Clear old temp files' below frees it."))
    return out


def check_battery() -> list[dict[str, Any]]:
    try:
        b = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        b = None
    if not b:
        return []
    if b.percent < 15 and not b.power_plugged:
        return [_f("warn", "battery", "Battery is low", f"{b.percent:.0f}%, not plugged in.", "Plug in soon.")]
    return [_f("ok", "battery", "Battery", f"{b.percent:.0f}%{' (charging)' if b.power_plugged else ''}.")]


def _temp_size(folder: Path, days: int) -> int:
    cutoff = time.time() - days * 86400
    total = 0
    for root, _dirs, names in os.walk(folder):
        for n in names:
            try:
                st = os.stat(os.path.join(root, n))
            except OSError:
                continue
            if st.st_mtime < cutoff:
                total += st.st_size
    return total


# ---------------------------------------------------------------- the checkup

CHECKS = [check_disks, check_memory, check_temps, check_crashes, check_startup, check_battery]
SEV_ORDER = {"bad": 0, "warn": 1, "info": 2, "ok": 3}


def _explain(findings: list[dict[str, Any]]) -> str:
    problems = [f for f in findings if f["severity"] in ("bad", "warn")]
    if not problems:
        return "Your PC looks healthy: nothing needs attention right now."
    model = crew.get("quick")["effective_model"] if any(a["slug"] == "quick" for a in crew.list_agents()) else None
    if not model or not llm.model_available(model):
        return "Needs attention: " + "; ".join(f["title"] for f in problems) + "."
    text = "\n".join(f"- [{f['severity']}] {f['title']}: {f['detail']} Fix: {f['fix']}" for f in problems)[:5000]
    prompt = ("You are a friendly PC technician. Explain these checkup findings to a non-technical person in 3-6 short "
              "sentences: what matters most, whether it's urgent, and the first thing to do. No jargon, no Markdown "
              f"headings.\n\n{text}")
    try:
        return llm.chat_text([{"role": "user", "content": prompt}], model=model, timeout=240).strip()
    except Exception:  # noqa: BLE001 - the findings still show
        return "Needs attention: " + "; ".join(f["title"] for f in problems) + "."


def run(explain: bool = True) -> dict[str, Any]:
    if not _lock.acquire(blocking=False):
        raise ValueError("A checkup is already running")
    try:
        _last["busy"] = True
        started = time.monotonic()
        findings: list[dict[str, Any]] = []
        for check in CHECKS:
            try:
                findings += check()
            except Exception as exc:  # noqa: BLE001 - one broken check doesn't stop the rest
                findings.append(_f("info", check.__name__.replace("check_", ""), "Couldn't check", f"{type(exc).__name__}: {exc}"[:200]))
        findings.sort(key=lambda f: SEV_ORDER[f["severity"]])
        worst = findings[0]["severity"] if findings else "ok"
        report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "worst": worst, "findings": findings,
                  "summary": _explain(findings) if explain else "", "seconds": round(time.monotonic() - started, 1),
                  "counts": {k: sum(1 for f in findings if f["severity"] == k) for k in SEV_ORDER}}
        _last["report"] = report
        (common.feature_dir("doctor") / "last.json").write_text(json.dumps(report), encoding="utf-8")
        common.save_group("doctor", {"last_run_at": report["at"]})
        activity.log("system", f"PC checkup: {report['counts']['bad']} problem(s), {report['counts']['warn']} warning(s)")
        return report
    finally:
        _last["busy"] = False
        _lock.release()


def last() -> dict[str, Any] | None:
    if _last["report"] is None:
        p = common.feature_dir("doctor") / "last.json"
        if p.is_file():
            try:
                _last["report"] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    return _last["report"]


def status() -> dict[str, Any]:
    return {"config": common.group("doctor"), "busy": _last["busy"], "report": last()}


def scheduled() -> dict[str, Any] | None:
    c = common.group("doctor")
    if not c["weekly"]:
        return None
    if c["last_run_at"]:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(c["last_run_at"]) < timedelta(days=7):
                return None
        except ValueError:
            pass
    report = run()
    if c["notify"] and report["worst"] in ("bad", "warn"):
        bad = [f["title"] for f in report["findings"] if f["severity"] in ("bad", "warn")]
        common.notify("PC checkup: needs attention", "; ".join(bad)[:400], kind="system")
    return report


def clean_temp(days: int = 3) -> dict[str, Any]:
    """Delete files older than `days` from the user's own temp folder (never system folders). Files in use are skipped."""
    folder = Path(tempfile.gettempdir()).resolve()
    if folder in (Path("/"), Path.home().resolve()) or len(folder.parts) < 3:
        raise ValueError(f"Refusing to clean {folder}")
    cutoff = time.time() - max(1, days) * 86400
    freed = removed = skipped = 0
    for root, dirs, names in os.walk(folder, topdown=False):
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.lstat(p)
                if st.st_mtime >= cutoff:
                    continue
                os.unlink(p)
                freed += st.st_size
                removed += 1
            except OSError:
                skipped += 1
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))  # only empty ones
            except OSError:
                pass
    activity.log("system", f"Cleared {removed} old temp files ({freed / 1e6:.0f} MB)")
    return {"removed": removed, "freed": freed, "skipped": skipped, "folder": str(folder)}

