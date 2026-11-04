"""Agent arena: two contestants answer the same task, you pick the better one, and the crew learns what you like.

A contestant is an agent (its persona, lessons and model), optionally on a different model, so the arena can
compare two agents ("Scout vs Mnemo") or one agent on two models ("Forge on qwen3-coder vs qwen2.5-coder").
Answers are shown side by side as A and B in random order, so you judge the work, not the name.

Arena rounds don't run tools: both contestants answer from what they know and what LifeOS tells them, which keeps
a round free of side effects (nothing is created, moved or sent twice). Your vote:
  * becomes a lesson for the losing side (what the winner did that you liked) and the winner (keep doing it),
    distilled by the local model from your reason and the two answers, so it shapes every later task;
  * counts on the arena leaderboard (wins per agent and per model), and you can switch an agent to the model
    that keeps winning.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from typing import Any

from . import activity, crew, db, learning, llm, missions

LESSON_SCHEMA = {"type": "object", "properties": {
    "winner_lesson": {"type": "string"}, "loser_lesson": {"type": "string"}}, "required": ["winner_lesson", "loser_lesson"]}


def _row(r: Any) -> dict[str, Any]:
    out = db.row_to_dict(r, ("lessons",))
    assert out is not None
    names = {a["slug"]: a["name"] for a in crew.list_agents()}
    out["a_name"] = names.get(out["a_agent"], out["a_agent"])
    out["b_name"] = names.get(out["b_agent"], out["b_agent"])
    if out["status"] != "decided":  # blind until you vote
        out["a_name"] = out["b_name"] = None
        out["a_agent_hidden"], out["b_agent_hidden"] = True, True
    return out


def _contestant(spec: dict[str, Any]) -> tuple[str, str]:
    slug = str(spec.get("agent") or "").strip()
    agent = crew.get(slug)
    model = str(spec.get("model") or "").strip() or agent["effective_model"]
    provider, _ = llm.parse_ref(model)
    if provider not in llm.PROVIDERS:
        raise ValueError(f"Unknown model provider in {model}")
    return slug, model


def start(prompt: str, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Give the contestants a task")
    if len(prompt) > 4000:
        raise ValueError("The task is too long (4000 characters max)")
    first, second = _contestant(a), _contestant(b)
    if first == second:
        raise ValueError("Pick two different agents, or the same agent on two different models")
    if random.random() < 0.5:  # blind: which side is which is random
        first, second = second, first
    with db.session() as conn:
        cur = conn.execute("INSERT INTO arena_matches(prompt, a_agent, a_model, b_agent, b_model, created_at) "
                           "VALUES (?,?,?,?,?,?)", (prompt, first[0], first[1], second[0], second[1], db.now()))
        mid = int(cur.lastrowid)
    threading.Thread(target=_run, args=(mid,), daemon=True, name=f"lifeos-arena-{mid}").start()
    activity.log("crew", f"Arena round {mid}: {prompt[:120]}")
    return get(mid)


def _answer(slug: str, model: str, prompt: str) -> str:
    agent = crew.get(slug)
    lessons = learning.lessons_for(slug, prompt)
    system = (f"You are {agent['name']}, the {agent['role']} on the user's LifeOS crew. {agent['description']}\n"
              f"{agent['persona']}\n" + (learning.playbook_text(lessons) + "\n" if lessons else "")
              + "This is an arena round: another contestant answers the same task and the user picks the better answer. "
                "No tools are available, so do the work directly in your answer: be complete, concrete and correct, "
                "use Markdown where it helps, and say plainly what you would need to look up instead of inventing it.")
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    with llm.track(None, slug):
        return llm.chat_text(msgs, model=model, timeout=900)


def _run(mid: int) -> None:
    m = get(mid)
    try:
        for side in ("a", "b"):  # one after the other: a single GPU runs one model at a time anyway
            started = time.monotonic()
            text = _answer(m[f"{side}_agent"], m[f"{side}_model"], m["prompt"])
            with db.session() as conn:
                conn.execute(f"UPDATE arena_matches SET {side}_result = ?, {side}_seconds = ? WHERE id = ?",
                             (text, round(time.monotonic() - started, 1), mid))
        with db.session() as conn:
            conn.execute("UPDATE arena_matches SET status = 'ready' WHERE id = ?", (mid,))
        missions._notify("Arena round ready: pick the winner", m["prompt"][:200])
    except Exception as exc:  # noqa: BLE001 - shown on the round
        with db.session() as conn:
            conn.execute("UPDATE arena_matches SET status = 'failed', error = ? WHERE id = ?", (str(exc)[:500], mid))


def get(mid: int) -> dict[str, Any]:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM arena_matches WHERE id = ?", (mid,)).fetchone()
    if not r:
        raise KeyError(f"Arena round {mid} not found")
    return _row(r)


def list_matches(limit: int = 30) -> list[dict[str, Any]]:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM arena_matches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def _lessons(m: dict[str, Any], winner: str, reason: str) -> dict[str, str]:
    """Turn the vote into two short rules. Falls back to the user's own words without a model."""
    w, lose = winner, "b" if winner == "a" else "a"
    fallback = {"winner_lesson": f"The user preferred this kind of answer to \"{m['prompt'][:80]}\""
                                 + (f": {reason}" if reason else "."),
                "loser_lesson": f"For tasks like \"{m['prompt'][:80]}\", the user preferred another approach"
                                + (f": {reason}" if reason else ".")}
    model = crew.get(crew.OVERSEER)["effective_model"]
    if not llm.model_available(model):
        return fallback
    prompt = (f"Task: {m['prompt'][:1500]}\n\nWINNING answer:\n{(m[f'{w}_result'] or '')[:3000]}\n\n"
              f"LOSING answer:\n{(m[f'{lose}_result'] or '')[:3000]}\n\nThe user's reason: {reason or '(none given)'}\n\n"
              "Write two short, general rules (max 25 words each) about what this user likes, that would help on "
              "similar future tasks: winner_lesson for the winner (what to keep doing) and loser_lesson for the loser "
              "(what to change). Don't mention 'the arena', 'answer A/B' or the agents' names.")
    try:
        data = llm.chat_json([{"role": "user", "content": prompt}], LESSON_SCHEMA, model=model, timeout=300)
        return {"winner_lesson": str(data.get("winner_lesson") or fallback["winner_lesson"]),
                "loser_lesson": str(data.get("loser_lesson") or fallback["loser_lesson"])}
    except Exception:  # noqa: BLE001
        return fallback


def vote(mid: int, winner: str, reason: str = "") -> dict[str, Any]:
    if winner not in ("a", "b", "tie"):
        raise ValueError("winner must be a, b or tie")
    m = get(mid)
    if m["status"] != "ready":
        raise ValueError(f"Round {mid} is {m['status']}")
    reason = re.sub(r"\s+", " ", reason or "").strip()[:400]
    added: list[dict[str, Any]] = []
    if winner != "tie":
        lose = "b" if winner == "a" else "a"
        texts = _lessons(m, winner, reason)
        for side, key in ((winner, "winner_lesson"), (lose, "loser_lesson")):
            if side == winner and m["a_agent"] == m["b_agent"]:
                continue  # same agent on two models: one lesson about the preference is enough
            lesson = learning.add_lesson(m[f"{side}_agent"], texts[key], source="user")
            if lesson:
                added.append(lesson)
    with db.session() as conn:
        conn.execute("UPDATE arena_matches SET status = 'decided', winner = ?, reason = ?, lessons = ?, decided_at = ? "
                     "WHERE id = ?", (winner, reason, json.dumps([l["id"] for l in added]), db.now(), mid))
    activity.log("crew", f"Arena round {mid}: {'tie' if winner == 'tie' else 'winner ' + winner.upper()}"
                 + (f" ({reason[:100]})" if reason else ""))
    out = get(mid)
    out["new_lessons"] = added
    return out


def delete(mid: int) -> dict[str, Any]:
    m = get(mid)
    with db.session() as conn:
        conn.execute("DELETE FROM arena_matches WHERE id = ?", (mid,))
    return m


def leaderboard() -> dict[str, Any]:
    with db.session() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM arena_matches WHERE status = 'decided'").fetchall()]
    names = {a["slug"]: a["name"] for a in crew.list_agents()}
    agents: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    for r in rows:
        for side in ("a", "b"):
            res = "tie" if r["winner"] == "tie" else ("win" if r["winner"] == side else "loss")
            for table, key in ((agents, r[f"{side}_agent"]), (models, r[f"{side}_model"])):
                e = table.setdefault(key, {"win": 0, "loss": 0, "tie": 0})
                e[res] += 1
    def rank(table: dict[str, dict[str, Any]], label: Any) -> list[dict[str, Any]]:
        out = []
        for k, e in table.items():
            games = e["win"] + e["loss"] + e["tie"]
            out.append({"key": k, "name": label(k), **e, "games": games,
                        "win_rate": round((e["win"] + e["tie"] / 2) / games, 2) if games else None})
        return sorted(out, key=lambda x: (x["win_rate"] or 0, x["games"]), reverse=True)
    return {"agents": rank(agents, lambda k: names.get(k, k)), "models": rank(models, lambda k: k), "rounds": len(rows)}
