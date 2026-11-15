"""The 3D apartment: LifeOS's desktop on the LifeOS operating system.

The apartment program (apartment/ in the repo, `lifeos-apartment` on the OS) draws the rooms and the crew; this
module keeps what belongs to the user: points earned by using the crew, the shop catalog, what was bought and how
the apartment is decorated.

Points are worked out from what actually happened (nothing to grind or fake): every AI call, every thousand
tokens, every message you send, every finished task and mission. Spending is the price of what you own, so the
balance is always earned minus owned.
"""
from __future__ import annotations

from typing import Any

from . import common, db

ROOMS = {"living": "Living room", "master": "Master bedroom", "kitchen": "Kitchen", "bath": "Bathroom",
         "ai1": "AI bedroom 1", "ai2": "AI bedroom 2", "work": "Work room", "hall": "Hallway"}

# What a point is worth. Shown in the shop so the user knows how to earn more.
EARN = {"call": 1, "kilotoken": 1, "message": 2, "task": 10, "mission": 20}

# kind: paint (wall color), floor, couch (couch color), decor (an object in a fixed place), extra (a feature).
# rgb is used by the apartment for paint, floor and couch items. Price 0 = everyone has it.
CATALOG: list[dict[str, Any]] = [
    {"id": "paint:cream", "kind": "paint", "name": "Cream paint", "cost": 0, "rgb": [232, 224, 206]},
    {"id": "paint:white", "kind": "paint", "name": "White paint", "cost": 0, "rgb": [236, 236, 232]},
    {"id": "paint:stone", "kind": "paint", "name": "Stone gray paint", "cost": 0, "rgb": [176, 176, 170]},
    {"id": "paint:sage", "kind": "paint", "name": "Sage green paint", "cost": 60, "rgb": [170, 190, 160]},
    {"id": "paint:sky", "kind": "paint", "name": "Sky blue paint", "cost": 60, "rgb": [160, 196, 224]},
    {"id": "paint:blush", "kind": "paint", "name": "Blush pink paint", "cost": 60, "rgb": [232, 186, 186]},
    {"id": "paint:lavender", "kind": "paint", "name": "Lavender paint", "cost": 60, "rgb": [196, 182, 226]},
    {"id": "paint:sand", "kind": "paint", "name": "Desert sand paint", "cost": 60, "rgb": [218, 196, 158]},
    {"id": "paint:mint", "kind": "paint", "name": "Mint paint", "cost": 80, "rgb": [176, 226, 204]},
    {"id": "paint:terracotta", "kind": "paint", "name": "Terracotta paint", "cost": 100, "rgb": [196, 120, 92]},
    {"id": "paint:navy", "kind": "paint", "name": "Midnight navy paint", "cost": 120, "rgb": [52, 64, 104]},
    {"id": "paint:forest", "kind": "paint", "name": "Forest green paint", "cost": 120, "rgb": [58, 96, 70]},
    {"id": "paint:charcoal", "kind": "paint", "name": "Charcoal paint", "cost": 120, "rgb": [62, 64, 70]},
    {"id": "paint:sunset", "kind": "paint", "name": "Sunset orange paint", "cost": 150, "rgb": [236, 150, 90]},
    {"id": "floor:oak", "kind": "floor", "name": "Oak floor", "cost": 0, "rgb": [176, 132, 88]},
    {"id": "floor:tile", "kind": "floor", "name": "White tile", "cost": 0, "rgb": [214, 216, 218]},
    {"id": "floor:carpet_gray", "kind": "floor", "name": "Gray carpet", "cost": 0, "rgb": [128, 130, 136]},
    {"id": "floor:walnut", "kind": "floor", "name": "Walnut floor", "cost": 80, "rgb": [110, 76, 50]},
    {"id": "floor:marble", "kind": "floor", "name": "Marble floor", "cost": 150, "rgb": [230, 228, 222]},
    {"id": "floor:carpet_blue", "kind": "floor", "name": "Blue carpet", "cost": 80, "rgb": [70, 96, 150]},
    {"id": "floor:carpet_red", "kind": "floor", "name": "Red carpet", "cost": 80, "rgb": [150, 58, 58]},
    {"id": "floor:concrete", "kind": "floor", "name": "Polished concrete", "cost": 100, "rgb": [150, 150, 146]},
    {"id": "floor:checker", "kind": "floor", "name": "Checkerboard tile", "cost": 120, "rgb": [40, 40, 44]},
    {"id": "couch:slate", "kind": "couch", "name": "Slate couch", "cost": 0, "rgb": [84, 92, 108]},
    {"id": "couch:red", "kind": "couch", "name": "Red couch", "cost": 80, "rgb": [170, 56, 56]},
    {"id": "couch:green", "kind": "couch", "name": "Velvet green couch", "cost": 80, "rgb": [48, 110, 76]},
    {"id": "couch:mustard", "kind": "couch", "name": "Mustard couch", "cost": 80, "rgb": [204, 160, 48]},
    {"id": "couch:white", "kind": "couch", "name": "White leather couch", "cost": 120, "rgb": [226, 224, 216]},
    {"id": "decor:plants", "kind": "decor", "name": "Big plants", "cost": 50, "desc": "Potted plants around the apartment"},
    {"id": "decor:rug", "kind": "decor", "name": "Living room rug", "cost": 40, "desc": "A big rug under the coffee table"},
    {"id": "decor:bookshelf", "kind": "decor", "name": "Bookshelf", "cost": 70, "desc": "A full bookshelf in the living room"},
    {"id": "decor:posters", "kind": "decor", "name": "Posters", "cost": 60, "desc": "Framed posters in the hallway and bedroom"},
    {"id": "decor:lamps", "kind": "decor", "name": "Floor lamps", "cost": 60, "desc": "Warm floor lamps"},
    {"id": "decor:aquarium", "kind": "decor", "name": "Aquarium", "cost": 200, "desc": "A fish tank with swimming fish"},
    {"id": "decor:arcade", "kind": "decor", "name": "Arcade cabinet", "cost": 250, "desc": "A glowing arcade machine"},
    {"id": "decor:neon", "kind": "decor", "name": "Neon sign", "cost": 150, "desc": "A neon sign over the couch"},
    {"id": "decor:fireplace", "kind": "decor", "name": "Fireplace", "cost": 300, "desc": "A flickering fireplace"},
    {"id": "decor:piano", "kind": "decor", "name": "Piano", "cost": 350, "desc": "An upright piano"},
    {"id": "decor:coffee", "kind": "decor", "name": "Coffee machine", "cost": 60, "desc": "The crew grabs coffee from it"},
    {"id": "decor:discoball", "kind": "decor", "name": "Disco ball", "cost": 180, "desc": "A spinning disco ball in the work room"},
    {"id": "decor:cat", "kind": "decor", "name": "Cat", "cost": 400, "desc": "A cat that wanders the apartment"},
    {"id": "decor:stringlights", "kind": "decor", "name": "String lights", "cost": 90, "desc": "Fairy lights in the AI bedrooms"},
    {"id": "decor:gaming", "kind": "decor", "name": "Gaming chair + RGB desk", "cost": 220, "desc": "Upgrades your PC corner"},
    {"id": "extra:bigtv", "kind": "extra", "name": "Big TV", "cost": 250, "desc": "A much bigger TV in the living room"},
    {"id": "extra:hats", "kind": "extra", "name": "Crew hats", "cost": 150, "desc": "Every agent wears a hat"},
    {"id": "extra:skylight", "kind": "extra", "name": "Skylights", "cost": 200, "desc": "Windows in the ceiling that follow the sky"},
]
BY_ID = {c["id"]: c for c in CATALOG}
FREE = [c["id"] for c in CATALOG if c["cost"] == 0]

DEFAULT_LAYOUT: dict[str, Any] = {
    "walls": {"living": "paint:cream", "master": "paint:white", "kitchen": "paint:white", "bath": "paint:white",
              "ai1": "paint:stone", "ai2": "paint:stone", "work": "paint:white", "hall": "paint:cream"},
    "floors": {"living": "floor:oak", "master": "floor:carpet_gray", "kitchen": "floor:tile", "bath": "floor:tile",
               "ai1": "floor:carpet_gray", "ai2": "floor:carpet_gray", "work": "floor:oak", "hall": "floor:oak"},
    "couch": "couch:slate",
    "decor": [],
    "extras": [],
}


def _cfg() -> dict[str, Any]:
    return common.group("apartment")


def owned() -> list[str]:
    have = [i for i in _cfg()["owned"] if i in BY_ID]
    return FREE + [i for i in have if i not in FREE]


def points() -> dict[str, Any]:
    with db.session() as conn:
        calls, tokens = conn.execute("SELECT COUNT(*), COALESCE(SUM(tokens_in + tokens_out), 0) FROM ledger").fetchone()
        messages = conn.execute("SELECT COUNT(*) FROM messages WHERE role = 'user'").fetchone()[0]
        tasks = conn.execute("SELECT COUNT(*) FROM mission_tasks WHERE status = 'done'").fetchone()[0]
        missions = conn.execute("SELECT COUNT(*) FROM missions WHERE status = 'done'").fetchone()[0]
    parts = {"call": int(calls), "kilotoken": int(tokens) // 1000, "message": int(messages), "task": int(tasks),
             "mission": int(missions)}
    earned = sum(EARN[k] * n for k, n in parts.items()) + int(_cfg()["bonus"])
    spent = sum(BY_ID[i]["cost"] for i in owned())
    return {"earned": earned, "spent": spent, "balance": earned - spent, "counts": parts, "rates": EARN}


def layout() -> dict[str, Any]:
    stored = _cfg()["layout"] or {}
    have = set(owned())
    out: dict[str, Any] = {"walls": dict(DEFAULT_LAYOUT["walls"]), "floors": dict(DEFAULT_LAYOUT["floors"]),
                           "couch": DEFAULT_LAYOUT["couch"], "decor": [], "extras": []}
    for group, kind in (("walls", "paint"), ("floors", "floor")):
        for room, item in (stored.get(group) or {}).items():
            if room in ROOMS and item in have and BY_ID[item]["kind"] == kind:
                out[group][room] = item
    if stored.get("couch") in have and BY_ID[stored["couch"]]["kind"] == "couch":
        out["couch"] = stored["couch"]
    for group, kind in (("decor", "decor"), ("extras", "extra")):
        out[group] = [i for i in (stored.get(group) or []) if i in have and BY_ID[i]["kind"] == kind]
    return out


def state() -> dict[str, Any]:
    have = set(owned())
    return {"points": points(), "catalog": [{**c, "owned": c["id"] in have} for c in CATALOG], "layout": layout(),
            "rooms": ROOMS}


def buy(item: str) -> dict[str, Any]:
    if item not in BY_ID:
        raise KeyError(f"No item '{item}' in the shop")
    if item in owned():
        raise ValueError(f"You already own {BY_ID[item]['name']}")
    p = points()
    cost = BY_ID[item]["cost"]
    if p["balance"] < cost:
        raise ValueError(f"{BY_ID[item]['name']} costs {cost} points; you have {p['balance']}. "
                         "Use the crew more to earn points.")
    cfg = _cfg()
    common.save_group("apartment", {"owned": cfg["owned"] + [item]})
    # Something bought is something you want to see: put decor and extras straight in, apply colors to the
    # living room unless it's a couch.
    kind = BY_ID[item]["kind"]
    lay = layout()
    if kind == "decor":
        lay["decor"].append(item)
    elif kind == "extra":
        lay["extras"].append(item)
    elif kind == "couch":
        lay["couch"] = item
    save_layout(lay)
    return state()


def save_layout(changes: dict[str, Any]) -> dict[str, Any]:
    have = set(owned())
    lay = layout()
    for group, kind in (("walls", "paint"), ("floors", "floor")):
        for room, item in (changes.get(group) or {}).items():
            if room not in ROOMS:
                raise ValueError(f"Unknown room '{room}'")
            if item not in have or BY_ID[item]["kind"] != kind:
                raise ValueError(f"You don't own '{item}'")
            lay[group][room] = item
    if "couch" in changes:
        item = changes["couch"]
        if item not in have or BY_ID[item]["kind"] != "couch":
            raise ValueError(f"You don't own '{item}'")
        lay["couch"] = item
    for group, kind in (("decor", "decor"), ("extras", "extra")):
        if group in changes:
            items = list(dict.fromkeys(changes[group] or []))
            bad = [i for i in items if i not in have or BY_ID[i]["kind"] != kind]
            if bad:
                raise ValueError(f"You don't own {', '.join(bad)}")
            lay[group] = items
    common.save_group("apartment", {"layout": lay})
    return layout()

