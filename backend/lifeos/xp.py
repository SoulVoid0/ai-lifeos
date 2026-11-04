"""Agent XP and levels, worked out from what each agent has actually done (nothing to fake or grind).

XP sources: finished mission tasks (more for well-reviewed ones, a bonus for your 4-5 star ratings), arena wins,
council votes that matched the decision, and lessons learned. Levels follow a gentle curve with ranks; a
level-up puts a note in the bell (and the app plays the level-up sound). The stats page shows the breakdown.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from . import common, crew, db, settings

RANKS = ["Cadet", "Crewman", "Ensign", "Lieutenant", "Lt. Commander", "Commander", "Captain", "Commodore", "Admiral",
         "Fleet Admiral"]
XP = {"task": 10, "task_good": 5, "rated_high": 15, "task_failed": 1, "arena_win": 25, "arena_tie": 8,
      "council": 4, "council_majority": 6, "lesson": 3}


def level_for(xp: int) -> tuple[int, int, int]:
    """(level, xp at the start of this level, xp needed for the next). Level n starts at 50·n·(n-1)."""
    lvl = int((1 + math.sqrt(1 + 4 * xp / 50)) / 2)
    lvl = max(1, lvl)
    while 50 * (lvl + 1) * lvl <= xp:
        lvl += 1
    while lvl > 1 and 50 * lvl * (lvl - 1) > xp:
        lvl -= 1
    return lvl, 50 * lvl * (lvl - 1), 50 * (lvl + 1) * lvl


def rank_for(level: int) -> str:
    return RANKS[min(len(RANKS) - 1, (level - 1) // 3)]


def _blank() -> dict[str, Any]:
    return {"xp": 0, "tasks_done": 0, "tasks_failed": 0, "good_tasks": 0, "rated_high": 0, "arena_wins": 0,
            "arena_losses": 0, "arena_ties": 0, "councils": 0, "council_majority": 0, "lessons": 0,
            "work_seconds": 0.0, "scores": [], "week_xp": 0, "steps": 0}


def _secs(a: str | None, b: str | None) -> float:
    try:
        return max(0.0, (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()) if a and b else 0.0
    except ValueError:
        return 0.0


def compute() -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")

    def s(slug: str) -> dict[str, Any]:
        return stats.setdefault(slug, _blank())

    def add(slug: str, pts: int, when: str | None) -> None:
        st = s(slug)
        st["xp"] += pts
        if when and when >= week:
            st["week_xp"] += pts

    with db.session() as conn:
        for r in conn.execute("SELECT agent, status, score, user_rating, steps, started_at, finished_at FROM mission_tasks "
                              "WHERE status IN ('done', 'failed')"):
            st = s(r["agent"])
            if r["status"] == "done":
                st["tasks_done"] += 1
                pts = XP["task"]
                if r["score"] is not None:
                    st["scores"].append(float(r["score"]))
                    if r["score"] >= 4:
                        st["good_tasks"] += 1
                        pts += XP["task_good"]
                if (r["user_rating"] or 0) >= 4:
                    st["rated_high"] += 1
                    pts += XP["rated_high"]
                add(r["agent"], pts, r["finished_at"])
            else:
                st["tasks_failed"] += 1
                add(r["agent"], XP["task_failed"], r["finished_at"])
            st["steps"] += r["steps"] or 0
            st["work_seconds"] += _secs(r["started_at"], r["finished_at"])
        for r in conn.execute("SELECT a_agent, b_agent, winner, decided_at FROM arena_matches WHERE status = 'decided'"):
            if r["winner"] == "tie":
                for side in ("a_agent", "b_agent"):
                    s(r[side])["arena_ties"] += 1
                    add(r[side], XP["arena_tie"], r["decided_at"])
                continue
            win = r["a_agent"] if r["winner"] == "a" else r["b_agent"]
            lose = r["b_agent"] if r["winner"] == "a" else r["a_agent"]
            if win == lose:
                continue
            s(win)["arena_wins"] += 1
            add(win, XP["arena_win"], r["decided_at"])
            s(lose)["arena_losses"] += 1
        import json

        for r in conn.execute("SELECT agents, options, votes, decision, finished_at FROM council_sessions WHERE status = 'decided'"):
            options, votes = json.loads(r["options"] or "[]"), json.loads(r["votes"] or "[]")
            for slug in json.loads(r["agents"] or "[]"):
                s(slug)["councils"] += 1
                add(slug, XP["council"], r["finished_at"])
            for v in votes:
                if 0 <= v.get("choice", -1) < len(options) and (r["decision"] or "").startswith(options[v["choice"]]):
                    s(v["agent"])["council_majority"] += 1
                    add(v["agent"], XP["council_majority"], r["finished_at"])
        for r in conn.execute("SELECT agent, created_at FROM lessons"):
            s(r["agent"])["lessons"] += 1
            add(r["agent"], XP["lesson"], r["created_at"])
    return stats


def board() -> dict[str, Any]:
    stats = compute()
    out = []
    for a in crew.list_agents():
        st = stats.get(a["slug"], _blank())
        lvl, lo, hi = level_for(st["xp"])
        scores = st.pop("scores")
        out.append({"slug": a["slug"], "name": a["name"], "role": a["role"], "color": a["color"], "icon": a["icon"],
                    "level": lvl, "rank": rank_for(lvl), "level_start": lo, "next_level": hi,
                    "progress": round((st["xp"] - lo) / max(1, hi - lo), 3),
                    "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
                    "success_rate": round(st["tasks_done"] / (st["tasks_done"] + st["tasks_failed"]), 3)
                    if st["tasks_done"] + st["tasks_failed"] else None, **st})
    out.sort(key=lambda x: -x["xp"])
    total = sum(x["xp"] for x in out)
    return {"agents": out, "crew_xp": total, "crew_level": level_for(total // max(1, len(out)))[0], "xp_table": XP}


def check_levels() -> list[dict[str, Any]]:
    """Notify about level-ups since the last check (run by the scheduler)."""
    known: dict[str, int] = dict(settings.get("xp_levels") or {})
    first = not known
    ups = []
    for a in board()["agents"]:
        before = int(known.get(a["slug"], 0))
        if a["level"] > before:
            if not first and before:
                ups.append({"slug": a["slug"], "name": a["name"], "level": a["level"], "rank": a["rank"]})
                common.notify(f"Level up! {a['name']} reached level {a['level']}", f"Rank: {a['rank']}", kind="levelup")
            known[a["slug"]] = a["level"]
    if ups or first or known != settings.get("xp_levels"):
        settings.update({"xp_levels": known})
    return ups
