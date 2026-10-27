"""Permission boundaries: the assistant only reaches what the user approved."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_api_requires_client_header(approved):
    from fastapi.testclient import TestClient

    from lifeos.main import app

    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/tasks").status_code == 403
        assert c.post("/api/chat", json={"message": "hi"}).status_code == 403


def test_cannot_approve_root_or_home(env):
    from lifeos import permissions

    with pytest.raises(permissions.PermissionDenied):
        permissions.add_folder("/")
    with pytest.raises(permissions.PermissionDenied):
        permissions.add_folder(str(Path.home()))


def test_paths_outside_approved_folders_denied(approved):
    from lifeos import permissions

    outside = approved["home"] / "secret.txt"
    outside.write_text("top secret")
    with pytest.raises(permissions.PermissionDenied):
        permissions.resolve_allowed(outside)
    with pytest.raises(permissions.PermissionDenied):
        permissions.resolve_allowed(approved["downloads"] / ".." / "secret.txt")


def test_symlink_escape_denied(approved):
    from lifeos import permissions

    outside = approved["home"] / "private"
    outside.mkdir()
    (outside / "keys.txt").write_text("k")
    link = approved["downloads"] / "link"
    os.symlink(outside, link)
    with pytest.raises(permissions.PermissionDenied):
        permissions.resolve_allowed(link / "keys.txt")


def test_tool_calls_outside_scope_are_refused(approved):
    from lifeos import tools

    secret = approved["home"] / "secret.txt"
    secret.write_text("x")
    out = tools.invoke("read_document", {"path": str(secret)}, {})
    assert out["status"] == "error" and "outside" in out["summary"]
    out = tools.invoke("move_file", {"path": str(approved["downloads"] / "notes.txt"),
                                     "destination_folder": str(approved["home"])}, {})
    assert out["status"] == "error"
    assert (approved["downloads"] / "notes.txt").exists()


def test_unknown_tool_and_bad_args_rejected(approved):
    from lifeos import tools

    assert tools.invoke("run_shell", {"cmd": "rm -rf /"}, {})["status"] == "error"
    assert tools.invoke("find_large_files", {"min_mb": -5}, {})["status"] == "error"


def test_policy_deny_and_pause(client, approved):
    client.patch("/api/settings", json={"tool_policies": {"create_task": "deny"}})
    r = client.post("/api/chat", json={"message": "remind me to call mom tomorrow"}).json()
    assert r["message"]["data"]["steps"][0]["status"] == "blocked"
    assert client.get("/api/tasks").json() == []

    client.patch("/api/settings", json={"tool_policies": {}})
    client.post("/api/settings/pause", json={"paused": True})
    r = client.post("/api/chat", json={"message": "remind me to call mom tomorrow"}).json()
    step = r["message"]["data"]["steps"][0]
    assert step["status"] == "pending"
    assert client.get("/api/tasks").json() == []
    client.post(f"/api/actions/{step['action']['id']}/approve")
    assert [t["title"] for t in client.get("/api/tasks").json()] == ["Call mom"]


def test_destructive_tool_never_auto(client):
    from lifeos import memory, tools

    m = memory.add("I live in Berlin")
    client.patch("/api/settings", json={"tool_policies": {"delete_memory": "auto"}})
    out = tools.invoke("delete_memory", {"memory_id": m["id"]}, {})
    assert out["status"] == "pending"
    assert memory.get(m["id"])


def test_moves_never_overwrite(approved):
    from lifeos import files

    d = approved["downloads"]
    (d / "Finance").mkdir()
    (d / "Finance" / "invoice_march.pdf").write_text("existing")
    out = files.apply_moves([{"src": str(d / "invoice_march.pdf"), "dest": str(d / "Finance" / "invoice_march.pdf")}])
    assert out["moved"][0]["dest"].endswith("invoice_march (1).pdf")
    assert (d / "Finance" / "invoice_march.pdf").read_text() == "existing"
