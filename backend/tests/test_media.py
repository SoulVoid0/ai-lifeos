"""Image/video generation: ComfyUI, Automatic1111 and the opt-in cloud path, plus the Muse agent and gallery API."""
from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


class FakeComfy:
    """Just enough of ComfyUI's HTTP API: /object_info, /prompt, /history and /view."""

    def __init__(self, checkpoints=("sd_xl_base_1.0.safetensors", "ltx-video-2b-v0.9.5.safetensors"), fail=False):
        self.checkpoints = list(checkpoints)
        self.fail = fail
        self.workflows: list[dict] = []

    def get(self, url, params=None, timeout=None, **_):
        req = httpx.Request("GET", url)
        if url.endswith("/object_info/CheckpointLoaderSimple"):
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {
                "ckpt_name": [self.checkpoints]}}}}, request=req)
        if "/history/" in url:
            if self.fail:
                return httpx.Response(200, json={"p1": {"status": {"status_str": "error", "messages": [
                    ["execution_error", {"exception_message": "out of memory"}]]}, "outputs": {}}}, request=req)
            video = any(n["class_type"] == "SaveAnimatedWEBP" for n in self.workflows[-1].values())
            key, name = ("images", "out.webp") if video else ("images", "out.png")
            return httpx.Response(200, json={"p1": {"status": {"status_str": "success", "completed": True},
                                                    "outputs": {"9": {key: [{"filename": name, "subfolder": "",
                                                                             "type": "output"}]}}}}, request=req)
        if url.endswith("/view"):
            return httpx.Response(200, content=PNG, request=req)
        if url.endswith("/api/ps"):
            raise httpx.ConnectError("no ollama", request=req)
        return httpx.Response(404, request=req)

    def post(self, url, json=None, timeout=None, **_):
        req = httpx.Request("POST", url)
        if url.endswith("/prompt"):
            self.workflows.append(json["prompt"])
            return httpx.Response(200, json={"prompt_id": "p1"}, request=req)
        raise httpx.ConnectError("unexpected", request=req)


@pytest.fixture()
def comfy(monkeypatch, env):
    from lifeos import media

    fake = FakeComfy()
    monkeypatch.setattr(media.httpx, "get", fake.get)
    monkeypatch.setattr(media.httpx, "post", fake.post)
    return fake


def wait_done(client, media_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        m = client.get(f"/api/media/{media_id}").json()
        if m["status"] in ("done", "failed"):
            return m
        time.sleep(0.05)
    raise AssertionError("media job did not finish")


def test_comfy_image_end_to_end(client, comfy):
    status = client.get("/api/media/status").json()
    assert status["ok"] and status["video"] and "sd_xl_base_1.0.safetensors" in status["checkpoints"]
    job = client.post("/api/media", json={"prompt": "a lighthouse at dusk, oil painting", "size": "landscape"}).json()
    m = wait_done(client, job["id"])
    assert m["status"] == "done", m
    assert m["model"] == "sd_xl_base_1.0.safetensors"  # the LTX checkpoint is never used for stills
    wf = comfy.workflows[-1]
    assert wf["4"]["inputs"]["ckpt_name"] == "sd_xl_base_1.0.safetensors"
    assert wf["6"]["inputs"]["text"] == "a lighthouse at dusk, oil painting"
    assert (wf["5"]["inputs"]["width"], wf["5"]["inputs"]["height"]) == (1216, 832)
    r = client.get(f"/api/media/{m['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert [x["id"] for x in client.get("/api/media").json()] == [m["id"]]
    client.delete(f"/api/media/{m['id']}")
    assert client.get("/api/media").json() == []


def test_comfy_video_uses_ltx_workflow(client, comfy):
    job = client.post("/api/media", json={"kind": "video", "prompt": "waves rolling onto a beach", "seconds": 2}).json()
    m = wait_done(client, job["id"])
    assert m["status"] == "done", m
    classes = {n["class_type"] for n in comfy.workflows[-1].values()}
    assert {"LTXVConditioning", "EmptyLTXVLatentVideo", "LTXVScheduler", "SaveAnimatedWEBP"} <= classes
    latent = next(n for n in comfy.workflows[-1].values() if n["class_type"] == "EmptyLTXVLatentVideo")
    assert latent["inputs"]["length"] == 49
    assert m["name"].endswith(".webp")


def test_comfy_error_is_reported(client, comfy):
    comfy.fail = True
    m = wait_done(client, client.post("/api/media", json={"prompt": "something big"}).json()["id"])
    assert m["status"] == "failed" and "out of memory" in m["error"]


def test_custom_workflow_placeholders(env, tmp_path):
    from lifeos import media

    wf = {"1": {"class_type": "X", "inputs": {"text": "{{prompt}}", "seed": "{{seed}}", "w": "{{width}}",
                                              "note": "seed={{seed}}"}}}
    out = media.fill_workflow(wf, {"prompt": "cat", "seed": 7, "width": 512})
    assert out["1"]["inputs"] == {"text": "cat", "seed": 7, "w": 512, "note": "seed=7"}
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(wf))
    assert media.update_cfg({"image_workflow": str(path)})["image_workflow"] == str(path)
    with pytest.raises(ValueError):
        media.update_cfg({"backend": "midjourney"})
    with pytest.raises(ValueError):
        media.update_cfg({"video_workflow": str(tmp_path / "missing.json")})


def test_cloud_images_need_opt_in(client, monkeypatch):
    from lifeos import media

    calls = []
    monkeypatch.setattr(media.httpx, "post", lambda *a, **k: calls.append(a) or None)
    client.patch("/api/media/settings", json={"backend": "openai"})
    assert client.get("/api/media/status").json()["ok"] is False
    m = wait_done(client, client.post("/api/media", json={"prompt": "a red fox"}).json()["id"])
    assert m["status"] == "failed" and calls == []
    assert client.post("/api/media", json={"kind": "video", "prompt": "a red fox"}).status_code == 400


def test_a1111_backend(client, monkeypatch):
    from lifeos import media

    def post(url, json=None, timeout=None, **_):
        assert url.endswith("/sdapi/v1/txt2img") and json["prompt"] == "mountain lake"
        return httpx.Response(200, json={"images": [base64.b64encode(PNG).decode()]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(media.httpx, "post", post)
    client.patch("/api/media/settings", json={"backend": "a1111"})
    m = wait_done(client, client.post("/api/media", json={"prompt": "mountain lake"}).json()["id"])
    assert m["status"] == "done" and media.file_for(m["id"])[0].read_bytes() == PNG


def test_lumen_generates_through_tool(client, comfy):
    from lifeos import tools

    crew = {a["slug"]: a for a in client.get("/api/crew/agents").json()}
    assert crew["photo"]["name"] == "Lumen" and crew["photo"]["tools"] == ["generate_image", "list_media"]
    assert crew["video"]["name"] == "Reel" and "generate_video" in crew["video"]["tools"]
    assert "generate_image" not in crew["creative"]["tools"]  # Muse directs, Lumen and Reel generate
    out = tools.invoke("generate_image", {"prompt": "a cozy cabin in snow", "size": "null", "seed": "never"},
                       {"mission_id": None})
    assert out["status"] == "done", out
    media_id = out["result"]["media_id"]
    assert client.get(f"/api/media/{media_id}").json()["status"] == "done"
    listed = tools.invoke("list_media", {}, {})
    assert listed["status"] == "done" and listed["result"]["media"][0]["id"] == media_id
    activity = client.get("/api/crew/agents/photo/activity").json()
    assert activity["agent"]["slug"] == "photo" and isinstance(activity["log"], list)


def test_offline_mission_routes_pictures_to_lumen_and_clips_to_reel(client, comfy):
    from lifeos import crew, rules

    assert crew.agent_for_tool("generate_image") == "photo" and crew.agent_for_tool("generate_video") == "video"
    p = rules.plan("make a 3 second video of waves on rocks")
    assert p.steps == [{"tool": "generate_video", "args": {"prompt": "waves on rocks", "seconds": 3}}]
    assert rules.plan("create a poster for my band").steps[0]["args"]["prompt"] == "poster for my band"
    m = client.post("/api/missions", json={"goal": "generate an image of a red fox in snow"}).json()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        cur = client.get(f"/api/missions/{m['id']}").json()
        if cur["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert cur["status"] == "done", cur
    assert [t["agent"] for t in cur["tasks"]] == ["photo"]
    items = client.get("/api/media", params={"agent": "photo"}).json()
    assert items and items[0]["prompt"] == "a red fox in snow" and items[0]["mission_id"] == m["id"]
    assert client.get("/api/media", params={"agent": "video"}).json() == []


def test_gpu_jobs_run_one_at_a_time(client, comfy, monkeypatch):
    import threading

    from lifeos import media

    running, peak, lock = [0], [0], threading.Lock()
    real = media._comfy

    def slow(*a, **k):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.15)
        with lock:
            running[0] -= 1
        return real(*a, **k)

    monkeypatch.setattr(media, "_comfy", slow)
    ids = [client.post("/api/media", json={"prompt": f"test image {i}"}).json()["id"] for i in range(3)]
    assert all(wait_done(client, i)["status"] == "done" for i in ids)
    assert peak[0] == 1


def test_muse_added_on_upgrade(env):
    from lifeos import crew, db

    crew.ensure_seeded()
    old = ["generate_image", "generate_video", "list_media", "search_memory", "save_to_outbox"]
    with db.session() as conn:
        conn.execute("DELETE FROM agents WHERE slug IN ('photo', 'video')")
        conn.execute("UPDATE agents SET tools = ?, voice = '', quirks = '', catchphrases = '[]' WHERE slug = 'creative'",
                     (json.dumps(old),))
        conn.execute("UPDATE agents SET voice = '', quirks = '', catchphrases = '[]', idle_lines = '[]' WHERE slug = 'files'")
        conn.execute("UPDATE agents SET quirks = 'my own quirk' WHERE slug = 'coder'")
        conn.execute("UPDATE settings SET value = '3' WHERE key = 'crew_defaults_version'")
    agents = {a["slug"]: a for a in crew.list_agents()}
    assert {"creative", "photo", "video"} <= set(agents)
    assert agents["creative"]["tools"] == ["list_media", "search_memory", "save_to_outbox"]
    assert agents["files"]["catchphrases"][0] == "Everything in its folder." and agents["files"]["idle_lines"]
    assert agents["coder"]["quirks"] == "my own quirk"  # edited personalities are kept


def test_personality_is_editable_and_used_in_chat(client, monkeypatch):
    from lifeos import llm

    r = client.patch("/api/crew/agents/photo", json={"voice": "Speaks like a pirate", "catchphrases": ["Arr!", "Arr!", " "],
                                                       "idle_lines": ["Ahoy"] * 20})
    a = r.json()
    assert a["voice"] == "Speaks like a pirate" and a["catchphrases"] == ["Arr!"] and a["idle_lines"] == ["Ahoy"]
    seen = {}
    monkeypatch.setattr(llm, "model_available", lambda ref=None: True)
    monkeypatch.setattr(llm, "chat_text", lambda msgs, **k: seen.update(msgs=msgs) or "Arr, a fine shot.")
    out = client.post("/api/crew/agents/photo/chat", json={"message": "hi"}).json()
    system = seen["msgs"][0]["content"]
    assert out["reply"] == "Arr, a fine shot." and "Speaks like a pirate" in system and "Arr!" in system
    assert "chat mode" in system
