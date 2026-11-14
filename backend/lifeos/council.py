"""Agent council: a few agents debate a question in rounds, then each votes and the majority is the decision.

Round 1: each member gives an opening position. Later rounds: each sees the others' latest arguments and may push
back or change its mind. Then the chair (Atlas, the overseer) turns the debate into 2-4 concrete options, everyone
votes with a one-line reason, and the option with the most votes wins (the chair breaks a tie). No tools run, so a
council only produces advice; you can turn the decision into a mission with one click.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from . import activity, common, crew, db, learning, llm

MAX_MEMBERS = 6
OPTIONS_SCHEMA = {"type": "object", "properties": {
    "options": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}},
    "required": ["options", "summary"]}
VOTE_SCHEMA = {"type": "object", "properties": {"choice": {"type": "integer"}, "reason": {"type": "string"}},
               "required": ["choice", "reason"]}
_stop: dict[int, threading.Event] = {}


def _row(r: Any) -> dict[str, Any]:
    out = db.row_to_dict(r, ("agents", "transcript", "options", "votes"))
    assert out is not None
    names = {a["slug"]: a["name"] for a in crew.list_agents()}
    out["names"] = {s: names.get(s, s) for s in out["agents"]}
    tally: dict[int, int] = {}
    for v in out["votes"]:
        tally[v["choice"]] = tally.get(v["choice"], 0) + 1
    out["tally"] = [tally.get(i, 0) for i in range(len(out["options"]))]
    return out


def get(sid: int) -> dict[str, Any]:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM council_sessions WHERE id = ?", (sid,)).fetchone()
    if not r:
        raise KeyError(f"Council {sid} not found")
    return _row(r)


def list_sessions(limit: int = 30) -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM council_sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def delete(sid: int) -> dict[str, Any]:
    s = get(sid)
    stop(sid)
    with db.session() as conn:
        conn.execute("DELETE FROM council_sessions WHERE id = ?", (sid,))
    return s


def default_members() -> list[str]:
    enabled = {a["slug"] for a in crew.list_agents() if a["enabled"]}
    picks = [s for s in ("planner", "research", "coder", "memory") if s in enabled]
    return picks or [s for s in enabled if s != crew.OVERSEER][:3]


def start(question: str, members: list[str] | None = None, rounds: int = 2) -> dict[str, Any]:
    question = re.sub(r"\s+", " ", question or "").strip()
    if len(question) < 5:
        raise ValueError("Ask the council a question")
    if len(question) > 3000:
        raise ValueError("The question is too long (3000 characters max)")
    members = list(dict.fromkeys(members or default_members()))
    if not 2 <= len(members) <= MAX_MEMBERS:
        raise ValueError(f"A council needs 2 to {MAX_MEMBERS} members")
    for slug in members:
        if not crew.get(slug)["enabled"]:
            raise ValueError(f"{slug} is turned off")
    rounds = max(1, min(int(rounds), 4))
    with db.session() as conn:
        cur = conn.execute("INSERT INTO council_sessions(question, agents, rounds, created_at) VALUES (?,?,?,?)",
                           (question, json.dumps(members), rounds, db.now()))
        sid = int(cur.lastrowid)
    _stop[sid] = threading.Event()
    threading.Thread(target=_run, args=(sid,), daemon=True, name=f"lifeos-council-{sid}").start()
    activity.log("crew", f"Council {sid} convened: {question[:120]}")
    return get(sid)


def stop(sid: int) -> None:
    ev = _stop.get(sid)
    if ev:
        ev.set()


def _save(sid: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in fields.values()]
    with db.session() as conn:
        conn.execute(f"UPDATE council_sessions SET {sets} WHERE id = ?", (*vals, sid))


def _system(agent: dict[str, Any], question: str) -> str:
    lessons = learning.lessons_for(agent["slug"], question)
    return (f"You are {agent['name']}, the {agent['role']} on the user's LifeOS crew. {agent['description']}\n"
            + (f"Voice: {agent['voice']}\n" if agent.get("voice") else "")
            + (learning.playbook_text(lessons) + "\n" if lessons else "")
            + "You sit on a council with other agents to help the user decide something. Argue from your own "
              "expertise, be concrete and honest about trade-offs and uncertainty, and keep each turn under 150 words. "
              "Change your mind if someone makes a better case.")


def _turn(slug: str, question: str, transcript: list[dict[str, Any]], rnd: int, rounds: int) -> str:
    agent = crew.get(slug)
    if rnd == 1:
        ask = f"The question: {question}\n\nGive your opening position."
    else:
        latest = {t["agent"]: t["text"] for t in transcript}
        others = "\n\n".join(f"{crew.get(s)['name']}: {txt}" for s, txt in latest.items() if s != slug)
        ask = (f"The question: {question}\n\nWhat the others said most recently:\n{others}\n\n"
               + ("Respond: where do you agree, where do you push back, and what's your position now?" if rnd < rounds
                  else "Final round: give your closing position in a few sentences."))
    msgs = [{"role": "system", "content": _system(agent, question)}, {"role": "user", "content": ask}]
    with llm.track(None, slug):
        return llm.chat_text(msgs, model=agent["effective_model"], timeout=600).strip()


def _debate_text(transcript: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[Round {t['round']}] {crew.get(t['agent'])['name']}: {t['text']}" for t in transcript)[-14000:]


def _options(question: str, transcript: list[dict[str, Any]]) -> tuple[list[str], str]:
    chair = crew.get(crew.OVERSEER)
    prompt = (f"Question: {question}\n\nThe council's debate:\n{_debate_text(transcript)}\n\n"
              "As chair, list 2 to 4 distinct, concrete options the council actually argued for (each one sentence, "
              "starting with a verb), and a 2-3 sentence neutral summary of the debate.")
    data = llm.chat_json([{"role": "user", "content": prompt}], OPTIONS_SCHEMA, model=chair["effective_model"], timeout=300)
    opts = [re.sub(r"\s+", " ", str(o)).strip()[:300] for o in data.get("options") or [] if str(o).strip()]
    opts = list(dict.fromkeys(opts))[:4]
    if len(opts) < 2:
        opts = (opts + ["Go ahead", "Don't do it"])[:2]
    return opts, str(data.get("summary") or "")[:1200]


def _vote(slug: str, question: str, transcript: list[dict[str, Any]], options: list[str]) -> dict[str, Any]:
    agent = crew.get(slug)
    listing = "\n".join(f"{i}. {o}" for i, o in enumerate(options))
    prompt = (f"Question: {question}\n\nThe debate:\n{_debate_text(transcript)}\n\nOptions:\n{listing}\n\n"
              "Vote for the option you now think is best: `choice` is its number, `reason` one sentence.")
    msgs = [{"role": "system", "content": _system(agent, question)}, {"role": "user", "content": prompt}]
    with llm.track(None, slug):
        data = llm.chat_json(msgs, VOTE_SCHEMA, model=agent["effective_model"], timeout=300)
    try:
        choice = int(data.get("choice"))
    except (TypeError, ValueError):
        choice = 0
    return {"agent": slug, "choice": max(0, min(choice, len(options) - 1)), "reason": str(data.get("reason") or "")[:300]}


def tally_winner(votes: list[dict[str, Any]], n: int, tiebreak: int | None = None) -> tuple[int, bool]:
    """(winning option, whether it was a tie broken by the chair)."""
    counts = [0] * n
    for v in votes:
        counts[v["choice"]] += 1
    best = max(counts)
    leaders = [i for i, c in enumerate(counts) if c == best]
    if len(leaders) == 1:
        return leaders[0], False
    return (tiebreak if tiebreak in leaders else leaders[0]), True


def _run(sid: int) -> None:
    s = get(sid)
    ev = _stop.setdefault(sid, threading.Event())
    transcript: list[dict[str, Any]] = []
    try:
        for rnd in range(1, s["rounds"] + 1):
            for slug in s["agents"]:
                if ev.is_set():
                    raise InterruptedError("Stopped")
                started = time.monotonic()
                text = _turn(slug, s["question"], transcript, rnd, s["rounds"])
                transcript.append({"round": rnd, "agent": slug, "text": text, "seconds": round(time.monotonic() - started, 1)})
                _save(sid, transcript=transcript)
        _save(sid, status="voting")
        options, summary = _options(s["question"], transcript)
        _save(sid, options=options, summary=summary)
        votes: list[dict[str, Any]] = []
        for slug in s["agents"]:
            if ev.is_set():
                raise InterruptedError("Stopped")
            votes.append(_vote(slug, s["question"], transcript, options))
            _save(sid, votes=votes)
        chair_pick = None
        winner, tied = tally_winner(votes, len(options))
        if tied:
            chair_pick = _vote(crew.OVERSEER, s["question"], transcript, options)["choice"]
            winner, _ = tally_winner(votes, len(options), chair_pick)
        decision = options[winner] + (" (tie broken by the chair)" if tied else "")
        _save(sid, decision=decision, status="decided", finished_at=db.now())
        common.notify("The council has decided", f"{s['question'][:120]}\n→ {decision}", kind="crew")
        activity.log("crew", f"Council {sid} decided: {decision[:150]}")
    except InterruptedError:
        _save(sid, status="stopped", finished_at=db.now())
    except Exception as exc:  # noqa: BLE001 - shown on the session
        _save(sid, status="failed", error=f"{type(exc).__name__}: {exc}"[:500], finished_at=db.now())
    finally:
        _stop.pop(sid, None)


def choose(sid: int, option: int | None) -> dict[str, Any]:
    """Record what the user went with (the council's pick or another option)."""
    s = get(sid)
    if option is not None and not 0 <= option < len(s["options"]):
        raise ValueError("No such option")
    _save(sid, user_choice=s["options"][option] if option is not None else None)
    return get(sid)
