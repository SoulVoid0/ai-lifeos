"""The MVP scenario from the spec, end to end through the HTTP API."""
from __future__ import annotations

from datetime import datetime, timedelta

PROMPT = ("Find all PDFs in my Downloads folder, organize them by category, "
          "and remind me to review the important ones tomorrow.")


def test_mvp_example(client, approved):
    downloads = approved["downloads"]
    r = client.post("/api/chat", json={"message": PROMPT})
    assert r.status_code == 200, r.text
    conversation_id = r.json()["conversation_id"]
    msg = r.json()["message"]
    steps = msg["data"]["steps"]
    assert [s["tool"] for s in steps] == ["search_files", "organize_files", "create_task"]

    # 1. located the PDFs in the authorized folder
    found = {f["name"] for f in steps[0]["result"]["files"]}
    assert found == {"invoice_march.pdf", "js-testing-guide.pdf", "lecture5.pdf", "scan001.pdf"}

    # 2-4. categorized and proposed a plan, nothing moved yet
    organize = steps[1]
    assert organize["status"] == "pending"
    moves = {m["src"].rsplit("/", 1)[-1]: m["category"] for m in organize["action"]["preview"]["moves"]}
    assert moves == {"invoice_march.pdf": "Finance", "js-testing-guide.pdf": "Programming",
                     "lecture5.pdf": "Study", "scan001.pdf": "Documents"}
    assert (downloads / "invoice_march.pdf").exists()
    assert "Waiting for your approval" in msg["content"]

    # 7. reminder for tomorrow listing the important files
    task = steps[2]["result"]["task"]
    assert task["title"] == "Review the important files"
    remind = datetime.fromisoformat(task["remind_at"]).astimezone()
    assert remind.date() == (datetime.now().astimezone() + timedelta(days=1)).date()
    assert "invoice_march.pdf" in task["notes"] and "lecture5.pdf" in task["notes"]
    assert "scan001.pdf" not in task["notes"]

    # 5-6. approve (excluding one file) and the files move
    aid = organize["action"]["id"]
    r = client.post(f"/api/actions/{aid}/approve", json={"exclude": [str(downloads / "scan001.pdf")]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "executed"
    assert (downloads / "Finance" / "invoice_march.pdf").exists()
    assert (downloads / "Programming" / "js-testing-guide.pdf").exists()
    assert (downloads / "Study" / "lecture5.pdf").exists()
    assert (downloads / "scan001.pdf").exists()
    assert (downloads / "holiday.jpg").exists()  # not a PDF, untouched

    # cannot run twice
    assert client.post(f"/api/actions/{aid}/approve").status_code == 400

    # 8. everything is in the activity log
    log = client.get("/api/activity").json()
    kinds = [e["kind"] for e in log]
    assert "proposal" in kinds and "action" in kinds
    assert any("Approved and ran" in e["summary"] for e in log)

    # reversible
    r = client.post(f"/api/actions/{aid}/undo")
    assert r.status_code == 200, r.text
    assert (downloads / "invoice_march.pdf").exists()
    assert not (downloads / "Finance").exists()

    # conversation persisted
    conv = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [m["role"] for m in conv] == ["user", "assistant"]


def test_reject_leaves_files(client, approved):
    r = client.post("/api/chat", json={"message": "Organize my Downloads folder"}).json()
    action = r["message"]["data"]["steps"][0]["action"]
    assert client.post(f"/api/actions/{action['id']}/reject").json()["status"] == "rejected"
    assert (approved["downloads"] / "invoice_march.pdf").exists()
    assert client.post(f"/api/actions/{action['id']}/approve").status_code == 400


def test_new_plan_supersedes_stale_one(client):
    first = client.post("/api/chat", json={"message": "Organize my Downloads folder"}).json()
    second = client.post("/api/chat", json={"message": "Organize my Downloads folder"}).json()
    a1 = first["message"]["data"]["steps"][0]["action"]["id"]
    a2 = second["message"]["data"]["steps"][0]["action"]["id"]
    pending = [a["id"] for a in client.get("/api/actions", params={"status": "pending"}).json()]
    assert pending == [a2]
    assert client.post(f"/api/actions/{a1}/approve").status_code == 400


def test_dashboard(client):
    client.post("/api/tasks", json={"title": "Submit essay", "due_at": "tomorrow 5pm", "priority": "high"})
    d = client.get("/api/dashboard").json()
    assert d["counts"]["open_tasks"] == 1
    assert d["priorities"][0]["title"] == "Submit essay"
    assert any(s["id"] == "setup-ollama" for s in d["suggestions"])
    assert "Summary for" in d["summary"]["text"]
