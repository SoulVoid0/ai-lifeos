"""Model-driven planning, with Ollama replaced by a fake."""
from __future__ import annotations

import pytest


@pytest.fixture()
def fake_llm(monkeypatch, client):
    from lifeos import llm

    calls: list[dict] = []
    replies: list[dict] = []

    def chat_json(messages, schema, **kw):
        calls.append({"messages": messages, "schema": schema})
        return replies.pop(0)

    def chat_text(messages, **kw):
        calls.append({"messages": messages})
        return "You fixed it by setting PYTHONPATH (see notes.txt)."

    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": ["llama3.1:8b"], "url": "x"})
    monkeypatch.setattr(llm, "chat_json", chat_json)
    monkeypatch.setattr(llm, "chat_text", chat_text)
    monkeypatch.setattr(llm, "embed", lambda texts: None)
    return {"calls": calls, "replies": replies}


def test_model_plan_executes_and_filters(client, fake_llm, approved):
    fake_llm["replies"].append({
        "reply": "Setting up your study session.",
        "steps": [
            {"tool": "search_files", "args": {"query": "lecture notes"}},
            {"tool": "run_shell", "args": {"cmd": "rm -rf ~"}},
            {"tool": "create_task", "args": {"title": "Study linear algebra", "remind": "today 8pm"}},
        ],
        "needs_answer": False,
    })
    r = client.post("/api/chat", json={"message": "Prepare my workspace for studying"}).json()
    data = r["message"]["data"]
    assert data["planner"] == "llm"
    assert [s["tool"] for s in data["steps"]] == ["search_files", "create_task"]
    assert data["steps"][0]["result"]["files"][0]["name"] == "lecture5.pdf"
    assert r["message"]["content"].startswith("Setting up your study session.")
    system = fake_llm["calls"][0]["messages"][0]["content"]
    assert "Downloads" in system and "organize_files" in system


def test_rules_used_first_for_simple_requests(client, fake_llm):
    client.post("/api/chat", json={"message": "remind me to stretch in 2 hours"})
    assert fake_llm["calls"] == []


def test_model_answers_questions_from_results(client, fake_llm):
    fake_llm["replies"].append({"reply": "", "steps": [{"tool": "search_everything", "args": {"query": "pytest error"}}],
                                "needs_answer": True})
    client.patch("/api/settings", json={"planner": "llm"})
    r = client.post("/api/chat", json={"message": "How did I fix that pytest thing?"}).json()
    assert "PYTHONPATH" in r["message"]["content"]


def test_model_categorizes_documents(client, fake_llm, approved):
    fake_llm["replies"].append({"reply": "Here's a plan.", "steps": [{"tool": "organize_files", "args": {"folder": "Downloads",
                                                                                                         "extensions": ["pdf"]}}],
                                "needs_answer": False})
    fake_llm["replies"].append({"assignments": [{"id": 0, "category": "Bills"}, {"id": 1, "category": "Coding"},
                                                {"id": 2, "category": "University"}, {"id": 3, "category": "Misc"}]})
    client.patch("/api/settings", json={"planner": "llm"})
    r = client.post("/api/chat", json={"message": "tidy up my pdfs please"}).json()
    moves = r["message"]["data"]["steps"][0]["action"]["preview"]["moves"]
    assert {m["category"] for m in moves} == {"Bills", "Coding", "University", "Misc"}
    prompt = fake_llm["calls"][1]["messages"][0]["content"]
    assert "untrusted" in prompt


def test_model_failure_falls_back_to_rules(client, monkeypatch):
    from lifeos import llm

    def boom(*a, **k):
        raise llm.LLMUnavailable("Ollama request failed: connection refused")

    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": [], "url": "x"})
    monkeypatch.setattr(llm, "chat_json", boom)
    r = client.post("/api/chat", json={"message": "Find PDFs in Downloads and tell me a joke"}).json()
    data = r["message"]["data"]
    assert data["planner"] == "rules"
    assert data["steps"][0]["tool"] == "search_files"
    assert "unavailable" in r["message"]["content"]


def test_ollama_client_payloads(env, monkeypatch):
    """chat_json sends a schema-constrained, non-streaming request and parses the reply."""
    import httpx

    from lifeos import llm

    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent["url"], sent["json"] = url, json
        if url.endswith("/api/embed"):
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]] * len(json["input"])}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"message": {"content": '{"reply": "hi", "steps": [], "needs_answer": false}'}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.chat_json([{"role": "user", "content": "hi"}], {"type": "object"}, model="m")
    assert out["reply"] == "hi"
    assert sent["url"].endswith("/api/chat") and sent["json"]["format"] == {"type": "object"}
    assert sent["json"]["stream"] is False and sent["json"]["model"] == "m"

    monkeypatch.setattr(llm, "status", lambda force=False: {"ok": True, "models": [], "url": "x"})
    assert llm.embed(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    v = llm.unpack(llm.pack([0.5, -1.0]))
    assert v == [0.5, -1.0] and round(llm.cosine(v, v), 6) == 1.0


@pytest.mark.parametrize("first_chunk", [True, False])  # cut mid-answer, and while Ollama is still reading the prompt
def test_stopping_a_mission_cuts_the_ollama_request(env, monkeypatch, first_chunk):
    """Ollama keeps the GPU busy until a request finishes or its connection drops, so Stop must drop it."""
    import http.server
    import json as _json
    import threading
    import time

    from lifeos import llm, settings

    disconnected = threading.Event()

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            try:
                if first_chunk:
                    self.wfile.write(_json.dumps({"message": {"content": "Hel"}, "done": False}).encode() + b"\n")
                    self.wfile.flush()
                for _ in range(100):  # "generating" for 10 s
                    time.sleep(0.1)
                    self.wfile.write(b"\n")
                    self.wfile.flush()
            except OSError:
                disconnected.set()

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings.update({"ollama_url": f"http://127.0.0.1:{server.server_port}"})
    stop = threading.Event()
    monkeypatch.setattr(llm, "abort_for", lambda mid: stop)
    out: dict = {}

    def run():
        try:
            with llm.track(4242, "coder"):
                llm.chat_text([{"role": "user", "content": "hi"}], model="m")
        except Exception as exc:  # noqa: BLE001
            out["exc"] = exc

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.6)
    started = time.monotonic()
    stop.set()
    t.join(5)
    assert not t.is_alive() and isinstance(out.get("exc"), llm.Aborted)
    assert time.monotonic() - started < 2
    assert disconnected.wait(3)
    server.shutdown()


def test_mission_requests_stream_and_collect_the_answer(env, monkeypatch):
    import http.server
    import json as _json
    import threading

    from lifeos import llm, settings

    seen = {}

    class Ok(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["body"] = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            for c in ({"message": {"content": "Hel"}, "done": False},
                      {"message": {"content": "lo"}, "done": True, "prompt_eval_count": 7, "eval_count": 2}):
                self.wfile.write(_json.dumps(c).encode() + b"\n")

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings.update({"ollama_url": f"http://127.0.0.1:{server.server_port}"})
    monkeypatch.setattr(llm, "abort_for", lambda mid: threading.Event())
    with llm.track(None, "coder"):  # no mission: a plain request (no stream)
        pass
    with llm.track(7, "coder"):
        assert llm.chat_text([{"role": "user", "content": "hi"}], model="m") == "Hello"
    assert seen["body"]["stream"] is True and seen["body"]["options"]["num_ctx"] == 16384
    server.shutdown()
