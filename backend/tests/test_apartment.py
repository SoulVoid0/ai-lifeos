"""The 3D apartment's points, shop and layout."""
from __future__ import annotations


def _earn(n_calls: int = 0, tokens: int = 0, tasks: int = 0):
    from lifeos import db

    with db.session() as conn:
        for _ in range(n_calls):
            conn.execute("INSERT INTO ledger(agent, model, provider, tokens_in, tokens_out, created_at) VALUES (?,?,?,?,?,?)",
                         ("overseer", "m", "ollama", tokens // max(n_calls, 1), 0, db.now()))
        if tasks:
            cur = conn.execute("INSERT INTO missions(goal, status, lead, created_at) VALUES ('g','done','overseer',?)", (db.now(),))
            for i in range(tasks):
                conn.execute("INSERT INTO mission_tasks(mission_id, key, agent, title, instructions, status) "
                             "VALUES (?,?,?,?,?, 'done')", (cur.lastrowid, f"t{i}", "overseer", "t", "i"))


def test_points_come_from_using_the_crew(client):
    st = client.get("/api/apartment").json()
    assert st["points"]["balance"] == 0
    assert any(c["id"] == "paint:cream" and c["owned"] for c in st["catalog"])
    _earn(n_calls=5, tokens=5000, tasks=2)
    p = client.get("/api/apartment").json()["points"]
    # 5 calls + 5 kilotokens + 2 tasks * 10 + 1 mission * 20
    assert p["earned"] == 5 + 5 + 20 + 20
    assert p["balance"] == p["earned"]


def test_buying_spends_points_and_applies_the_item(client):
    r = client.post("/api/apartment/buy", json={"item": "decor:aquarium"})
    assert r.status_code == 400 and "costs 200" in r.json()["detail"]
    _earn(n_calls=100, tokens=200000, tasks=5)
    st = client.post("/api/apartment/buy", json={"item": "decor:aquarium"}).json()
    assert st["points"]["spent"] == 200
    assert "decor:aquarium" in st["layout"]["decor"]
    assert client.post("/api/apartment/buy", json={"item": "decor:aquarium"}).status_code == 400
    assert client.post("/api/apartment/buy", json={"item": "nope"}).status_code == 404


def test_layout_only_accepts_owned_items(client):
    bad = client.patch("/api/apartment/layout", json={"walls": {"living": "paint:navy"}})
    assert bad.status_code == 400
    ok = client.patch("/api/apartment/layout", json={"walls": {"living": "paint:white"}, "floors": {"hall": "floor:tile"}})
    assert ok.status_code == 200
    lay = ok.json()
    assert lay["walls"]["living"] == "paint:white" and lay["floors"]["hall"] == "floor:tile"
    assert client.patch("/api/apartment/layout", json={"walls": {"garage": "paint:white"}}).status_code == 400
    assert client.get("/api/apartment").json()["layout"]["walls"]["living"] == "paint:white"
