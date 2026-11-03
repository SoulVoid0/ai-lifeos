"""Low-resource mode: thinking off, Ollama tuning, small models beside big ones, in-app downloads, the Spark agent."""
from __future__ import annotations

import http.server
import json
import threading

import pytest

GB = 1024 ** 3


class FakeOllama:
    """A tiny Ollama: /api/tags, /api/show, /api/ps, /api/chat, /api/generate, /api/pull, /api/delete."""

    def __init__(self) -> None:
        self.models = {"qwen3:14b": 9_300_000_000, "qwen3:4b-instruct-2507-q4_K_M": 2_500_000_000,
                       "llama3.1:8b": 4_900_000_000}
        self.thinking = {"qwen3:14b"}
        self.loaded: list[str] = []
        self.chats: list[dict] = []
        self.replies: list[tuple[int, dict]] = []  # queued (status, body) for /api/chat
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: dict | list | None = None, lines: list[dict] | None = None):
                self.send_response(code)
                self.end_headers()
                if lines is not None:
                    for line in lines:
                        self.wfile.write(json.dumps(line).encode() + b"\n")
                else:
                    self.wfile.write(json.dumps(body or {}).encode())

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": n, "model": n, "size": s} for n, s in fake.models.items()]})
                elif self.path == "/api/ps":
                    self._send(200, {"models": [{"name": n, "size": fake.models.get(n, 0), "size_vram": fake.models.get(n, 0)}
                                                for n in fake.loaded]})
                else:
                    self._send(404)

            def do_DELETE(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.models.pop(body["model"], None)
                self._send(200)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show":
                    caps = ["completion", "tools"] + (["thinking"] if body["model"] in fake.thinking else [])
                    self._send(200, {"capabilities": caps, "model_info": {
                        "general.architecture": "qwen3", "qwen3.block_count": 40, "qwen3.attention.head_count_kv": 8,
                        "qwen3.attention.key_length": 128}})
                elif self.path == "/api/generate":
                    if body.get("keep_alive") == 0 and body["model"] in fake.loaded:
                        fake.loaded.remove(body["model"])
                    self._send(200)
                elif self.path == "/api/pull":
                    fake.models[body["model"]] = 1_000_000_000
                    self._send(200, lines=[{"status": "pulling", "total": 10, "completed": 5},
                                           {"status": "success"}])
                elif self.path == "/api/chat":
                    fake.chats.append(body)
                    if body["model"] not in fake.loaded:
                        fake.loaded.append(body["model"])
                    code, reply = fake.replies.pop(0) if fake.replies else (200, {"message": {"content": '{"ok": "yes"}'}})
                    if body.get("stream") and code == 200:
                        self._send(200, lines=[{**reply, "done": True}])
                    else:
                        self._send(code, reply)
                else:
                    self._send(404)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


@pytest.fixture()
def ollama(env):
    from lifeos import llm, settings

    fake = FakeOllama()
    settings.update({"ollama_url": fake.url, "chat_model": "qwen3:14b"})
    llm._status_cache.update(checked=0.0, url=None)
    llm._info_cache.clear()
    llm._ps_cache.update(checked=0.0, models=[])
    for s in (llm._residents, llm._no_think_param, llm._think_breaks_json):
        s.clear()
    llm._gate._model = None
    yield fake
    fake.server.shutdown()


def test_thinking_is_turned_off_only_for_thinking_models(ollama):
    from lifeos import llm

    llm.chat_json([{"role": "user", "content": "hi"}], {"type": "object"}, model="qwen3:14b")
    llm.chat_text([{"role": "user", "content": "hi"}], model="llama3.1:8b")
    assert ollama.chats[0]["think"] is False
    assert "think" not in ollama.chats[1]


def test_thinking_left_on_when_the_user_wants_it(ollama):
    from lifeos import llm, settings

    settings.update({"ollama_think": True})
    llm.chat_text([{"role": "user", "content": "hi"}], model="qwen3:14b")
    assert "think" not in ollama.chats[0]


def test_old_ollama_that_rejects_think_gets_it_dropped(ollama):
    from lifeos import llm

    ollama.replies.append((400, {"error": 'invalid option "think"'}))
    assert llm.chat_text([{"role": "user", "content": "hi"}], model="qwen3:14b") == '{"ok": "yes"}'
    assert ollama.chats[0]["think"] is False and "think" not in ollama.chats[1]
    llm.chat_text([{"role": "user", "content": "again"}], model="qwen3:14b")
    assert "think" not in ollama.chats[2]  # remembered


def test_schema_ignored_without_thinking_falls_back_to_thinking(ollama):
    from lifeos import llm

    ollama.replies.append((200, {"message": {"content": "Sure! Here you go: ok"}}))
    out = llm.chat_json([{"role": "user", "content": "hi"}], {"type": "object"}, model="qwen3:14b")
    assert out == {"ok": "yes"}
    assert ollama.chats[0]["think"] is False and "think" not in ollama.chats[1]


def test_quantized_cache_crash_falls_back_to_f16(ollama, monkeypatch):
    from lifeos import llm, services, settings

    restarts = []
    monkeypatch.setattr(services, "apply_tuning", lambda restart=True, force=False: restarts.append(force) or {"restarted": True})
    ollama.replies.append((500, {"error": "llama runner: V cache quantization requires flash_attn"}))
    assert llm.chat_text([{"role": "user", "content": "hi"}], model="qwen3:14b")
    assert settings.get("ollama_kv_cache") == "f16" and restarts == [True]
    assert "OLLAMA_KV_CACHE_TYPE" not in services.tuning_env()


def test_tuning_env_and_kv_aware_gpu_layers(ollama, monkeypatch):
    from lifeos import llm, services, settings

    env = services.tuning_env()
    assert env == {"OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "2", "OLLAMA_FLASH_ATTENTION": "1",
                   "OLLAMA_KV_CACHE_TYPE": "q8_0"}
    assert services.kv_cache_type() == "f16"  # not applied yet: Ollama still runs with its own settings
    services._save_applied(env)
    services._kv_cache.update(at=0.0)
    assert services.kv_cache_type() == "q8_0"
    (services.config.data_dir() / "ollama.log").write_text(
        f"{services.LOG_MARK} x {{}}\nwarning: flash attention enabled but not supported by gpu\n")
    services._kv_cache.update(at=0.0)
    assert services.kv_cache_type() == "f16"  # Ollama said no: the cache is still f16
    settings.update({"ollama_tuning": False})
    assert services.tuning_env() == {}
    f16 = llm.gpu_layers("qwen3:14b", 8.0, 32768, 2.0)
    q8 = llm.gpu_layers("qwen3:14b", 8.0, 32768, 1.0625)
    assert f16 is not None and q8 is not None and q8 > f16


def test_ollama_is_started_with_the_tuning(ollama, monkeypatch):
    from lifeos import services

    spawned = []
    up = iter([False, True])
    monkeypatch.setattr(services, "ollama_up", lambda: next(up, True))
    monkeypatch.setattr(services, "ollama_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(services, "_spawn", lambda name, args, cwd=None, extra_env=None: spawned.append(extra_env))
    assert services.ensure_ollama(wait=3)
    assert spawned[0]["OLLAMA_KV_CACHE_TYPE"] == "q8_0" and services.applied_env() == spawned[0]
    assert services.tuning_status()["pending_restart"] is False


def test_fast_model_runs_beside_the_main_one(ollama):
    from lifeos import llm, settings

    settings.update({"ollama_gpu_gb": 13.5, "fast_model": "qwen3:4b-instruct-2507-q4_K_M"})
    llm.chat_text([{"role": "user", "content": "plan"}], model="qwen3:14b")
    llm.chat_text([{"role": "user", "content": "chat"}], model="qwen3:4b-instruct-2507-q4_K_M")
    assert set(ollama.loaded) == {"qwen3:14b", "qwen3:4b-instruct-2507-q4_K_M"}  # nothing was unloaded
    assert llm._gate.current() == "qwen3:14b" and "qwen3:4b-instruct-2507-q4_K_M" in llm._residents
    assert ollama.chats[1]["options"]["num_ctx"] == llm.FAST_MODEL_CTX
    llm._ps_cache.update(checked=0.0)
    assert llm.light_model("qwen3:14b") == "qwen3:4b-instruct-2507-q4_K_M"

    # A model too big to share the GPU with it pushes the small one out first.
    ollama.models["big-coder:30b"] = 12_800_000_000
    llm.chat_text([{"role": "user", "content": "code"}], model="big-coder:30b")
    assert ollama.loaded == ["big-coder:30b"]
    llm._ps_cache.update(checked=0.0)
    settings.update({"stay_on_loaded_model": True})
    assert llm.light_model("qwen3:14b") == "big-coder:30b"  # chat uses what's loaded instead of swapping
    assert llm.loaded_or("qwen3:14b") == "big-coder:30b"
    settings.update({"stay_on_loaded_model": False})
    assert llm.loaded_or("qwen3:14b") == "qwen3:14b"


def test_compact_trims_in_one_go():
    from lifeos import missions

    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    msgs += [{"role": "user", "content": "x" * 3000} for _ in range(30)]
    missions._compact(msgs)
    total = sum(len(m["content"]) for m in msgs)
    limit = int(16384 * 3 * 0.7)
    assert total <= int(limit * 0.6) + missions.KEEP_RECENT * 3000
    before = [m["content"] for m in msgs]
    msgs.append({"role": "user", "content": "y" * 1000})
    missions._compact(msgs)  # under the limit again: the prefix (and Ollama's cache of it) stays as it was
    assert [m["content"] for m in msgs[:-1]] == before


def test_low_resource_mode_applies_and_downloads(ollama, client, monkeypatch):
    from lifeos import crew, lean, services, settings

    monkeypatch.setattr(services, "apply_tuning", lambda restart=True, force=False: {"restarted": True})
    r = client.post("/api/system/lean", json={"download": True}).json()
    assert r["downloading"] == [crew.LEAN_CODER]
    cfg = settings.get_all()
    assert cfg["ollama_think"] is False and cfg["fast_model"] == crew.FAST_MODEL and cfg["max_parallel_agents"] == 1
    assert cfg["stay_on_loaded_model"] and cfg["ollama_kv_cache"] == "q8_0"
    agents = {a["slug"]: a for a in crew.list_agents()}
    assert agents["research"]["model"] == "qwen3:14b" and agents["coder"]["model"] == crew.LEAN_CODER
    assert agents["quick"]["name"] == "Spark" and agents["quick"]["max_steps"] == 4
    for _ in range(50):
        if lean.pulls() and lean.pulls()[0]["state"] != "pulling":
            break
        threading.Event().wait(0.05)
    assert lean.pulls()[0]["state"] == "done"
    st = client.get("/api/system/lean").json()
    assert st["on"] and all(p["installed"] for p in st["plan"])
    models = client.get("/api/system/models").json()["models"]
    assert any(m["name"] == "qwen3:14b" and m["in_use"] for m in models)
    assert client.delete("/api/system/models/qwen3:14b").status_code == 400  # still assigned
    assert client.delete("/api/system/models/llama3.1:8b").json() == {"removed": "llama3.1:8b"}


def test_planner_is_told_about_the_micro_agent(ollama, monkeypatch):
    from lifeos import missions

    plans = []

    def fake_chat_json(messages, schema, model=None, timeout=180):
        plans.append(messages[0]["content"])
        return {"thinking": "simple", "reply": "hi", "tasks": []}

    monkeypatch.setattr(missions.llm, "chat_json", fake_chat_json)
    m = missions.start("what's on today?")
    missions.wait(m["id"], timeout=20)
    assert "Spark" in plans[0] and "micro agent" in plans[0]
