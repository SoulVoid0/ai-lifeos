"""External integrations (spec Phase 6): calendars (ICS files and subscription URLs, export) and
read-only email over IMAP. Cloud model providers live in llm.py, credentials in vault.py.

Everything fetched from outside is untrusted data: it is stored and shown, never executed, and
when the crew reads it, it is passed to the model inside delimiters like file contents.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from . import activity, db, permissions, settings, tasks, vault

WINDOW_PAST_DAYS = 30
WINDOW_FUTURE_DAYS = 400
MAX_OCCURRENCES = 300
EMAIL_SECRET = "email:password"

# Outlook/Exchange exports often use Windows zone names.
WINDOWS_ZONES = {
    "Pacific Standard Time": "America/Los_Angeles", "Mountain Standard Time": "America/Denver",
    "Central Standard Time": "America/Chicago", "Eastern Standard Time": "America/New_York",
    "GMT Standard Time": "Europe/London", "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris", "Central Europe Standard Time": "Europe/Budapest",
    "E. Europe Standard Time": "Europe/Bucharest", "Russian Standard Time": "Europe/Moscow",
    "India Standard Time": "Asia/Kolkata", "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo", "AUS Eastern Standard Time": "Australia/Sydney",
    "UTC": "UTC", "Coordinated Universal Time": "UTC",
}


# ---------------------------------------------------------------- ICS parsing

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _zone(tzid: str | None) -> Any:
    if not tzid:
        return None
    tzid = tzid.strip('"')
    try:
        return ZoneInfo(WINDOWS_ZONES.get(tzid, tzid))
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _parse_dt(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=datetime.now().astimezone().tzinfo), True
    utc = value.endswith("Z")
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return dt.replace(tzinfo=timezone.utc), False
    tz = _zone(params.get("TZID"))
    return (dt.replace(tzinfo=tz) if tz else dt.astimezone()), False


def _prop(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *raw_params = head.split(";")
    params = {}
    for p in raw_params:
        k, _, v = p.partition("=")
        params[k.upper()] = v
    return name.upper(), params, value


WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _expand(start: datetime, rule: str, exdates: set[str], window: tuple[datetime, datetime]) -> list[datetime]:
    """Expand a simple RRULE (DAILY/WEEKLY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL, weekly BYDAY)."""
    parts = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq = parts.get("FREQ", "").upper()
    interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until = None
    if parts.get("UNTIL"):
        until, _ = _parse_dt(parts["UNTIL"], {})
    byday = [WEEKDAYS.index(d[-2:]) for d in parts.get("BYDAY", "").split(",") if d[-2:] in WEEKDAYS]
    out: list[datetime] = []
    n = 0
    i = 0
    while len(out) < MAX_OCCURRENCES and i < 5000:
        if freq == "DAILY":
            cands = [start + timedelta(days=i * interval)]
        elif freq == "WEEKLY":
            week0 = start - timedelta(days=start.weekday())
            week = week0 + timedelta(weeks=i * interval)
            cands = [week + timedelta(days=d) for d in (sorted(byday) or [start.weekday()])]
            cands = [c.replace(hour=start.hour, minute=start.minute, second=start.second) for c in cands if c >= start]
        elif freq == "MONTHLY":
            m = start.month - 1 + i * interval
            try:
                cands = [start.replace(year=start.year + m // 12, month=m % 12 + 1)]
            except ValueError:
                cands = []
        elif freq == "YEARLY":
            try:
                cands = [start.replace(year=start.year + i * interval)]
            except ValueError:
                cands = []
        else:
            return [start]
        i += 1
        stop = False
        for c in cands:
            if until and c > until or c > window[1]:
                stop = True
                break
            n += 1
            if count is not None and n > count:
                stop = True
                break
            if c.strftime("%Y%m%d") not in exdates and c >= window[0]:
                out.append(c)
        if stop:
            break
    return out


def parse_ics(text: str) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    window = (now - timedelta(days=WINDOW_PAST_DAYS), now + timedelta(days=WINDOW_FUTURE_DAYS))
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in _unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {"exdates": set()}
            continue
        if upper == "END:VEVENT" and current is not None:
            if "start" in current:
                start: datetime = current["start"]
                duration = (current["end"] - start) if current.get("end") else (
                    timedelta(days=1) if current.get("all_day") else timedelta(hours=1))
                starts = _expand(start, current["rrule"], current["exdates"], window) if current.get("rrule") else (
                    [start] if window[0] - duration <= start <= window[1] else [])
                for s in starts:
                    events.append({
                        "uid": current.get("uid") or f"{current.get('title', '')}-{start.isoformat()}",
                        "title": current.get("title") or "(no title)",
                        "start_at": s.astimezone(timezone.utc).isoformat(timespec="seconds"),
                        "end_at": (s + duration).astimezone(timezone.utc).isoformat(timespec="seconds"),
                        "all_day": int(bool(current.get("all_day"))),
                        "location": current.get("location"),
                        "description": (current.get("description") or "")[:2000] or None,
                    })
            current = None
            continue
        if current is None:
            continue
        name, params, value = _prop(line)
        try:
            if name == "SUMMARY":
                current["title"] = _unescape(value)[:300]
            elif name == "UID":
                current["uid"] = value[:300]
            elif name == "LOCATION":
                current["location"] = _unescape(value)[:300]
            elif name == "DESCRIPTION":
                current["description"] = _unescape(value)
            elif name == "DTSTART":
                current["start"], current["all_day"] = _parse_dt(value, params)
            elif name == "DTEND":
                current["end"], _ = _parse_dt(value, params)
            elif name == "RRULE":
                current["rrule"] = value
            elif name == "EXDATE":
                for v in value.split(","):
                    current["exdates"].add(v.strip()[:8])
        except (ValueError, IndexError):
            continue
    return events


# ---------------------------------------------------------------- calendar sources

def list_sources() -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = db.rows_to_dicts(conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM events e WHERE e.source_id = s.id) AS event_count "
            "FROM calendar_sources s ORDER BY id").fetchall())
    return rows


def add_source(location: str, label: str | None = None) -> dict[str, Any]:
    location = location.strip()
    if re.match(r"^(https?|webcal)://", location, re.I):
        kind = "ics_url"
        location = re.sub(r"^webcal://", "https://", location, flags=re.I)
    else:
        kind = "ics_file"
        _, resolved = permissions.resolve_allowed(location)
        if resolved.suffix.lower() != ".ics":
            raise ValueError("Calendar files must be .ics")
        location = str(resolved)
    with db.session() as conn:
        cur = conn.execute("INSERT INTO calendar_sources(kind, location, label, created_at) VALUES (?,?,?,?) "
                           "ON CONFLICT(location) DO UPDATE SET label = COALESCE(excluded.label, label), enabled = 1",
                           (kind, location, label, db.now()))
        sid = cur.lastrowid or conn.execute("SELECT id FROM calendar_sources WHERE location = ?", (location,)).fetchone()["id"]
    activity.log("integration", f"Added calendar {label or location}")
    sync_source(int(sid))
    return next(s for s in list_sources() if s["id"] == sid)


def remove_source(source_id: int) -> None:
    with db.session() as conn:
        if not conn.execute("DELETE FROM calendar_sources WHERE id = ?", (source_id,)).rowcount:
            raise KeyError(f"Calendar {source_id} not found")
    activity.log("integration", f"Removed calendar {source_id}")


def _read_source(src: dict[str, Any]) -> str:
    if src["kind"] == "ics_url":
        r = httpx.get(src["location"], timeout=30, follow_redirects=True)
        r.raise_for_status()
        if len(r.content) > 10 * 1024 * 1024:
            raise ValueError("Calendar feed is larger than 10 MB")
        return r.text
    _, path = permissions.resolve_allowed(src["location"])
    return Path(path).read_text(encoding="utf-8", errors="replace")


def sync_source(source_id: int) -> dict[str, Any]:
    with db.session() as conn:
        src = db.row_to_dict(conn.execute("SELECT * FROM calendar_sources WHERE id = ?", (source_id,)).fetchone())
    if not src:
        raise KeyError(f"Calendar {source_id} not found")
    try:
        events = parse_ics(_read_source(src))
    except Exception as exc:  # network, permission and parse errors are all reported on the source
        with db.session() as conn:
            conn.execute("UPDATE calendar_sources SET last_error = ? WHERE id = ?", (str(exc)[:300], source_id))
        activity.log("integration", f"Calendar sync failed: {exc}"[:300], status="error")
        return {"ok": False, "error": str(exc)}
    with db.session() as conn:
        conn.execute("DELETE FROM events WHERE source_id = ?", (source_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO events(source_id, uid, title, start_at, end_at, all_day, location, description) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(source_id, e["uid"], e["title"], e["start_at"], e["end_at"], e["all_day"], e["location"], e["description"])
             for e in events])
        conn.execute("UPDATE calendar_sources SET last_synced_at = ?, last_error = NULL WHERE id = ?", (db.now(), source_id))
    return {"ok": True, "events": len(events)}


def sync_all() -> dict[str, Any]:
    out = {}
    for s in list_sources():
        if s["enabled"]:
            out[s["location"]] = sync_source(s["id"])
    return out


def add_event(title: str, start: str, end: str | None = None, location: str | None = None,
              description: str | None = None) -> dict[str, Any]:
    from . import timeparse

    s = timeparse.parse(start)
    if s is None:
        raise ValueError(f"Could not understand the date '{start}'")
    e = timeparse.parse(end) if end else s + timedelta(hours=1)
    if e is None or e < s:
        raise ValueError("The event must end after it starts")
    uid = f"lifeos-{datetime.now().timestamp()}"
    with db.session() as conn:
        cur = conn.execute("INSERT INTO events(source_id, uid, title, start_at, end_at, all_day, location, description) "
                           "VALUES (NULL,?,?,?,?,0,?,?)", (uid, title.strip()[:300], timeparse.iso(s), timeparse.iso(e),
                                                          location, description))
        row = conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone()
    activity.log("data", f"Added event \"{title}\"")
    return db.row_to_dict(row)  # type: ignore[return-value]


def delete_event(event_id: int) -> None:
    with db.session() as conn:
        if not conn.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount:
            raise KeyError(f"Event {event_id} not found")


def events(start: str | None = None, end: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    start = start or (now - timedelta(days=1)).isoformat(timespec="seconds")
    end = end or (now + timedelta(days=60)).isoformat(timespec="seconds")
    with db.session() as conn:
        return db.rows_to_dicts(conn.execute(
            "SELECT e.*, s.label AS source_label FROM events e LEFT JOIN calendar_sources s ON s.id = e.source_id "
            "WHERE COALESCE(e.end_at, e.start_at) >= ? AND e.start_at < ? ORDER BY e.start_at LIMIT ?",
            (start, end, limit)).fetchall())


def overlaps(days: int = 14) -> list[dict[str, Any]]:
    """Pairs of timed events that overlap in the next `days` days."""
    now = datetime.now(timezone.utc)
    items = [e for e in events(now.isoformat(timespec="seconds"), (now + timedelta(days=days)).isoformat(timespec="seconds"))
             if not e["all_day"] and e["end_at"]]
    out = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if b["start_at"] >= a["end_at"]:
                break
            out.append({"a": a, "b": b})
    return out


def _ics_dt(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_text(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def export_ics() -> str:
    """Tasks with a due date or reminder, plus events created in LifeOS, as an iCalendar feed."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AI LifeOS//EN", "CALSCALE:GREGORIAN"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for t in tasks.list_tasks("open"):
        when = t["due_at"] or t["remind_at"]
        if not when:
            continue
        end = (datetime.fromisoformat(when) + timedelta(minutes=t.get("estimate_minutes") or 30)).isoformat()
        lines += ["BEGIN:VEVENT", f"UID:lifeos-task-{t['id']}@lifeos", f"DTSTAMP:{stamp}", f"DTSTART:{_ics_dt(when)}",
                  f"DTEND:{_ics_dt(end)}", f"SUMMARY:{_ics_text(('Due: ' if t['due_at'] else '') + t['title'])}"]
        if t.get("notes"):
            lines.append(f"DESCRIPTION:{_ics_text(t['notes'][:1000])}")
        lines.append("END:VEVENT")
    with db.session() as conn:
        own = db.rows_to_dicts(conn.execute("SELECT * FROM events WHERE source_id IS NULL").fetchall())
    for e in own:
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}@lifeos", f"DTSTAMP:{stamp}", f"DTSTART:{_ics_dt(e['start_at'])}",
                  f"DTEND:{_ics_dt(e['end_at'] or e['start_at'])}", f"SUMMARY:{_ics_text(e['title'])}"]
        if e.get("location"):
            lines.append(f"LOCATION:{_ics_text(e['location'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------- email (IMAP, read-only)

def email_status() -> dict[str, Any]:
    cfg = settings.get("email")
    return {"configured": bool(cfg.get("host") and cfg.get("username") and vault.has_secret(EMAIL_SECRET)),
            **{k: cfg.get(k) for k in ("host", "port", "username", "folder")}}


def set_email_password(password: str) -> None:
    if password:
        vault.set_secret(EMAIL_SECRET, password)
    else:
        vault.delete_secret(EMAIL_SECRET)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    out = []
    for part, enc in email.header.decode_header(value):
        out.append(part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part)
    return "".join(out).strip()


def _body_text(msg: email.message.Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    html = ""
    for part in parts:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AssertionError):
            continue
        if ctype == "text/plain" and text.strip():
            return text
        if ctype == "text/html" and not html:
            html = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text))
    return html


def check_email(limit: int = 10, unread_only: bool = True) -> list[dict[str, Any]]:
    """Fetch recent message headers and a short snippet. Uses BODY.PEEK, so nothing is marked read."""
    cfg = settings.get("email")
    password = vault.get_secret(EMAIL_SECRET)
    if not (cfg.get("host") and cfg.get("username") and password):
        raise ValueError("No email account is set up (Settings → Integrations)")
    limit = max(1, min(int(limit), 50))
    conn = imaplib.IMAP4_SSL(cfg["host"], int(cfg.get("port") or 993), timeout=30)
    try:
        conn.login(cfg["username"], password)
        conn.select(f'"{cfg.get("folder") or "INBOX"}"', readonly=True)
        typ, data = conn.search(None, "UNSEEN" if unread_only else "ALL")
        if typ != "OK":
            raise ValueError("Mailbox search failed")
        ids = data[0].split()[-limit:]
        out = []
        for mid in reversed(ids):
            typ, msg_data = conn.fetch(mid, "(BODY.PEEK[]<0.30000>)")
            raw = next((p[1] for p in msg_data if isinstance(p, tuple)), b"")
            msg = email.message_from_bytes(raw)
            body = re.sub(r"\s+", " ", _body_text(msg)).strip()
            try:
                when = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat(timespec="seconds")
            except (TypeError, ValueError):
                when = None
            out.append({"id": mid.decode(), "from": _decode(msg.get("From")), "subject": _decode(msg.get("Subject")),
                        "date": when, "snippet": body[:400]})
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def test_email() -> dict[str, Any]:
    try:
        msgs = check_email(limit=1, unread_only=False)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "latest": msgs[0]["subject"] if msgs else None}
