"""Mood lighting: the app's colors and ambient music follow the time of day and what's going on.

The backend decides the mood (so the main window, the wallpaper, the pet and the OS session all agree); the app
turns it into CSS colors and a generative ambient track played with the Web Audio API (no audio files, nothing
downloaded). Phases: night, dawn, day, dusk. Activity: idle, busy (agents working), alert (something needs you),
focus (quiet hours), gaming (game mode).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import common, db, gamemode, settings

# Accent, background tint and glow per phase; activity shifts them.
PHASES: dict[str, dict[str, Any]] = {
    "night": {"accent": "#6366f1", "tint": "#070816", "glow": "#312e81", "hours": (21, 5)},
    "dawn": {"accent": "#f472b6", "tint": "#120a14", "glow": "#9d174d", "hours": (5, 8)},
    "day": {"accent": "#22d3ee", "tint": "#0a0f14", "glow": "#155e75", "hours": (8, 17)},
    "dusk": {"accent": "#f59e0b", "tint": "#140d08", "glow": "#9a3412", "hours": (17, 21)},
}
ACTIVITY: dict[str, dict[str, Any]] = {
    "idle": {"tempo": 64, "intensity": 0.35, "scale": "lydian"},
    "busy": {"tempo": 88, "intensity": 0.6, "scale": "dorian", "accent": None},
    "alert": {"tempo": 96, "intensity": 0.7, "scale": "phrygian", "accent": "#f43f5e"},
    "focus": {"tempo": 56, "intensity": 0.25, "scale": "aeolian"},
    "gaming": {"tempo": 0, "intensity": 0.0, "scale": "aeolian", "accent": "#a3e635"},
}
ROOT_BY_PHASE = {"night": 45, "dawn": 50, "day": 52, "dusk": 48}  # MIDI note of the drone


def cfg() -> dict[str, Any]:
    return common.group("mood")


def phase(now: datetime | None = None) -> str:
    h = (now or datetime.now()).hour
    for name, p in PHASES.items():
        lo, hi = p["hours"]
        if (lo <= h < hi) if lo < hi else (h >= lo or h < hi):
            return name
    return "day"


def _in_quiet(now: datetime) -> bool:
    q = settings.get("quiet_hours")
    if not q.get("enabled"):
        return False
    def mins(t: str) -> int:
        hh, _, mm = t.partition(":")
        return int(hh) * 60 + int(mm or 0)
    m, s, e = now.hour * 60 + now.minute, mins(q["start"]), mins(q["end"])
    return s <= m < e if s <= e else (m >= s or m < e)


def activity_now(now: datetime | None = None) -> tuple[str, dict[str, int]]:
    now = now or datetime.now()
    with db.session() as conn:
        working = conn.execute("SELECT COUNT(DISTINCT agent) FROM mission_tasks WHERE status = 'running'").fetchone()[0]
        waiting = conn.execute("SELECT (SELECT COUNT(*) FROM actions WHERE status = 'pending') + "
                               "(SELECT COUNT(*) FROM briefs WHERE status = 'open')").fetchone()[0]
    counts = {"working": int(working or 0), "waiting": int(waiting or 0)}
    if gamemode.active():
        return "gaming", counts
    if counts["waiting"]:
        return "alert", counts
    if counts["working"]:
        return "busy", counts
    if _in_quiet(now):
        return "focus", counts
    return "idle", counts


def current(now: datetime | None = None) -> dict[str, Any]:
    c = cfg()
    now = now or datetime.now()
    ph = phase(now) if c["follow_time"] else "day"
    act, counts = activity_now(now) if c["follow_activity"] else ("idle", {"working": 0, "waiting": 0})
    pal = dict(PHASES[ph])
    pal.pop("hours")
    a = ACTIVITY[act]
    if a.get("accent"):
        pal["accent"] = a["accent"]
    return {"config": c, "phase": ph, "activity": act, "counts": counts, "palette": pal,
            "music": {"on": c["music"] and act != "gaming", "volume": c["volume"], "tempo": a["tempo"],
                      "intensity": a["intensity"], "scale": a["scale"], "root": ROOT_BY_PHASE[ph]},
            "hour": now.hour}
