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


def test_muse_generates_through_tool(client, comfy):
    from lifeos import tools

    muse = next(a for a in client.get("/api/crew/agents").json() if a["slug"] == "creative")
    assert muse["name"] == "Muse" and "generate_image" in muse["tools"]
    out = tools.invoke("generate_image", {"prompt": "a cozy cabin in snow", "size": "null", "seed": "never"},
                       {"mission_id": None})
    assert out["status"] == "done", out
    media_id = out["result"]["media_id"]
    assert client.get(f"/api/media/{media_id}").json()["status"] == "done"
    listed = tools.invoke("list_media", {}, {})
    assert listed["status"] == "done" and listed["result"]["media"][0]["id"] == media_id
    activity = client.get("/api/crew/agents/creative/activity").json()
    assert activity["agent"]["slug"] == "creative" and isinstance(activity["log"], list)


def test_muse_added_on_upgrade(env):
    from lifeos import crew, db

    crew.ensure_seeded()
    with db.session() as conn:
        conn.execute("DELETE FROM agents WHERE slug = 'creative'")
        conn.execute("UPDATE settings SET value = '2' WHERE key = 'crew_defaults_version'")
    assert "creative" in [a["slug"] for a in crew.list_agents()]
