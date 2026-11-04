"""Learn mode: flashcard decks made by the crew from a topic, your notes or a file, reviewed with spaced repetition.

Sage (the researcher) writes the cards; reviews use SM-2 (the Anki algorithm): grade 0 "again", 1 "hard",
2 "good", 3 "easy". Cards you know drift weeks apart; cards you miss come back the same session. A typed answer
can be checked by a model ("close enough?") before you grade it. Once a day the bell says how many cards are due.
"""
from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import activity, common, crew, db, files, llm

CARDS_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "cards": {"type": "array", "items": {"type": "object", "properties": {
        "front": {"type": "string"}, "back": {"type": "string"}}, "required": ["front", "back"]}}},
    "required": ["title", "cards"]}
CHECK_SCHEMA = {"type": "object", "properties": {"correct": {"type": "boolean"}, "feedback": {"type": "string"},
                                                 "suggested_grade": {"type": "integer"}},
                "required": ["correct", "feedback", "suggested_grade"]}
MAKER = "research"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _maker_model() -> str:
    try:
        return crew.get(MAKER)["effective_model"]
    except KeyError:
        return crew.get(crew.OVERSEER)["effective_model"]


# ---------------------------------------------------------------- decks

def _deck(r: Any, conn: Any) -> dict[str, Any]:
    d = dict(r)
    now = _iso(_now())
    d["cards"] = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id = ?", (d["id"],)).fetchone()[0]
    d["due"] = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id = ? AND due_at <= ?", (d["id"], now)).fetchone()[0]
    d["new"] = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id = ? AND reps = 0", (d["id"],)).fetchone()[0]
    d["learned"] = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id = ? AND interval_days >= 21", (d["id"],)).fetchone()[0]
    return d


def list_decks() -> list[dict[str, Any]]:
    with db.session() as conn:
        return [_deck(r, conn) for r in conn.execute("SELECT * FROM decks ORDER BY id DESC").fetchall()]


def get_deck(deck_id: int, with_cards: bool = True) -> dict[str, Any]:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
        if not r:
            raise KeyError(f"Deck {deck_id} not found")
        d = _deck(r, conn)
        if with_cards:
            d["card_list"] = [dict(c) for c in conn.execute("SELECT * FROM cards WHERE deck_id = ? ORDER BY id", (deck_id,))]
    return d


def delete_deck(deck_id: int) -> dict[str, Any]:
    d = get_deck(deck_id, with_cards=False)
    with db.session() as conn:
        conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
    return d


def _source_text(topic: str, text: str, path: str) -> tuple[str, str]:
    if path:
        doc = files.read_document(path, max_chars=12000)
        return f"File: {doc['name']}", f"Make flashcards from this document ({doc['name']}):\n\n{doc['text']}"
    if text.strip():
        return "Your notes", f"Make flashcards from these notes:\n\n{text[:12000]}"
    if topic.strip():
        return f"Topic: {topic.strip()[:200]}", f"Make flashcards that teach the essentials of: {topic.strip()[:500]}"
    raise ValueError("Give a topic, some notes or a file")


def _clean_cards(raw: list[Any]) -> list[tuple[str, str]]:
    out, seen = [], set()
    for c in raw:
        if not isinstance(c, dict):
            continue
        f = re.sub(r"\s+", " ", str(c.get("front") or "")).strip()[:500]
        b = str(c.get("back") or "").strip()[:1500]
        if f and b and f.lower() not in seen:
            seen.add(f.lower())
            out.append((f, b))
    return out


def create_deck(topic: str = "", text: str = "", path: str = "", count: int = 15, title: str = "") -> dict[str, Any]:
    source, ask = _source_text(topic, text, path)  # validates (and checks the file is in an approved folder) first
    count = max(3, min(int(count), 60))
    with db.session() as conn:
        cur = conn.execute("INSERT INTO decks(title, source, status, created_at) VALUES (?,?,?,?)",
                           (title.strip()[:120] or topic.strip()[:120] or "New deck", source, "making", db.now()))
        deck_id = int(cur.lastrowid)
    threading.Thread(target=_make, args=(deck_id, ask, count, bool(title.strip())), daemon=True,
                     name=f"lifeos-deck-{deck_id}").start()
    return get_deck(deck_id)


def _make(deck_id: int, ask: str, count: int, keep_title: bool) -> None:
    prompt = (f"{ask}\n\nWrite exactly {count} flashcards. Each `front` is one clear question or prompt (no yes/no "
              "questions); each `back` is the short, correct answer (one or two sentences, plus a tiny example where it "
              "helps). Cover the most important ideas first, go from basics to details, and don't repeat yourself. "
              "`title` is a short name for the deck.")
    try:
        with llm.track(None, MAKER):
            data = llm.chat_json([{"role": "user", "content": prompt}], CARDS_SCHEMA, model=_maker_model(), timeout=900)
        cards = _clean_cards(data.get("cards") or [])
        if not cards:
            raise ValueError("The model didn't write any usable cards")
        add_cards(deck_id, cards)
        with db.session() as conn:
            if not keep_title and data.get("title"):
                conn.execute("UPDATE decks SET title = ? WHERE id = ?", (str(data["title"])[:120], deck_id))
            conn.execute("UPDATE decks SET status = 'ready' WHERE id = ?", (deck_id,))
        activity.log("learn", f"Made a deck of {len(cards)} flashcards")
    except Exception as exc:  # noqa: BLE001 - shown on the deck
        with db.session() as conn:
            conn.execute("UPDATE decks SET status = 'failed', error = ? WHERE id = ?", (f"{type(exc).__name__}: {exc}"[:400], deck_id))


def add_cards(deck_id: int, cards: list[tuple[str, str]]) -> int:
    now = _iso(_now())
    with db.session() as conn:
        conn.executemany("INSERT INTO cards(deck_id, front, back, due_at) VALUES (?,?,?,?)",
                         [(deck_id, f, b, now) for f, b in cards])
    return len(cards)


def update_card(card_id: int, front: str | None = None, back: str | None = None) -> dict[str, Any]:
    with db.session() as conn:
        if front is not None:
            conn.execute("UPDATE cards SET front = ? WHERE id = ?", (front.strip()[:500], card_id))
        if back is not None:
            conn.execute("UPDATE cards SET back = ? WHERE id = ?", (back.strip()[:1500], card_id))
        r = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if not r:
        raise KeyError(f"Card {card_id} not found")
    return dict(r)


def delete_card(card_id: int) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))


# ---------------------------------------------------------------- reviewing

def sm2(ease: float, interval: float, reps: int, grade: int) -> tuple[float, float, int]:
    """SM-2 with 4 buttons. Returns (ease, interval in days, reps). Grade 0 = again, 1 = hard, 2 = good, 3 = easy."""
    if grade == 0:
        return max(1.3, ease - 0.2), 0.0, 0  # back in this session
    q = {1: 3, 2: 4, 3: 5}[grade]
    ease = max(1.3, ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
    if reps == 0:
        interval = 1.0 if grade < 3 else 3.0
    elif reps == 1:
        interval = 3.0 if grade == 1 else 6.0
    else:
        interval = interval * (1.2 if grade == 1 else ease * (1.3 if grade == 3 else 1.0))
    return round(ease, 3), round(min(interval, 3650), 2), reps + 1


def queue(deck_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Due cards, oldest first, with new cards capped per day."""
    new_cap = int(common.group("learn")["new_per_day"])
    now = _iso(_now())
    today = _iso(datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc))
    where, args = ("AND c.deck_id = ?", [deck_id]) if deck_id else ("", [])
    with db.session() as conn:
        seen_new = conn.execute("SELECT COUNT(DISTINCT r.card_id) FROM card_reviews r JOIN cards c ON c.id = r.card_id "
                                f"WHERE r.reviewed_at >= ? {where} AND r.card_id IN (SELECT card_id FROM card_reviews "
                                "GROUP BY card_id HAVING MIN(reviewed_at) >= ?)", (today, *args, today)).fetchone()[0]
        due = conn.execute(f"SELECT c.*, d.title AS deck_title FROM cards c JOIN decks d ON d.id = c.deck_id "
                           f"WHERE c.due_at <= ? AND c.reps > 0 {where} ORDER BY c.due_at LIMIT ?", (now, *args, limit)).fetchall()
        fresh = conn.execute(f"SELECT c.*, d.title AS deck_title FROM cards c JOIN decks d ON d.id = c.deck_id "
                             f"WHERE c.reps = 0 AND c.due_at <= ? {where} ORDER BY c.id LIMIT ?",
                             (now, *args, max(0, new_cap - seen_new))).fetchall()
    cards = [dict(r) for r in due] + [dict(r) for r in fresh]
    return cards[:limit]


def review(card_id: int, grade: int) -> dict[str, Any]:
    if grade not in (0, 1, 2, 3):
        raise ValueError("grade must be 0 (again), 1 (hard), 2 (good) or 3 (easy)")
    with db.session() as conn:
        r = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not r:
            raise KeyError(f"Card {card_id} not found")
        ease, interval, reps = sm2(r["ease"], r["interval_days"], r["reps"], grade)
        lapses = r["lapses"] + (1 if grade == 0 and r["reps"] > 0 else 0)
        due = _now() + (timedelta(minutes=1) if grade == 0 else timedelta(days=interval))
        conn.execute("UPDATE cards SET ease = ?, interval_days = ?, reps = ?, lapses = ?, due_at = ?, last_review_at = ? "
                     "WHERE id = ?", (ease, interval, reps, lapses, _iso(due), db.now(), card_id))
        conn.execute("INSERT INTO card_reviews(card_id, grade, reviewed_at) VALUES (?,?,?)", (card_id, grade, db.now()))
        out = dict(conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone())
    return out


def check_answer(card_id: int, answer: str) -> dict[str, Any]:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if not r:
        raise KeyError(f"Card {card_id} not found")
    answer = answer.strip()[:1500]
    if not answer:
        return {"correct": False, "feedback": "No answer given.", "suggested_grade": 0, "back": r["back"]}
    simple = re.sub(r"\W+", " ", answer.lower()).strip() == re.sub(r"\W+", " ", r["back"].lower()).strip()
    if simple:
        return {"correct": True, "feedback": "Exactly right.", "suggested_grade": 3, "back": r["back"]}
    model = llm.light_model(_maker_model())
    prompt = (f"Flashcard question: {r['front']}\nCorrect answer: {r['back']}\nThe learner answered: {answer}\n\n"
              "Is the learner's answer correct in meaning (ignore spelling and wording)? Give one short sentence of "
              "feedback (what was missing or wrong, or confirm it). suggested_grade: 0 wrong, 1 partly right, "
              "2 right, 3 right and complete.")
    try:
        data = llm.chat_json([{"role": "user", "content": prompt}], CHECK_SCHEMA, model=model, timeout=120)
        grade = max(0, min(int(data.get("suggested_grade", 0)), 3))
        return {"correct": bool(data.get("correct")), "feedback": str(data.get("feedback") or "")[:300],
                "suggested_grade": grade, "back": r["back"]}
    except Exception:  # noqa: BLE001 - grade yourself then
        return {"correct": False, "feedback": "Couldn't check it automatically; compare with the answer.",
                "suggested_grade": 2, "back": r["back"]}


def stats() -> dict[str, Any]:
    now = _now()
    with db.session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        due = conn.execute("SELECT COUNT(*) FROM cards WHERE due_at <= ?", (_iso(now),)).fetchone()[0]
        learned = conn.execute("SELECT COUNT(*) FROM cards WHERE interval_days >= 21").fetchone()[0]
        days = [r[0] for r in conn.execute("SELECT DISTINCT substr(reviewed_at, 1, 10) FROM card_reviews ORDER BY 1 DESC LIMIT 400")]
        week = conn.execute("SELECT grade, COUNT(*) FROM card_reviews WHERE reviewed_at >= ? GROUP BY grade",
                            (_iso(now - timedelta(days=7)),)).fetchall()
    streak, d = 0, now.date()
    have = set(days)
    if d.isoformat() not in have:
        d -= timedelta(days=1)  # today not done yet: the streak is still alive
    while d.isoformat() in have:
        streak += 1
        d -= timedelta(days=1)
    by = {g: n for g, n in week}
    reviewed = sum(by.values())
    return {"cards": total, "due": due, "learned": learned, "streak": streak, "reviews_week": reviewed,
            "accuracy_week": round(1 - by.get(0, 0) / reviewed, 3) if reviewed else None,
            "config": common.group("learn")}


def remind() -> int | None:
    """Once a day: a bell note when cards are due."""
    c = common.group("learn")
    today = date.today().isoformat()
    if not c["daily_reminder"] or c["last_reminder"] == today or datetime.now().hour < 9:
        return None
    n = len(queue(limit=500))
    common.save_group("learn", {"last_reminder": today})
    if n:
        common.notify(f"{n} flashcard{'s' if n != 1 else ''} to review", "Learn → Review (a few minutes keeps them fresh)",
                      kind="learn")
    return n


def export_deck(deck_id: int) -> str:
    """Tab-separated front/back (imports into Anki)."""
    d = get_deck(deck_id)
    return "\n".join(f"{c['front']}\t{c['back']}".replace("\n", "<br>") for c in d["card_list"])


def import_tsv(deck_id: int, text: str) -> int:
    cards = []
    for line in text.splitlines():
        if "\t" in line:
            f, _, b = line.partition("\t")
            cards.append({"front": f, "back": b.replace("<br>", "\n")})
    return add_cards(deck_id, _clean_cards(cards))

