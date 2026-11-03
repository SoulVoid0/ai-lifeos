"""Small natural-language date parser for reminders and due dates (local time)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
PARTS_OF_DAY = {"morning": 9, "noon": 12, "afternoon": 15, "evening": 18, "tonight": 20, "night": 20}
DEFAULT_HOUR = 9


def _now() -> datetime:
    return datetime.now().astimezone()


def parse(text: str | None, now: datetime | None = None) -> datetime | None:
    """Parse phrases like 'tomorrow at 5pm', 'next friday', 'in 3 days', '2026-11-02 14:00'.

    Returns a timezone-aware local datetime, or None when nothing date-like is found.
    """
    if not text:
        return None
    now = now or _now()
    s = text.lower().strip()

    try:  # ISO first
        dt = datetime.fromisoformat(s.replace("z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now.tzinfo)
        if len(s) == 10:
            dt = dt.replace(hour=DEFAULT_HOUR)
        return dt
    except ValueError:
        pass

    day: datetime | None = None
    explicit_time = False

    m = re.search(r"\bin (\d+|an?|one|two|three|a couple of) (minute|min|hour|hr|day|week|month)s?\b", s)
    if m:
        n = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "a couple of": 2}.get(m.group(1))
        n = n if n is not None else int(m.group(1))
        unit = m.group(2)
        delta = {"minute": timedelta(minutes=n), "min": timedelta(minutes=n), "hour": timedelta(hours=n),
                 "hr": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n),
                 "month": timedelta(days=30 * n)}[unit]
        result = now + delta
        if unit in ("minute", "min", "hour", "hr"):
            return result.replace(second=0, microsecond=0)
        day = result

    if day is None:
        if re.search(r"\bday after tomorrow\b", s):
            day = now + timedelta(days=2)
        elif re.search(r"\b(tomorrow|tmrw|tmr)\b", s):
            day = now + timedelta(days=1)
        elif re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", s):
            day = now
        elif re.search(r"\bnext week\b", s):
            day = now + timedelta(days=7 - now.weekday())  # next Monday
        elif re.search(r"\b(this|end of( the)?) week(end)?\b", s):
            day = now + timedelta(days=(4 - now.weekday()) % 7)

    if day is None:
        m = re.search(r"\b(next |this |on )?(" + "|".join(WEEKDAYS) + r")\b", s)
        if m:
            target = WEEKDAYS.index(m.group(2))
            ahead = (target - now.weekday()) % 7 or 7
            day = now + timedelta(days=ahead)

    if day is None:
        m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.? (\d{1,2})(st|nd|rd|th)?(,? (\d{4}))?\b", s) or \
            re.search(r"\b(\d{1,2})(st|nd|rd|th)? (of )?(" + "|".join(MONTHS) + r")[a-z]*(,? (\d{4}))?\b", s)
        if m:
            g = m.groups()
            if g[0] in MONTHS:
                month, dom, year = MONTHS.index(g[0]) + 1, int(g[1]), g[4]
            else:
                month, dom, year = MONTHS.index(g[3]) + 1, int(g[0]), g[5]
            try:
                day = now.replace(year=int(year) if year else now.year, month=month, day=dom)
                if not year and day.date() < now.date():
                    day = day.replace(year=now.year + 1)
            except ValueError:
                day = None

    hour, minute = None, 0
    m = re.search(r"\b(?:at |by |@)?(\d{1,2})(?::(\d{2}))? ?(am|pm)\b", s) or re.search(r"\b(?:at |by )(\d{1,2}):(\d{2})\b", s) \
        or re.search(r"\b(\d{1,2}):(\d{2})\b", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3) if m.lastindex and m.lastindex >= 3 else None
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        explicit_time = 0 <= hour < 24 and 0 <= minute < 60
        if not explicit_time:
            hour = None
    if hour is None:
        for word, h in PARTS_OF_DAY.items():
            if re.search(rf"\b{word}\b", s):
                hour, explicit_time = h, True
                break

    if day is None and not explicit_time:
        return None
    if day is None:
        day = now
        candidate = day.replace(hour=hour or DEFAULT_HOUR, minute=minute, second=0, microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)
    return day.replace(hour=hour if hour is not None else DEFAULT_HOUR, minute=minute if hour is not None else 0,
                       second=0, microsecond=0)


def iso(dt: datetime | None) -> str | None:
    """Serialize as UTC so stored timestamps compare correctly as strings."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None
