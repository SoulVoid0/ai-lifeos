"""The multi-agent runtime: planning, delegation, parallel tasks, approvals, briefs, cancel, budgets."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_default_crew_is_seeded(client):
    agents = client.get("/api/crew/agents").json()
    slugs = [a["slug"] for a in agents]
    assert slugs[0] == "overseer"
    assert {"planner", "files", "research", "coder", "memory", "comms"} <= set(slugs)
    status = client.get("/api/crew/status").json()
    assert all(a["state"] == "idle" for a in status["agents"])


def test_offline_mission_runs_spec_workflow_with_approval(client, approved):
    goal = ("Find all PDFs in my Downloads folder, organize them by category, and remind me to review the "
            "important ones tomorrow")
    m = client.post("/api/missions", json={"goal": goal}).json()
    mid = m["id"]

    def waiting():
        cur = client.get(f"/api/missions/{mid}").json()
        return cur if cur["pending_actions"] and cur["status"] == "waiting" else None

    cur = wait_for(waiting)
    assert cur["status"] == "waiting"
    agents = [t["agent"] for t in cur["tasks"]]
    assert agents[0] == "files" and "planner" in agents
    action = cur["pending_actions"][0]
    assert action["tool"] == "organize_files" and action["mission_id"] == mid
    # The Archivist shows as waiting in the Control Center.
    files_agent = next(a for a in client.get("/api/crew/status").json()["agents"] if a["slug"] == "files")
    assert files_agent["state"] == "waiting"

    client.post(f"/api/actions/{action['id']}/approve", json={})
    done = wait_for(lambda: (r := client.get(f"/api/missions/{mid}").json())["status"] == "done" and r)
    assert all(t["status"] == "done" for t in done["tasks"])
    assert (approved["downloads"] / "Finance" / "invoice_march.pdf").exists()
    out = Path(done["outbox_path"])
    assert out.is_file() and "Chrono" in out.read_text()
    tasks = client.get("/api/tasks").json()
    assert any("review" in t["title"].lower() for t in tasks)
    kinds = {e["kind"] for e in client.get("/api/missions/events", params={"mission_id": mid}).json()}
    assert {"mission", "plan", "delegate", "tool", "approval", "decision", "result", "done"} <= kinds
    assert any(f["name"] == out.name for f in client.get("/api/outbox").json())
    report = client.get("/api/outbox/file", params={"name": out.name}).json()["text"]
    assert "Moved" in report and "Review the important files" in report


@pytest.fixture()
def fake_models(monkeypatch, client):
    """Script the Overseer and specialists. Plan calls and step calls are told apart by their schema."""
    from lifeos import llm

    script: dict[str, list] = {"plan": [], "steps": {}}
    seen: list[dict] = []
    lock = threading.Lock()

    def chat_json(messages, schema, **kw):
        with lock:
            seen.append({"messages": messages, "schema": schema, "model": kw.get("model")})
            if "tasks" in schema["properties"]:
                return llm.clean(script["plan"].pop(0))
            if "score" in schema["properties"]:
                reply = script.setdefault("reviews", []).pop(0) if script.get("reviews") else {"score": 4, "feedback": "Fine."}
                return reply
            if "lessons" in schema["properties"]:
                return script.setdefault("reflections", []).pop(0) if script.get("reflections") else {"lessons": []}
            system = messages[0]["content"]
            for name, replies in script["steps"].items():
                if system.startswith(f"You are {name},"):
                    reply = replies.pop(0)
                    return llm.clean(reply() if callable(reply) else reply)  # like the real chat_json
        raise AssertionError("unexpected model call")

    monkeypatch.setattr(llm, "model_available", lambda ref=None: True)
    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": ["llama3.1:8b"], "url": "x"})
    monkeypatch.setattr(llm, "chat_json", chat_json)
    monkeypatch.setattr(llm, "chat_text", lambda messages, **kw: "## Report\nAll done.")
    monkeypatch.setattr(llm, "embed", lambda texts: None)
    return {"script": script, "seen": seen}


def test_overseer_delegates_parallel_tasks_and_dependencies(client, fake_models):
    fake_models["script"]["plan"].append({"thinking": "Two independent lookups, then a plan.", "tasks": [
        {"id": "t1", "agent": "research", "title": "Find testing notes", "instructions": "Search for JavaScript testing",
         "depends_on": []},
        {"id": "t2", "agent": "planner", "title": "Check open tasks", "instructions": "List open tasks", "depends_on": []},
        {"id": "t3", "agent": "planner", "title": "Make a study task", "instructions": "Create a task", "depends_on": ["t1", "t2"]},
    ]})
    fake_models["script"]["steps"]["Scout"] = [
        {"thought": "search", "action": "tool", "tool": "search_files", "args": {"query": "javascript testing"}},
        {"thought": "done", "action": "finish", "answer": "Found js-testing-guide.pdf"},
    ]
    fake_models["script"]["steps"]["Chrono"] = [
        {"thought": "list", "action": "tool", "tool": "list_tasks", "args": {}},
        {"thought": "done", "action": "finish", "answer": "No open tasks."},
        {"thought": "create", "action": "tool", "tool": "create_task", "args": {"title": "Study JS testing", "due": "friday"}},
        {"thought": "done", "action": "finish", "answer": "Created Study JS testing."},
    ]
    client.patch("/api/settings", json={"max_parallel_agents": 1})  # keeps the scripted Chrono replies in order
    m = client.post("/api/missions", json={"goal": "Help me study JavaScript testing this week"}).json()
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] in ("done", "failed") and r)
    assert done["status"] == "done", done
    by_key = {t["key"]: t for t in done["tasks"]}
    assert by_key["t1"]["result"] == "Found js-testing-guide.pdf"
    assert by_key["t3"]["started_at"] >= max(by_key["t1"]["finished_at"], by_key["t2"]["finished_at"])
    # The dependent task was given its teammates' results.
    chrono_calls = [c for c in fake_models["seen"] if c["messages"][0]["content"].startswith("You are Chrono,")]
    assert any("Found js-testing-guide.pdf" in c["messages"][1]["content"] for c in chrono_calls)
    # Specialists only see their own tools in the step schema.
    scout_schema = next(c for c in fake_models["seen"] if c["messages"][0]["content"].startswith("You are Scout,"))["schema"]
    assert "organize_files" not in scout_schema["properties"]["tool"]["enum"]
    assert "Report" in done["result"]
    assert any(t["title"] == "Study JS testing" for t in client.get("/api/tasks").json())


def test_agent_cannot_use_tools_outside_its_grant(client, fake_models, approved):
    fake_models["script"]["plan"].append({"thinking": "", "tasks": [
        {"id": "t1", "agent": "memory", "title": "Tidy", "instructions": "Organize downloads", "depends_on": []}]})
    fake_models["script"]["steps"]["Mnemo"] = [
        {"thought": "try a file tool", "action": "tool", "tool": "organize_files", "args": {"folder": "Downloads"}},
        {"thought": "can't", "action": "finish", "answer": "That's not one of my tools."},
    ]
    m = client.post("/api/missions", json={"goal": "Organize downloads"}).json()
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] == "done" and r)
    assert done["pending_actions"] == []
    assert client.get("/api/actions", params={"status": "pending"}).json() == []
    assert (approved["downloads"] / "invoice_march.pdf").exists()


def test_task_brief_question_and_answer(client, fake_models):
    fake_models["script"]["plan"] += [
        {"thinking": "Ambiguous.", "tasks": [], "question": "Which project?", "options": ["Thesis", "Website"]},
        {"thinking": "Now clear.", "tasks": [], "reply": "Got it: Thesis."},
    ]
    m = client.post("/api/missions", json={"goal": "Continue my project"}).json()
    briefs = wait_for(lambda: client.get("/api/briefs").json())
    assert briefs[0]["question"] == "Which project?" and briefs[0]["options"] == ["Thesis", "Website"]
    assert client.get(f"/api/missions/{m['id']}").json()["status"] == "waiting"
    client.post(f"/api/briefs/{briefs[0]['id']}/answer", json={"answer": "Thesis"})
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] == "done" and r)
    assert done["result"].startswith("Got it")
    plan_calls = [c for c in fake_models["seen"] if "tasks" in c["schema"]["properties"]]
    assert "Thesis" in plan_calls[1]["messages"][-1]["content"]


def test_cancel_rejects_pending_approvals(client, approved):
    m = client.post("/api/missions", json={"goal": "Organize my Downloads folder"}).json()
    cur = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["pending_actions"] and r)
    aid = cur["pending_actions"][0]["id"]
    client.post(f"/api/missions/{m['id']}/cancel")
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] == "cancelled" and r)
    assert client.get("/api/actions").json()[0]["id"] == aid
    assert next(a for a in client.get("/api/actions").json() if a["id"] == aid)["status"] == "rejected"
    assert all(t["status"] in ("cancelled", "skipped", "done") for t in done["tasks"]), done["tasks"]
    assert (approved["downloads"] / "invoice_march.pdf").exists()


def test_token_budget_stops_agent(client, fake_models):
    from lifeos import db

    fake_models["script"]["plan"].append({"thinking": "", "tasks": [
        {"id": "t1", "agent": "research", "title": "Dig", "instructions": "Search", "depends_on": []}]})
    client.patch("/api/settings", json={"mission_token_budget": 1000})
    real = fake_models["script"]

    def heavy_step(*a, **kw):
        with db.session() as conn:
            conn.execute("UPDATE missions SET tokens_in = tokens_in + 5000")
        return {"thought": "search", "action": "tool", "tool": "search_everything", "args": {"query": "x"}}

    real["steps"]["Scout"] = [heavy_step, {"thought": "x", "action": "finish", "answer": "never"}]
    m = client.post("/api/missions", json={"goal": "Research everything"}).json()
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] in ("done", "failed") and r)
    assert done["tasks"][0]["status"] == "failed"
    assert "budget" in done["tasks"][0]["result"].lower()


def test_direct_agent_mission_and_stop_all(client, fake_models):
    fake_models["script"]["steps"]["Mnemo"] = [
        {"thought": "save", "action": "tool", "tool": "save_memory", "args": {"content": "I like tea", "kind": "preference"}},
        {"thought": "done", "action": "finish", "answer": "Saved."},
    ]
    m = client.post("/api/missions", json={"goal": "Remember I like tea", "agent": "memory"}).json()
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] == "done" and r)
    assert done["tasks"][0]["agent"] == "memory"
    assert any(x["content"] == "I like tea" for x in client.get("/api/memories").json())
    assert client.post("/api/missions/stop-all").json()["stopped"] == 0


def test_crew_management_rules(client):
    a = client.post("/api/crew/agents", json={"name": "Quill", "role": "Writer", "tools": ["save_to_outbox"],
                                             "model": "anthropic:claude-sonnet-4-5"}).json()
    assert a["slug"] == "quill" and a["tools"] == ["save_to_outbox"]
    assert client.post("/api/crew/agents", json={"name": "Bad", "tools": ["run_shell"]}).status_code == 400
    assert client.patch("/api/crew/agents/overseer", json={"enabled": False}).status_code == 400
    assert client.delete("/api/crew/agents/files").status_code == 400
    assert client.patch("/api/crew/agents/files", json={"enabled": False}).json()["enabled"] is False
    pack = client.get("/api/crew/pack").json()
    assert [x["slug"] for x in pack["agents"]] == ["quill"]
    client.delete("/api/crew/agents/quill")
    res = client.post("/api/crew/pack", json=pack).json()
    assert res["agents"] == ["quill"]
    client.post("/api/crew/reset")
    assert client.get("/api/crew/agents").json()[2]["enabled"] is True


def test_cloud_models_are_gated(client):
    from lifeos import llm, vault

    assert llm.parse_ref("llama3.1:8b") == ("ollama", "llama3.1:8b")
    assert llm.parse_ref("openai:gpt-4o-mini") == ("openai", "gpt-4o-mini")
    assert llm.parse_ref("openrouter:qwen/qwen3-32b") == ("openrouter", "qwen/qwen3-32b")
    with pytest.raises(llm.LLMUnavailable, match="turned off"):
        llm.chat_text([{"role": "user", "content": "hi"}], model="openai:gpt-4o-mini")
    assert not llm.model_available("openai:gpt-4o-mini")
    client.put("/api/providers/openai/key", json={"key": "sk-test"})
    assert vault.get_secret("provider:openai") == "sk-test"
    providers = client.get("/api/providers").json()
    assert next(p for p in providers["providers"] if p["id"] == "openai")["has_key"] is True
    assert "sk-test" not in str(providers)
    client.patch("/api/settings", json={"cloud_enabled": True, "cloud_daily_budget_usd": 0})
    with pytest.raises(llm.LLMUnavailable, match="budget"):
        llm.chat_text([{"role": "user", "content": "hi"}], model="openai:gpt-4o-mini")


def test_ledger_records_usage_and_cost(client):
    from lifeos import llm

    client.patch("/api/settings", json={"model_prices": {"openai:gpt-4o-mini": {"in": 1.0, "out": 2.0}}})
    with llm.track(None, "research"):
        llm._record("openai", "gpt-4o-mini", 1_000_000, 500_000)
    ledger = client.get("/api/ledger").json()
    assert ledger["today"]["cloud_cost_usd"] == pytest.approx(2.0)
    assert ledger["by_agent"][0]["agent"] == "research"


def test_small_model_mistakes_do_not_stop_the_task(client, fake_models, monkeypatch):
    """Nulls, "never" dates, tool names in the action field, unknown tools, unreadable JSON and a missing answer."""
    from lifeos import llm

    fake_models["script"]["plan"].append({"thinking": "null", "reply": "null", "question": "null", "tasks": [
        {"id": "t1", "agent": "Chrono", "title": "Plan study", "instructions": "Create a study task", "depends_on": "null"}]})

    def unreadable():
        raise llm.BadOutput("Model returned invalid JSON: I think I should")

    fake_models["script"]["steps"]["Chrono"] = [
        unreadable,
        {"thought": "null", "action": "create_task",
         "args": {"title": "Study JS testing", "due": "never", "remind": "null", "priority": "Medium",
                  "estimate_minutes": "90", "project": None, "invented": "x"}},
        {"thought": "Use a tool that doesn't exist", "action": "tool", "tool": "send_email", "args": {}},
        {"thought": "Bad args", "action": "tool", "tool": "complete_task", "args": "not json"},
        {"thought": "null", "action": "finish", "answer": "null"},
        {"thought": "Done", "action": "finish", "answer": "Created the study task with no due date."},
    ]
    m = client.post("/api/missions", json={"goal": "Plan my study time"}).json()
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] in ("done", "failed") and r)
    assert done["status"] == "done", done
    task = done["tasks"][0]
    assert task["agent"] == "planner" and task["status"] == "done"
    assert task["result"] == "Created the study task with no due date."
    created = next(t for t in client.get("/api/tasks").json() if t["title"] == "Study JS testing")
    assert created["due_at"] is None and created["estimate_minutes"] == 90
    assert not done["briefs"], "a null question must not become a brief"
    assert "null" not in (done["result"] or "").lower().split()
    # The model was told what went wrong each time.
    chrono = [c for c in fake_models["seen"] if c["messages"][0]["content"].startswith("You are Chrono,")]
    hints = " ".join(m["content"] for m in chrono[-1]["messages"] if m["role"] == "user")
    assert "could not be read" in hints and "no tool called 'send_email'" in hints and "gave no answer" in hints


def test_task_wraps_up_in_plain_text_when_the_model_keeps_failing(client, fake_models, monkeypatch):
    from lifeos import llm

    client.patch("/api/settings", json={"peer_review": False})
    monkeypatch.setattr(llm, "chat_text", lambda messages, **kw: "I found js-testing-guide.pdf; nothing else to do.")
    fake_models["script"]["plan"].append({"thinking": "", "tasks": [
        {"id": "t1", "agent": "research", "title": "Find notes", "instructions": "Search", "depends_on": []}]})
    fake_models["script"]["steps"]["Scout"] = [
        {"thought": "search", "action": "tool", "tool": "search_files", "args": {"query": "javascript"}}] + \
        [{"thought": "hmm", "action": "dance"}] * 6
    m = client.post("/api/missions", json={"goal": "Find my notes"}).json()
    done = wait_for(lambda: (r := client.get(f"/api/missions/{m['id']}").json())["status"] in ("done", "failed") and r)
    assert done["status"] == "done"
    assert done["tasks"][0]["result"] == "I found js-testing-guide.pdf; nothing else to do."
    kinds = [e["kind"] for e in client.get("/api/missions/events", params={"mission_id": m["id"]}).json()]
    assert "limit" in kinds


def test_missing_local_model_falls_back_to_default(client, monkeypatch):
    from lifeos import llm

    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": ["llama3.1:8b"], "url": "x"})
    forge = next(a for a in client.get("/api/crew/agents").json() if a["slug"] == "coder")
    assert forge["model"] == "qwen3-coder:30b"
    assert forge["effective_model"] == "llama3.1:8b" and forge["model_missing"] is True
    assert any(r["model"] == "qwen3-coder:30b" for r in forge["recommended"])
    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": ["llama3.1:8b", "qwen3-coder:30b"], "url": "x"})
    forge = next(a for a in client.get("/api/crew/agents").json() if a["slug"] == "coder")
    assert forge["effective_model"] == "qwen3-coder:30b" and not forge["model_missing"]
    assert llm.installed("llama3.1", ["llama3.1:latest"]) and not llm.installed("qwen3:8b", ["qwen3:14b"])


def test_existing_installs_get_new_default_models_once(client):
    from lifeos import db

    with db.session() as conn:
        conn.execute("UPDATE agents SET model = '' WHERE slug = 'coder'")
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'crew_defaults_version'")
    assert next(a for a in client.get("/api/crew/agents").json() if a["slug"] == "coder")["model"] == "qwen3-coder:30b"
    client.patch("/api/crew/agents/coder", json={"model": ""})  # the user's later choice sticks
    assert next(a for a in client.get("/api/crew/agents").json() if a["slug"] == "coder")["model"] == ""
