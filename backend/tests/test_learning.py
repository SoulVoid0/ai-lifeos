"""How the crew learns: peer review and revision, lessons, ratings, scorecard, auto-routing, training export."""
from __future__ import annotations

import json

from test_crew import fake_models, wait_for  # noqa: F401  (fixture re-export)


def _one_task_plan(agent="research", title="Find testing notes"):
    return {"thinking": "", "tasks": [{"id": "t1", "agent": agent, "title": title,
                                       "instructions": "Search my files for JavaScript testing", "depends_on": []}]}


def _run(client, goal):
    m = client.post("/api/missions", json={"goal": goal}).json()
    return wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] in ("done", "failed") and r)


def test_low_review_triggers_revision_and_becomes_a_lesson(client, fake_models):
    script = fake_models["script"]
    script["plan"] += [_one_task_plan(), _one_task_plan(title="Find more testing notes")]
    script["steps"]["Scout"] = [
        {"thought": "guess", "action": "finish", "answer": "Probably somewhere in Downloads."},
        {"thought": "search properly", "action": "tool", "tool": "search_files", "args": {"query": "javascript testing"}},
        {"thought": "done", "action": "finish", "answer": "Found js-testing-guide.pdf in Downloads."},
        {"thought": "done", "action": "finish", "answer": "Found js-testing-guide.pdf again."},
    ]
    script["reviews"] = [
        {"score": 2, "feedback": "No file was named; search before answering.",
         "lesson": "Always run search_files before saying where a file is."},
        {"score": 5, "feedback": "Specific and sourced."},
        {"score": 4, "feedback": "Good."},
    ]
    script["reflections"] = [{"lessons": [{"agent": "*", "text": "Name the exact file path in every answer about files."},
                                          {"agent": "nobody", "text": "Ignored because the agent doesn't exist."}]}]
    done = _run(client, "Where are my JavaScript testing notes?")
    assert done["status"] == "done", done
    task = done["tasks"][0]
    assert task["result"] == "Found js-testing-guide.pdf in Downloads."
    assert task["score"] == 5 and task["model"] == "llama3.1:8b" and task["steps"] == 3
    kinds = [e["kind"] for e in client.get("/api/missions/events", params={"mission_id": done["id"]}).json()]
    assert "revise" in kinds and kinds.count("review") >= 2 and "learn" in kinds

    lessons = client.get("/api/learning/lessons").json()
    texts = {(l["agent"], l["source"]): l["text"] for l in lessons}
    assert texts[("research", "review")].startswith("Always run search_files")
    assert ("*", "reflection") in texts and not any(l["agent"] == "nobody" for l in lessons)

    # The next mission's Scout prompt carries the playbook.
    _run(client, "Find more JavaScript testing notes")
    scout_prompts = [c["messages"][0]["content"] for c in fake_models["seen"]
                     if c["messages"][0]["content"].startswith("You are Scout,")]
    assert "Always run search_files" in scout_prompts[-1] and "exact file path" in scout_prompts[-1]

    card = next(a for a in client.get("/api/learning/scorecard").json() if a["slug"] == "research")
    assert card["tasks"] == 2 and card["avg_score"] == 4.5 and card["models"][0]["model"] == "llama3.1:8b"

    exported = [json.loads(line) for line in client.get("/api/learning/export.jsonl").text.splitlines()]
    assert len(exported) == 2 and exported[0]["metadata"]["agent"] == "research"
    assert exported[0]["messages"][0]["role"] == "system" and "finish" in exported[0]["messages"][-1]["content"]
    assert client.get("/api/learning").json()["lessons"] == len(lessons)


def test_peer_review_can_be_turned_off(client, fake_models):
    client.patch("/api/settings", json={"peer_review": False, "reflection": False})
    fake_models["script"]["plan"].append(_one_task_plan())
    fake_models["script"]["steps"]["Scout"] = [{"thought": "x", "action": "finish", "answer": "Nothing found."}]
    done = _run(client, "Find testing notes")
    assert done["tasks"][0]["score"] == 3.0 and not done["tasks"][0]["review"]
    assert not any("score" in c["schema"]["properties"] for c in fake_models["seen"])


def test_rejected_approval_teaches_offline(client, approved):
    m = client.post("/api/missions", json={"goal": "Organize my Downloads folder"}).json()
    cur = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["pending_actions"] and r)
    client.post(f"/api/actions/{cur['pending_actions'][0]['id']}/reject")
    wait_for(lambda: client.get(f"/api/missions/{m['id']}").json()["status"] in ("done", "failed"))
    lessons = client.get("/api/learning/lessons", params={"agent": "files"}).json()
    assert lessons and lessons[0]["source"] == "rejection" and "organize files" in lessons[0]["text"]
    task = client.get(f"/api/missions/{m['id']}").json()["tasks"][0]
    assert task["model"] == "rules" and task["score"] == 1.0


def test_rating_and_lesson_crud(client, fake_models):
    fake_models["script"]["plan"].append(_one_task_plan())
    fake_models["script"]["steps"]["Scout"] = [{"thought": "x", "action": "finish", "answer": "Found it."}]
    task = _run(client, "Find testing notes")["tasks"][0]
    r = client.post(f"/api/missions/tasks/{task['id']}/rate", json={"rating": -1, "note": "Give me the full path next time"})
    assert r.json()["lesson"]["source"] == "user"
    card = next(a for a in client.get("/api/learning/scorecard").json() if a["slug"] == "research")
    assert card["thumbs_down"] == 1 and card["avg_score"] == 1.0
    assert client.post(f"/api/missions/tasks/{task['id']}/rate", json={"rating": 3}).status_code == 400
    assert client.get("/api/learning/export.jsonl").text == ""

    added = client.post("/api/learning/lessons", json={"agent": "planner", "text": "Never schedule work after 22:00"}).json()
    dup = client.post("/api/learning/lessons", json={"agent": "planner", "text": "never schedule work after 22:00!"})
    assert dup.status_code == 400
    assert client.patch(f"/api/learning/lessons/{added['id']}", json={"pinned": True}).json()["pinned"] is True
    assert client.post("/api/learning/lessons", json={"agent": "ghost", "text": "Something long enough"}).status_code == 404
    client.delete(f"/api/learning/lessons/{added['id']}")
    assert not client.get("/api/learning/lessons", params={"agent": "planner"}).json()


def test_auto_route_tries_each_candidate_then_prefers_the_best(client, fake_models):
    from lifeos import learning

    client.patch("/api/crew/agents/research", json={"candidates": ["qwen2.5:7b"]})
    client.patch("/api/settings", json={"auto_route": True})
    agent = client.get("/api/crew/agents").json()
    research = next(a for a in agent if a["slug"] == "research")
    assert research["candidates"] == ["qwen2.5:7b"]
    picks = []
    for i in range(6):
        fake_models["script"]["plan"].append(_one_task_plan())
        fake_models["script"]["steps"]["Scout"] = [{"thought": "x", "action": "finish", "answer": f"Answer {i}"}]
        fake_models["script"]["reviews"] = [{"score": 5, "feedback": "ok"}]
        done = _run(client, f"Find testing notes {i}")
        picks.append(done["tasks"][0]["model"])
        if done["tasks"][0]["model"] == "llama3.1:8b":  # make the default model look bad
            client.post(f"/api/missions/tasks/{done['tasks'][0]['id']}/rate", json={"rating": -1})
    assert picks.count("qwen2.5:7b") == 3 and picks.count("llama3.1:8b") == 3
    model, reason = learning.choose_model(next(a for a in client.get("/api/crew/agents").json() if a["slug"] == "research"))
    assert model == "qwen2.5:7b" or reason.startswith("exploring")


def test_talk_to_an_agent(client, fake_models, monkeypatch):
    from lifeos import llm

    seen = {}

    def chat_text(messages, **kw):
        seen["messages"] = messages
        return "Hi, I'm Chrono. Press Give task and I'll plan your week."

    monkeypatch.setattr(llm, "chat_text", chat_text)
    r = client.post("/api/crew/agents/planner/chat", json={"message": "Can you plan my week?",
                                                            "history": [{"role": "assistant", "content": "Hello!"}]}).json()
    assert "Chrono" in r["reply"] and r["model"] == "llama3.1:8b"
    assert seen["messages"][0]["content"].startswith("You are Chrono,") and seen["messages"][1]["content"] == "Hello!"

    monkeypatch.setattr(llm, "model_available", lambda ref=None: False)
    r = client.post("/api/crew/agents/files/chat", json={"message": "tidy downloads"}).json()
    assert r["model"] is None and "Archivist" in r["reply"] and "Give task" in r["reply"]
    assert client.post("/api/crew/agents/ghost/chat", json={"message": "hi"}).status_code == 404
