"""Automation engine (spec 2.6 and Phase 4): reusable workflows.

A workflow is: a trigger (manual, interval, daily, weekly, monthly, startup) → conditions that
must all hold → ordered steps → a summary notification. A step is a tool call, a crew mission,
or a notification; each step can have its own condition, retries with a delay, and an on-error
policy. Steps go through the same permission policy as everything else, so a step that needs
approval is queued for the user and the workflow carries on. When automation is paused,
scheduled runs are skipped.

Workflows can be written in plain language ("Every Friday, organize my downloads, summarize my
completed tasks and prepare a plan for next week"); the draft is shown for review before saving.
"""
from __future__ import annotations

import calendar
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import activity, crew, db, llm, permissions, rules, settings, tasks, tools

TRIGGERS = ("manual", "interval", "daily", "weekly", "monthly", "startup")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = {d: i for i, d in enumerate(DAYS)} | {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
METRICS = {
    "open_tasks": "Open tasks",
    "overdue_tasks": "Overdue tasks",
    "due_within_hours": "Tasks due within N hours",
    "loose_files": "Loose files in a folder",
    "duplicate_mb": "MB used by duplicate files",
    "large_files": "Large unused files",
    "pending_actions": "Actions waiting for approval",
    "weekday": "Day of week (0 = Monday)",
    "hour": "Hour of day (0-23)",
}
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
STEP_TYPES = ("tool", "mission", "notify")
MAX_STEPS = 20


# ---------------------------------------------------------------- validation

def _hhmm(value: Any, default: str = "09:00") -> str:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or default).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Time must be HH:MM, got {value!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def validate_trigger(t: dict[str, Any] | None) -> dict[str, Any]:
    t = dict(t or {"type": "manual"})
    kind = t.get("type", "manual")
    if kind not in TRIGGERS:
        raise ValueError(f"Trigger type must be one of {', '.join(TRIGGERS)}")
    if kind == "interval":
        minutes = int(t.get("minutes") or 60)
        if minutes < 5:
            raise ValueError("Interval must be at least 5 minutes")
        return {"type": kind, "minutes": minutes}
    if kind == "daily":
        return {"type": kind, "time": _hhmm(t.get("time"))}
    if kind == "weekly":
        days = [str(d).lower()[:3] for d in t.get("days") or ["mon"]]
        if not days or any(d not in DAYS for d in days):
            raise ValueError("Weekly trigger needs days like mon, tue, ...")
        return {"type": kind, "days": sorted(set(days), key=DAYS.index), "time": _hhmm(t.get("time"))}
    if kind == "monthly":
        day = int(t.get("day") or 1)
        if not 1 <= day <= 31:
            raise ValueError("Monthly trigger day must be 1-31")
        return {"type": kind, "day": day, "time": _hhmm(t.get("time"))}
    return {"type": kind}


def validate_condition(c: dict[str, Any]) -> dict[str, Any]:
    metric, op = c.get("metric"), c.get("op", ">")
    if metric not in METRICS:
        raise ValueError(f"Unknown condition metric {metric!r}")
    if op not in OPS:
        raise ValueError(f"Unknown operator {op!r}")
    try:
        value = float(c.get("value", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Condition value must be a number") from exc
    out: dict[str, Any] = {"metric": metric, "op": op, "value": value}
    if metric == "due_within_hours":
        out["hours"] = max(1, int(c.get("hours") or 24))
    if metric == "loose_files":
        if not c.get("folder"):
            raise ValueError("loose_files needs a folder")
        out["folder"] = str(c["folder"])
    return out


def validate_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"A workflow can have at most {MAX_STEPS} steps")
    out, ids = [], set()
    for i, s in enumerate(steps):
        kind = s.get("type", "tool")
        if kind not in STEP_TYPES:
            raise ValueError(f"Step {i + 1}: type must be tool, mission or notify")
        sid = re.sub(r"[^A-Za-z0-9_]", "", str(s.get("id") or f"s{i + 1}"))[:16] or f"s{i + 1}"
        if sid in ids:
            sid = f"s{i + 1}"
        ids.add(sid)
        step: dict[str, Any] = {"id": sid, "type": kind, "label": str(s.get("label") or "")[:120],
                                "retries": max(0, min(int(s.get("retries") or 0), 5)),
                                "retry_delay_s": max(1, min(int(s.get("retry_delay_s") or 30), 600)),
                                "on_error": "continue" if s.get("on_error") == "continue" else "stop"}
        if s.get("condition"):
            step["condition"] = validate_condition(s["condition"])
        if kind == "tool":
            tool = s.get("tool")
            if tool not in tools.TOOLS:
                raise ValueError(f"Step {i + 1}: unknown tool {tool!r}")
            args = s.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError(f"Step {i + 1}: args must be an object")
            if not any("{{" in str(v) for v in args.values()):
                try:
                    tools.validate(tool, args)  # templated args are validated at run time
                except tools.ToolError as exc:
                    raise ValueError(f"Step {i + 1}: {exc}") from exc
            step.update(tool=tool, args=args)
        elif kind == "mission":
            goal = str(s.get("goal") or "").strip()
            if not goal:
                raise ValueError(f"Step {i + 1}: a mission step needs a goal")
            agent = s.get("agent") or None
            if agent:
                crew.get(agent)
            step.update(goal=goal[:4000], agent=agent)
        else:
            step.update(title=str(s.get("title") or "LifeOS")[:200], message=str(s.get("message") or "")[:2000])
        out.append(step)
    return out


# ---------------------------------------------------------------- scheduling

def next_run(trigger: dict[str, Any], after: datetime | None = None) -> str | None:
    now = (after or datetime.now()).astimezone()
    kind = trigger["type"]
    if kind in ("manual", "startup"):
        return None
    if kind == "interval":
        nxt = now + timedelta(minutes=trigger["minutes"])
    else:
        hh, mm = map(int, trigger["time"].split(":"))
        base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if kind == "daily":
            nxt = base if base > now else base + timedelta(days=1)
        elif kind == "weekly":
            wanted = [DAYS.index(d) for d in trigger["days"]]
            nxt = None
            for offset in range(0, 8):
                cand = base + timedelta(days=offset)
                if cand.weekday() in wanted and cand > now:
                    nxt = cand
                    break
            assert nxt is not None
        else:  # monthly; days past the month's end run on its last day
            def at(year: int, month: int) -> datetime:
                day = min(trigger["day"], calendar.monthrange(year, month)[1])
                return base.replace(year=year, month=month, day=day)
            nxt = at(now.year, now.month)
            if nxt <= now:
                y, mth = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
                nxt = at(y, mth)
    return nxt.astimezone(timezone.utc).isoformat(timespec="seconds")


def describe_trigger(t: dict[str, Any]) -> str:
    kind = t["type"]
    if kind == "interval":
        m = t["minutes"]
        return f"Every {m // 60} hour(s)" if m % 60 == 0 else f"Every {m} minutes"
    if kind == "daily":
        return f"Every day at {t['time']}"
    if kind == "weekly":
        return f"Every {', '.join(d.capitalize() for d in t['days'])} at {t['time']}"
    if kind == "monthly":
        return f"Monthly on day {t['day']} at {t['time']}"
    return "When LifeOS starts" if kind == "startup" else "Manually"


# ---------------------------------------------------------------- CRUD

_JSON = ("trigger", "conditions", "steps")


def _out(row: Any) -> dict[str, Any]:
    w = db.row_to_dict(row, _JSON)
    assert w is not None
    w["enabled"] = bool(w["enabled"])
    w["trigger_text"] = describe_trigger(w["trigger"])
    return w


def get(wid: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM workflows WHERE id = ?", (wid,)).fetchone()
        if not row:
            raise KeyError(f"Workflow {wid} not found")
        w = _out(row)
        last = conn.execute("SELECT status, summary, finished_at FROM workflow_runs WHERE workflow_id = ? ORDER BY id DESC "
                            "LIMIT 1", (wid,)).fetchone()
    w["last_run"] = dict(last) if last else None
    return w


def list_workflows() -> list[dict[str, Any]]:
    with db.session() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM workflows ORDER BY id")]
    return [get(i) for i in ids]


def _fields(data: dict[str, Any], partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not partial or "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Workflow needs a name")
        out["name"] = name[:100]
    if "description" in data:
        out["description"] = str(data.get("description") or "")[:500]
    if not partial or "trigger" in data:
        out["trigger"] = validate_trigger(data.get("trigger"))
    if not partial or "conditions" in data:
        out["conditions"] = [validate_condition(c) for c in data.get("conditions") or []]
    if not partial or "steps" in data:
        out["steps"] = validate_steps(data.get("steps") or [])
        if not out["steps"]:
            raise ValueError("Add at least one step")
    if "enabled" in data and data["enabled"] is not None:
        out["enabled"] = int(bool(data["enabled"]))
    if "source_text" in data:
        out["source_text"] = data.get("source_text")
    return out


def create(data: dict[str, Any]) -> dict[str, Any]:
    f = _fields(data, partial=False)
    f.setdefault("enabled", 1)
    f["next_run_at"] = next_run(f["trigger"])
    cols = {k: json.dumps(v) if k in _JSON else v for k, v in f.items()} | {"created_at": db.now(), "updated_at": db.now()}
    with db.session() as conn:
        cur = conn.execute(f"INSERT INTO workflows({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                           tuple(cols.values()))
        wid = int(cur.lastrowid)
    activity.log("workflow", f"Created workflow \"{f['name']}\"", details={"trigger": f["trigger"]})
    return get(wid)


def update(wid: int, data: dict[str, Any]) -> dict[str, Any]:
    current = get(wid)
    f = _fields(data, partial=True)
    if "trigger" in f or ("enabled" in f and f["enabled"]):
        f["next_run_at"] = next_run(f.get("trigger") or current["trigger"])
    if f:
        f["updated_at"] = db.now()
        cols = {k: json.dumps(v) if k in _JSON else v for k, v in f.items()}
        with db.session() as conn:
            conn.execute(f"UPDATE workflows SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", (*cols.values(), wid))
        activity.log("workflow", f"Updated workflow \"{current['name']}\"", details={k: data[k] for k in data if k != "steps"})
    return get(wid)


def delete(wid: int) -> dict[str, Any]:
    w = get(wid)
    with db.session() as conn:
        conn.execute("DELETE FROM workflows WHERE id = ?", (wid,))
    activity.log("workflow", f"Deleted workflow \"{w['name']}\"")
    return w


def runs(wid: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with db.session() as conn:
        if wid:
            rows = conn.execute("SELECT r.*, w.name FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id "
                                "WHERE r.workflow_id = ? ORDER BY r.id DESC LIMIT ?", (wid, limit)).fetchall()
        else:
            rows = conn.execute("SELECT r.*, w.name FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id "
                                "ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
    return db.rows_to_dicts(rows, ("log",))


# ---------------------------------------------------------------- conditions

def metric(c: dict[str, Any]) -> float:
    name = c["metric"]
    now = datetime.now(timezone.utc)
    if name == "open_tasks":
        return len(tasks.list_tasks("open"))
    if name == "overdue_tasks":
        iso = now.isoformat(timespec="seconds")
        return len([t for t in tasks.list_tasks("open") if t["due_at"] and t["due_at"] < iso])
    if name == "due_within_hours":
        lo, hi = now.isoformat(timespec="seconds"), (now + timedelta(hours=c.get("hours", 24))).isoformat(timespec="seconds")
        return len([t for t in tasks.list_tasks("open") if t["due_at"] and lo <= t["due_at"] < hi])
    if name == "loose_files":
        found = permissions.folder_by_name(c["folder"])
        if not found:
            return 0
        fid, root = found
        from pathlib import Path

        with db.session() as conn:
            rows = conn.execute("SELECT path FROM files WHERE folder_id = ?", (fid,)).fetchall()
        return len([r for r in rows if Path(r["path"]).parent == root])
    if name == "duplicate_mb":
        with db.session() as conn:
            waste = conn.execute(
                "SELECT COALESCE(SUM(size * (n - 1)), 0) AS w FROM (SELECT size, COUNT(*) AS n FROM files "
                "WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING n > 1)").fetchone()["w"]
        return waste / 1024 ** 2
    if name == "large_files":
        from . import files

        cfg = settings.get_all()
        return len(files.large_files(cfg["large_file_mb"], cfg["stale_days"], limit=500))
    if name == "pending_actions":
        with db.session() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM actions WHERE status = 'pending'").fetchone()["n"]
    if name == "weekday":
        return datetime.now().weekday()
    if name == "hour":
        return datetime.now().hour
    raise ValueError(f"Unknown metric {name}")


def check(c: dict[str, Any]) -> tuple[bool, str]:
    value = metric(c)
    ok = OPS[c["op"]](value, c["value"])
    label = METRICS[c["metric"]] + (f" ({c['folder']})" if c.get("folder") else "") + (
        f" ({c['hours']}h)" if c["metric"] == "due_within_hours" else "")
    shown = f"{value:g}" if isinstance(value, (int, float)) else str(value)
    return ok, f"{label}: {shown} {c['op']} {c['value']:g} → {'yes' if ok else 'no'}"


# ---------------------------------------------------------------- running

_TEMPLATE = re.compile(r"\{\{\s*([a-z_.0-9]+)\s*\}\}", re.I)


def _render(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            cur: Any = scope
            for part in m.group(1).split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
                if cur is None:
                    return ""
            return str(cur)
        return _TEMPLATE.sub(sub, value)
    if isinstance(value, dict):
        return {k: _render(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, scope) for v in value]
    return value


def _notify(title: str, body: str, kind: str = "workflow") -> None:
    if kind == "workflow" and not settings.get("notification_prefs").get("workflows", True):
        return
    with db.session() as conn:
        conn.execute("INSERT INTO notifications(title, body, kind, created_at) VALUES (?,?,?,?)",
                     (title[:200], body[:2000] or None, kind, db.now()))


def _run_step(step: dict[str, Any], scope: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if step["type"] == "tool":
        out = tools.invoke(step["tool"], _render(step["args"], scope), ctx)
        return {"status": "ok" if out["status"] == "done" else "pending" if out["status"] == "pending" else "error",
                "summary": out["summary"], "action_id": (out.get("action") or {}).get("id")}
    if step["type"] == "mission":
        from . import missions

        m = missions.start(_render(step["goal"], scope), step.get("agent"), source="workflow")
        m = missions.wait(m["id"], timeout=6 * 3600)
        ok = m["status"] == "done"
        return {"status": "ok" if ok else "error", "summary": (m.get("result") or m.get("error") or m["status"])[:1500],
                "mission_id": m["id"]}
    _notify(_render(step["title"], scope), _render(step["message"], scope), kind="workflow-step")
    return {"status": "ok", "summary": f"Sent notification: {_render(step['title'], scope)}"}


def execute(wid: int, trigger: str = "manual") -> dict[str, Any]:
    """Run a workflow now, synchronously. Returns the run record."""
    w = get(wid)
    started = db.now()
    with db.session() as conn:
        cur = conn.execute("INSERT INTO workflow_runs(workflow_id, status, trigger, started_at) VALUES (?,?,?,?)",
                           (wid, "running", trigger, started))
        rid = int(cur.lastrowid)
    log: list[dict[str, Any]] = []

    def save(status: str, summary: str | None = None) -> None:
        with db.session() as conn:
            conn.execute("UPDATE workflow_runs SET status = ?, log = ?, summary = ?, finished_at = ? WHERE id = ?",
                         (status, json.dumps(log, default=str), summary, db.now() if status != "running" else None, rid))

    checks = [check(c) for c in w["conditions"]]
    for ok, text in checks:
        log.append({"type": "condition", "status": "ok" if ok else "skipped", "summary": text})
    if not all(ok for ok, _ in checks):
        save("skipped", "Conditions not met")
        _finish(w, "skipped", "Conditions not met", trigger)
        return runs_by_id(rid)

    local = datetime.now().astimezone()
    scope: dict[str, Any] = {"steps": {}, "date": local.strftime("%Y-%m-%d"), "weekday": local.strftime("%A"),
                             "workflow": w["name"]}
    ctx: dict[str, Any] = {"workflow_run_id": rid}
    failed = False
    for step in w["steps"]:
        label = step["label"] or (step.get("tool") or step["type"]).replace("_", " ")
        if step.get("condition"):
            ok, text = check(step["condition"])
            if not ok:
                log.append({"step": step["id"], "label": label, "status": "skipped", "summary": f"Skipped: {text}"})
                save("running")
                continue
        attempt, result = 0, {"status": "error", "summary": "not run"}
        while True:
            try:
                result = _run_step(step, scope, ctx)
            except Exception as exc:  # keep the run alive; the step's on_error decides what happens
                result = {"status": "error", "summary": f"{type(exc).__name__}: {exc}"}
            if result["status"] != "error" or attempt >= step["retries"]:
                break
            attempt += 1
            log.append({"step": step["id"], "label": label, "status": "retry",
                        "summary": f"Attempt {attempt} failed ({result['summary'][:200]}); retrying in {step['retry_delay_s']}s"})
            save("running")
            time.sleep(step["retry_delay_s"])
        scope["steps"][step["id"]] = {"summary": result["summary"], "status": result["status"]}
        log.append({"step": step["id"], "label": label, **result, "attempts": attempt + 1})
        save("running")
        if result["status"] == "error" and step["on_error"] == "stop":
            failed = True
            break

    done = [e for e in log if e.get("step") and e["status"] in ("ok", "pending")]
    pending = [e for e in log if e.get("status") == "pending"]
    errors = [e for e in log if e.get("step") and e["status"] == "error"]
    lines = [f"• {e['summary']}" for e in log if e.get("step") and e["status"] in ("ok", "pending", "error")]
    summary = (f"{len(done)} step(s) done" + (f", {len(pending)} waiting for approval" if pending else "")
               + (f", {len(errors)} failed" if errors else "") + ".\n" + "\n".join(lines))
    status = "failed" if failed else "done"
    save(status, summary)
    _finish(w, status, summary, trigger)
    return runs_by_id(rid)


def runs_by_id(rid: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT r.*, w.name FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id WHERE r.id = ?",
                           (rid,)).fetchone()
    if not row:
        raise KeyError(f"Run {rid} not found")
    return db.row_to_dict(row, ("log",))  # type: ignore[return-value]


def _finish(w: dict[str, Any], status: str, summary: str, trigger: str) -> None:
    with db.session() as conn:
        conn.execute("UPDATE workflows SET last_run_at = ? WHERE id = ?", (db.now(), w["id"]))
    activity.log("workflow", f"Workflow \"{w['name']}\" {status} ({trigger})", details={"summary": summary[:1000]},
                 status={"done": "ok", "skipped": "ok"}.get(status, "error"))
    if status != "skipped":
        _notify(f"Workflow {'finished' if status == 'done' else 'failed'}: {w['name']}", summary)


def run_async(wid: int, trigger: str = "manual") -> None:
    get(wid)
    threading.Thread(target=_safe_execute, args=(wid, trigger), name=f"lifeos-workflow-{wid}", daemon=True).start()


def _safe_execute(wid: int, trigger: str) -> None:
    try:
        execute(wid, trigger)
    except Exception as exc:
        activity.log("workflow", f"Workflow {wid} crashed: {exc}"[:300], status="error")


_running: set[int] = set()
_running_lock = threading.Lock()


def run_due() -> int:
    """Start every enabled scheduled workflow whose time has come. Called by the scheduler."""
    if settings.get("automation_paused"):
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.session() as conn:
        rows = conn.execute("SELECT id, trigger FROM workflows WHERE enabled = 1 AND next_run_at IS NOT NULL "
                            "AND next_run_at <= ?", (now,)).fetchall()
        for r in rows:  # move the schedule forward first so a slow run can't be started twice
            conn.execute("UPDATE workflows SET next_run_at = ? WHERE id = ?", (next_run(json.loads(r["trigger"])), r["id"]))
    started = 0
    for r in rows:
        with _running_lock:
            if r["id"] in _running:
                continue
            _running.add(r["id"])
        started += 1

        def go(wid: int = r["id"]) -> None:
            try:
                _safe_execute(wid, "schedule")
            finally:
                with _running_lock:
                    _running.discard(wid)
        threading.Thread(target=go, name=f"lifeos-workflow-{r['id']}", daemon=True).start()
    return started


def run_startup() -> int:
    if settings.get("automation_paused"):
        return 0
    n = 0
    for w in list_workflows():
        if w["enabled"] and w["trigger"]["type"] == "startup":
            run_async(w["id"], "startup")
            n += 1
    return n


# ---------------------------------------------------------------- natural language → workflow

def _rule_trigger(text: str) -> tuple[dict[str, Any], str]:
    t = text
    time_m = re.search(r"\bat (\d{1,2})(?::(\d{2}))? ?(am|pm)?\b", t, re.I)
    hhmm = None
    if time_m:
        h, mnt, ap = int(time_m.group(1)), int(time_m.group(2) or 0), (time_m.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h <= 23:
            hhmm = f"{h:02d}:{mnt:02d}"
        t = t.replace(time_m.group(0), " ")
    part_of_day = {"morning": "08:00", "afternoon": "14:00", "evening": "18:00", "night": "21:00"}

    def strip(pattern: str) -> str:
        return re.sub(pattern, " ", t, count=1, flags=re.I)

    m = re.search(r"\bevery (\d+) ?(minutes?|mins?|hours?|hrs?)\b", t, re.I)
    if m:
        n = int(m.group(1)) * (60 if m.group(2).lower().startswith("h") else 1)
        return {"type": "interval", "minutes": max(5, n)}, strip(m.re.pattern)
    m = re.search(r"\bevery (hour|half hour)\b|\bhourly\b", t, re.I)
    if m:
        return {"type": "interval", "minutes": 30 if "half" in m.group(0).lower() else 60}, strip(m.re.pattern)
    m = re.search(r"\b(?:when|on|at) (?:lifeos |the app |it |app )?(?:starts?(?: up)?|startup|launch(?:es)?|opens?)\b",
                  t, re.I)
    if m:
        return {"type": "startup"}, strip(m.re.pattern)
    m = re.search(r"\bevery (weekday|weekend)s?\b", t, re.I)
    if m:
        days = DAYS[:5] if m.group(1).lower() == "weekday" else DAYS[5:]
        return {"type": "weekly", "days": days, "time": hhmm or "09:00"}, strip(m.re.pattern)
    m = re.search(r"\b(?:every|each|on) ((?:(?:mon|tues|wednes|thurs|fri|satur|sun)day(?:s)?(?:,? (?:and )?)?)+)", t, re.I)
    if m:
        days = [DAYS[DAY_NAMES[d.lower().rstrip("s")]] for d in re.findall(r"(?:mon|tues|wednes|thurs|fri|satur|sun)days?",
                                                                            m.group(1), re.I)]
        return {"type": "weekly", "days": days, "time": hhmm or "09:00"}, strip(re.escape(m.group(0)))
    m = re.search(r"\bevery (morning|afternoon|evening|night)\b", t, re.I)
    if m:
        return {"type": "daily", "time": hhmm or part_of_day[m.group(1).lower()]}, strip(m.re.pattern)
    m = re.search(r"\b(every ?day|daily|each day)\b", t, re.I)
    if m:
        return {"type": "daily", "time": hhmm or "09:00"}, strip(m.re.pattern)
    m = re.search(r"\b(every week|weekly|each week)\b", t, re.I)
    if m:
        return {"type": "weekly", "days": ["mon"], "time": hhmm or "09:00"}, strip(m.re.pattern)
    m = re.search(r"\b(every month|monthly|each month)\b", t, re.I)
    if m:
        return {"type": "monthly", "day": 1, "time": hhmm or "09:00"}, strip(m.re.pattern)
    return {"type": "manual"}, t


def _rule_draft(text: str) -> dict[str, Any]:
    trigger, rest = _rule_trigger(text)
    rest = re.sub(r"^\s*[,:\-]+\s*|\s+", " ", rest).strip(" ,.")
    steps: list[dict[str, Any]] = []
    for clause in rules.split_clauses(rest):
        clause = clause.strip(" ,.")
        if not clause:
            continue
        plan = rules.plan(clause)
        if plan.complete:
            for s in plan.steps:
                steps.append({"type": "tool", "tool": s["tool"], "args": s.get("args") or {},
                              "label": tools.describe_call(s["tool"], s.get("args") or {})[:120]})
        else:
            steps.append({"type": "mission", "goal": clause, "label": f"Crew: {clause[:100]}"})
    name = re.sub(r"\s+", " ", text).strip()
    return {"name": (name[:1].upper() + name[1:])[:60], "description": text.strip()[:500], "trigger": trigger,
            "conditions": [], "steps": steps, "source_text": text, "planner": "rules"}


def _llm_draft(text: str) -> dict[str, Any]:
    catalog = "\n".join(f"- {t.name}: {t.description}" for t in tools.TOOLS.values())
    agents = ", ".join(a["slug"] for a in crew.specialists())
    folders = ", ".join(p.name for _, p in permissions.approved_roots()) or "none"
    schema = {"type": "object", "required": ["name", "trigger", "steps"], "properties": {
        "name": {"type": "string"}, "description": {"type": "string"},
        "trigger": {"type": "object", "properties": {
            "type": {"type": "string", "enum": list(TRIGGERS)}, "minutes": {"type": "integer"},
            "time": {"type": "string"}, "days": {"type": "array", "items": {"type": "string", "enum": DAYS}},
            "day": {"type": "integer"}}, "required": ["type"]},
        "conditions": {"type": "array", "items": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": list(METRICS)}, "op": {"type": "string", "enum": list(OPS)},
            "value": {"type": "number"}, "folder": {"type": "string"}, "hours": {"type": "integer"}},
            "required": ["metric", "op", "value"]}},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": list(STEP_TYPES)}, "tool": {"type": "string", "enum": list(tools.TOOLS)},
            "args": {"type": "object"}, "goal": {"type": "string"}, "agent": {"type": "string"},
            "title": {"type": "string"}, "message": {"type": "string"}, "label": {"type": "string"}},
            "required": ["type"]}}}}
    prompt = (f"Turn the user's request into a LifeOS workflow.\nTools (for tool steps):\n{catalog}\n"
              f"Approved folders: {folders}. Crew agents (for mission steps): {agents}.\n"
              "Use tool steps for things a single tool does; use a mission step (with a clear goal) for work that needs "
              "reasoning, research or several tools. Use 24-hour HH:MM times. Only add conditions the user asked for.\n"
              f"Request: {text}")
    data = llm.chat_json([{"role": "user", "content": prompt}], schema)
    data["source_text"] = text
    data["planner"] = "llm"
    return data


def draft_from_text(text: str) -> dict[str, Any]:
    """A validated, unsaved workflow built from a plain-language description."""
    text = text.strip()
    if not text:
        raise ValueError("Describe the workflow")
    draft = None
    if settings.get("planner") != "rules" and llm.model_available(settings.get("chat_model")):
        try:
            draft = _llm_draft(text)
            _fields(draft, partial=False)
        except (llm.LLMUnavailable, ValueError, TypeError) as exc:
            activity.log("workflow", f"Model draft failed, used rules: {exc}"[:300], status="error")
            draft = None
    if draft is None:
        draft = _rule_draft(text)
        if not draft["steps"]:
            raise ValueError("Couldn't find any steps in that description")
    f = _fields(draft, partial=False)
    out = {**draft, **{k: v for k, v in f.items() if k in ("name", "trigger", "conditions", "steps")}}
    out["trigger_text"] = describe_trigger(out["trigger"])
    return out


# ---------------------------------------------------------------- templates and suggestions

TEMPLATES: list[dict[str, Any]] = [
    {"name": "Morning briefing", "description": "Every morning: today's summary and a suggested plan.",
     "trigger": {"type": "daily", "time": "08:00"}, "conditions": [],
     "steps": [{"type": "tool", "tool": "daily_summary", "args": {}, "label": "Summarize today"},
               {"type": "tool", "tool": "plan_day", "args": {"available_hours": 6}, "label": "Plan the day"},
               {"type": "notify", "title": "Good morning", "message": "{{steps.s1.summary}}\n\n{{steps.s2.summary}}"}]},
    {"name": "Weekly downloads cleanup", "description": "Fridays at 17:00, if Downloads is cluttered, prepare a tidy-up plan.",
     "trigger": {"type": "weekly", "days": ["fri"], "time": "17:00"},
     "conditions": [{"metric": "loose_files", "folder": "Downloads", "op": ">=", "value": 10}],
     "steps": [{"type": "tool", "tool": "organize_files", "args": {"folder": "Downloads"}, "label": "Organize Downloads"},
               {"type": "tool", "tool": "find_duplicates", "args": {"folder": "Downloads"}, "label": "Check duplicates"}]},
    {"name": "Deadline watch", "description": "Every 2 hours, warn about anything due in the next 24 hours.",
     "trigger": {"type": "interval", "minutes": 120},
     "conditions": [{"metric": "due_within_hours", "hours": 24, "op": ">", "value": 0}],
     "steps": [{"type": "tool", "tool": "list_tasks", "args": {"status": "open", "due_within_days": 1}, "label": "Due soon"},
               {"type": "notify", "title": "Deadlines coming up", "message": "{{steps.s1.summary}}"}]},
    {"name": "Weekly review", "description": "Sunday evening: the crew reviews your week and drafts next week's plan.",
     "trigger": {"type": "weekly", "days": ["sun"], "time": "18:00"}, "conditions": [],
     "steps": [{"type": "mission", "goal": "Review my week: what I completed, what is overdue and what is due next week. "
                "Then draft a realistic plan for next week and save it to the outbox.", "label": "Crew weekly review"}]},
    {"name": "Monthly storage check", "description": "First of the month: large unused files and duplicates.",
     "trigger": {"type": "monthly", "day": 1, "time": "10:00"}, "conditions": [],
     "steps": [{"type": "tool", "tool": "find_large_files", "args": {"min_mb": 200, "older_than_days": 120},
                "label": "Large unused files"},
               {"type": "tool", "tool": "find_duplicates", "args": {}, "label": "Duplicates"},
               {"type": "notify", "title": "Storage check", "message": "{{steps.s1.summary}}\n{{steps.s2.summary}}"}]},
    {"name": "Inbox triage", "description": "Weekdays at 09:00 the comms agent triages unread email into a briefing.",
     "trigger": {"type": "weekly", "days": DAYS[:5], "time": "09:00"}, "conditions": [],
     "steps": [{"type": "mission", "agent": "comms", "goal": "Check my unread email, group it by urgency, list what needs a "
                "reply today, and save the briefing to the outbox.", "label": "Inbox triage"}]},
]


def templates() -> list[dict[str, Any]]:
    out = []
    for t in TEMPLATES:
        item = {**t, "trigger_text": describe_trigger(validate_trigger(t["trigger"]))}
        item["steps"] = validate_steps(t["steps"]) if all(
            s.get("tool") in tools.TOOLS or s["type"] != "tool" for s in t["steps"]) else t["steps"]
        out.append(item)
    return out


def suggestions() -> list[dict[str, Any]]:
    """Workflow improvements: automate repeated manual work, fix workflows that keep failing."""
    since = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat(timespec="seconds")
    flows = list_workflows()
    automated_tools = {s.get("tool") for w in flows if w["enabled"] for s in w["steps"]}
    out = []
    with db.session() as conn:
        rows = conn.execute("SELECT tool, COUNT(*) AS n FROM actions WHERE created_at >= ? AND tool IS NOT NULL "
                            "AND conversation_id IS NOT NULL GROUP BY tool HAVING n >= 3", (since,)).fetchall()
        for r in rows:
            if r["tool"] in automated_tools:
                continue
            label = r["tool"].replace("_", " ")
            out.append({"id": f"automate-{r['tool']}-{r['n'] // 3}", "kind": "workflow", "priority": 2,
                        "title": f"Automate \"{label}\"", "detail": f"You did this {r['n']} times in 3 weeks. "
                        "A scheduled workflow could prepare it for you.", "link": "/automations"})
        for w in flows:
            last = conn.execute("SELECT status FROM workflow_runs WHERE workflow_id = ? ORDER BY id DESC LIMIT 3",
                                (w["id"],)).fetchall()
            if len(last) == 3 and all(x["status"] == "failed" for x in last):
                out.append({"id": f"wf-failing-{w['id']}", "kind": "workflow", "priority": 3,
                            "title": f"Workflow \"{w['name']}\" keeps failing",
                            "detail": "The last 3 runs failed. Open it to check its steps.", "link": "/automations"})
    return out
